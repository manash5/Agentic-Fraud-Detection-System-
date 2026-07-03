from __future__ import annotations

import json
import importlib
import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = BACKEND_ROOT / "services" / "fraud-detection-pipeline"


def _purge_app_modules():
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def velocity_module():
    _purge_app_modules()
    for path in (str(SERVICE_ROOT), str(BACKEND_ROOT)):
        while path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)

    module = importlib.import_module("app.agents.velocity_agent")
    yield module
    _purge_app_modules()


class FakeResult:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot
        self.queries = []

    def execute(self, statement, params=None):
        self.queries.append((str(statement), params))
        return FakeResult(row=self.snapshot)


class FakeRedis:
    def __init__(self, initial=None):
        self.store = initial or {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value
        return True


def base_snapshot(**overrides):
    snapshot = {
        "txn_id": "TXN-1",
        "account_id": "ACC-1",
        "z_score_amount": 0.5,
        "txn_count_1m": 1,
        "txn_count_1h": 1,
        "txn_count_24h": 4,
        "new_counterparty_flag": False,
        "dormancy_break": False,
        "unique_counterparties_1h": 1,
        "night_flag": False,
        "avg_monthly_txn_count": 60,
    }
    snapshot.update(overrides)
    return snapshot


def test_high_z_score_adds_z_score_risk_contribution(velocity_module):
    db = FakeConnection(snapshot=base_snapshot(z_score_amount=4.2))

    result = velocity_module.evaluate_velocity("TXN-1", "ACC-1", None, db)

    assert result["risk_score"] >= 0.30
    assert result["breakdown"]["z_score_risk"] == 0.30


def test_txn_count_1m_spike_adds_count_risk(velocity_module):
    db = FakeConnection(snapshot=base_snapshot(txn_count_1m=3))

    result = velocity_module.evaluate_velocity("TXN-1", "ACC-1", None, db)

    assert result["risk_score"] >= 0.25
    assert result["breakdown"]["txn_count_1m_risk"] == 0.25


def test_dormancy_break_with_high_z_score_adds_dormancy_risk(velocity_module):
    db = FakeConnection(snapshot=base_snapshot(dormancy_break=True, z_score_amount=3.2))

    result = velocity_module.evaluate_velocity("TXN-1", "ACC-1", None, db)

    assert result["risk_score"] >= 0.25
    assert result["breakdown"]["dormancy_break_risk"] == 0.25


def test_redis_hit_uses_redis_source_with_low_latency(velocity_module):
    snapshot = base_snapshot(z_score_amount=4.2)
    key = "velocity:ACC-1:TXN-1"
    redis_conn = FakeRedis(initial={key: json.dumps(snapshot)})
    db = FakeConnection(snapshot=None)

    result = velocity_module.evaluate_velocity("TXN-1", "ACC-1", redis_conn, db)

    assert result["source"] == "redis"
    assert result["latency_ms"] < 5
    assert db.queries == []


def test_redis_miss_falls_back_to_postgres_and_caches(velocity_module):
    snapshot = base_snapshot(new_counterparty_flag=True)
    redis_conn = FakeRedis()
    db = FakeConnection(snapshot=snapshot)

    result = velocity_module.evaluate_velocity("TXN-1", "ACC-1", redis_conn, db)

    assert result["source"] == "postgres_fallback"
    assert result["breakdown"]["new_counterparty_risk"] == 0.20
    assert "velocity:ACC-1:TXN-1" in redis_conn.store


@pytest.mark.parametrize(
    ("avg_monthly_txn_count", "expected_confidence"),
    [
        (50, 0.95),
        (10, 0.60),
    ],
)
def test_confidence_uses_monthly_transaction_history(
    velocity_module,
    avg_monthly_txn_count,
    expected_confidence,
):
    db = FakeConnection(
        snapshot=base_snapshot(avg_monthly_txn_count=avg_monthly_txn_count),
    )

    result = velocity_module.evaluate_velocity("TXN-1", "ACC-1", None, db)

    assert result["confidence"] == expected_confidence


def test_missing_velocity_snapshot_raises_not_found(velocity_module):
    db = FakeConnection(snapshot=None)

    with pytest.raises(
        velocity_module.TransactionVelocityNotFoundError,
        match="Transaction velocity data not found",
    ):
        velocity_module.evaluate_velocity("TXN-missing", "ACC-1", None, db)
