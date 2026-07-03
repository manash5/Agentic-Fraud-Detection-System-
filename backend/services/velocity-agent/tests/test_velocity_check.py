import sys
from pathlib import Path

import pytest


SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from app.velocity_check import TransactionNotFoundError, evaluate_velocity


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, snapshot=None, customer=None):
        self.snapshot = snapshot
        self.customer = customer

    def execute(self, statement, params):
        query = str(statement)
        if "FROM velocity_snapshots" in query:
            return FakeResult(self.snapshot)
        if "FROM customers" in query:
            return FakeResult(self.customer)
        raise AssertionError(f"Unexpected query: {query}")


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
        "weekend_flag": False,
        "night_flag": False,
        "unique_counterparties_1h": 1,
    }
    snapshot.update(overrides)
    return snapshot


def customer(avg_monthly_txn_count=60):
    return {
        "account_id": "ACC-1",
        "avg_monthly_txn_value_npr": 100_000,
        "avg_monthly_txn_count": avg_monthly_txn_count,
    }


def test_high_z_score_adds_high_risk():
    db = FakeConnection(
        snapshot=base_snapshot(z_score_amount=4.2),
        customer=customer(),
    )

    result = evaluate_velocity("TXN-1", db)

    assert result["risk_score"] >= 0.30
    assert result["breakdown"]["z_score_risk"] == 0.30
    assert result["confidence"] == 0.95


def test_txn_count_1m_spike_adds_high_risk():
    db = FakeConnection(
        snapshot=base_snapshot(txn_count_1m=3),
        customer=customer(avg_monthly_txn_count=120),
    )

    result = evaluate_velocity("TXN-1", db)

    assert result["risk_score"] >= 0.25
    assert result["breakdown"]["txn_count_risk"] == 0.25


def test_dormant_high_z_score_adds_high_risk_with_low_confidence():
    db = FakeConnection(
        snapshot=base_snapshot(dormancy_break=True, z_score_amount=3.2),
        customer=customer(),
    )

    result = evaluate_velocity("TXN-1", db)

    assert result["risk_score"] >= 0.25
    assert result["breakdown"]["dormancy_break_risk"] == 0.25
    assert result["confidence"] == 0.50


def test_normal_transaction_has_low_risk():
    db = FakeConnection(snapshot=base_snapshot(), customer=customer())

    result = evaluate_velocity("TXN-1", db)

    assert result["risk_score"] == 0.0
    assert all(value == 0.0 for value in result["breakdown"].values())


def test_missing_velocity_snapshot_row_raises_not_found():
    db = FakeConnection(snapshot=None, customer=customer())

    with pytest.raises(TransactionNotFoundError, match="Transaction not found"):
        evaluate_velocity("TXN-missing", db)
