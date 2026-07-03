import sys
from pathlib import Path

import pytest


SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from app.geo_check import TransactionNotFoundError, evaluate_geo


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row

    def single(self):
        return self.row


class FakeConnection:
    def __init__(self, geo_event=None, device=None, prior_device_seen_count=2):
        self.geo_event = geo_event
        self.device = device
        self.prior_device_seen_count = prior_device_seen_count

    def execute(self, statement, params):
        query = str(statement)
        if "FROM geo_events" in query:
            return FakeResult(self.geo_event)
        if "FROM device_fingerprints" in query:
            return FakeResult(self.device)
        if "FROM transactions" in query and "count(*) AS prior_seen_count" in query:
            return FakeResult({"prior_seen_count": self.prior_device_seen_count})
        raise AssertionError(f"Unexpected query: {query}")


class FakeNeo4jSession:
    def __init__(self, responses, should_fail=False):
        self.responses = responses
        self.should_fail = should_fail

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, statement, params):
        if self.should_fail:
            raise RuntimeError("Neo4j unavailable")

        query = getattr(statement, "text", None) or str(statement)
        if "shared_account_count" in query:
            return FakeResult({"shared_account_count": self.responses.get("shared_account_count", 0)})
        if "has_circular_flow" in query:
            return FakeResult({"has_circular_flow": self.responses.get("has_circular_flow", False)})
        if "shortestPath" in query:
            return FakeResult(self.responses.get("fraud_ring"))
        raise AssertionError(f"Unexpected Cypher query: {query}")


class FakeNeo4jDriver:
    def __init__(self, responses=None, should_fail=False):
        self.responses = responses or {}
        self.should_fail = should_fail

    def session(self):
        return FakeNeo4jSession(self.responses, self.should_fail)


def base_geo_event(**overrides):
    event = {
        "txn_id": "TXN-1",
        "account_id": "ACC-1",
        "device_id": "DEV-1",
        "ip_address": "203.0.113.10",
        "ip_country": "Nepal",
        "ip_city": "Kathmandu",
        "is_vpn": False,
        "is_tor": False,
        "is_datacenter": False,
        "km_from_home_district": 2.0,
        "prev_txn_km": 1.0,
        "prev_txn_time_delta_min": 60,
        "impossible_travel": False,
    }
    event.update(overrides)
    return event


def base_device(**overrides):
    device = {
        "device_id": "DEV-1",
        "locale": "ne_NP",
        "is_rooted_or_jailbroken": False,
        "vpn_detected": False,
        "tor_exit_node": False,
        "num_accounts_seen_on_device": 1,
        "is_shared_device": False,
        "risk_signals": {},
    }
    device.update(overrides)
    return device


def test_impossible_travel_adds_high_risk_and_confidence():
    db = FakeConnection(geo_event=base_geo_event(impossible_travel=True), device=base_device())

    result = evaluate_geo("TXN-1", "ACC-1", db, FakeNeo4jDriver())

    assert result["risk_score"] >= 0.50
    assert result["breakdown"]["impossible_travel_risk"] == 0.50
    assert result["confidence"] == 0.98


def test_rooted_en_us_device_on_nepal_ip_adds_high_risk():
    db = FakeConnection(
        geo_event=base_geo_event(ip_country="Nepal"),
        device=base_device(locale="en_US", is_rooted_or_jailbroken=True),
    )

    result = evaluate_geo("TXN-1", "ACC-1", db, FakeNeo4jDriver())

    assert result["risk_score"] >= 0.40
    assert result["breakdown"]["rooted_locale_mismatch_risk"] == 0.40


def test_account_near_comm042_fraud_ring_adds_high_risk():
    db = FakeConnection(
        geo_event=base_geo_event(account_id="ACC-COMM042-001"),
        device=base_device(),
    )
    graph = FakeNeo4jDriver(
        {
            "fraud_ring": {
                "fraud_node": "ACC-0011204",
                "distance": 1,
            }
        }
    )

    result = evaluate_geo("TXN-1", "ACC-COMM042-001", db, graph)

    assert result["risk_score"] >= 0.35
    assert result["breakdown"]["fraud_ring_proximity_risk"] == 0.35
    assert result["fraud_ring_details"]["is_near_fraud_seed"] is True
    assert result["fraud_ring_details"]["nearest_fraud_node_id"] == "ACC-0011204"


def test_normal_transaction_from_home_district_has_low_risk():
    db = FakeConnection(geo_event=base_geo_event(), device=base_device())

    result = evaluate_geo("TXN-1", "ACC-1", db, FakeNeo4jDriver())

    assert result["risk_score"] == 0.0
    assert all(value == 0.0 for value in result["breakdown"].values())
    assert result["confidence"] == 0.95


def test_missing_device_fingerprint_uses_fallback_and_lower_confidence():
    db = FakeConnection(geo_event=base_geo_event(), device=None)

    result = evaluate_geo("TXN-1", "ACC-1", db, FakeNeo4jDriver())

    assert result["breakdown"]["new_device_risk"] == 0.10
    assert result["risk_score"] == 0.10
    assert result["confidence"] == 0.75


def test_neo4j_connection_failure_gracefully_degrades():
    db = FakeConnection(geo_event=base_geo_event(), device=base_device())

    result = evaluate_geo("TXN-1", "ACC-1", db, FakeNeo4jDriver(should_fail=True))

    assert result["risk_score"] == 0.0
    assert result["confidence"] == 0.60
    assert result["breakdown"]["shared_ip_risk"] == 0.0


def test_missing_geo_event_raises_not_found():
    db = FakeConnection(geo_event=None, device=base_device())

    with pytest.raises(TransactionNotFoundError, match="Transaction not found"):
        evaluate_geo("TXN-missing", "ACC-1", db, FakeNeo4jDriver())
