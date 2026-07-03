import json
import sys
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np


SERVICE_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = SERVICE_ROOT.parents[1]
sys.path.insert(0, str(SERVICE_ROOT))
sys.path.insert(0, str(BACKEND_ROOT))

from app import behavior_check
from app.behavior_check import build_feature_vector, compute_shap_explanation, configure_models, evaluate_behavior
from app.model_loader import BehaviorModels, load_models


FEATURE_COLUMNS = [
    "amount_npr",
    "has_device_id",
    "hour_of_day",
    "is_night",
    "cust_avg_monthly_txn_count",
    "amount_ratio",
    "geo_is_vpn",
    "vel_z_score_amount",
    "dev_num_accounts_seen_on_device",
    "currency_NPR",
]


class FakeXGBoost:
    def __init__(self, score=0.75):
        self.score = score

    def predict_proba(self, matrix):
        return np.asarray([[1.0 - self.score, self.score]])


class FakeIsolationForest:
    def __init__(self, decision_score=0.36):
        self.decision_score = decision_score

    def decision_function(self, matrix):
        return np.asarray([self.decision_score])


class FakeResult:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, feature_row=None, transaction_count=10, sequence_rows=None):
        self.feature_row = feature_row
        self.transaction_count = transaction_count
        self.sequence_rows = sequence_rows or []

    def execute(self, statement, params):
        query = str(statement)
        if "COUNT(*) AS transaction_count" in query:
            return FakeResult({"transaction_count": self.transaction_count})
        if "ORDER BY t.timestamp DESC" in query:
            return FakeResult(rows=self.sequence_rows)
        if "FROM transactions t" in query:
            return FakeResult(self.feature_row)
        raise AssertionError(f"Unexpected query: {query}")


def base_feature_row(**overrides):
    row = {
        "txn_id": "TXN-1",
        "account_id": "ACC-1",
        "timestamp": datetime(2026, 1, 1, 23, 30),
        "counterparty_id": "ACC-2",
        "txn_type": "P2P_TRANSFER",
        "amount_npr": 10_000,
        "currency": "NPR",
        "channel": "MOBILE_APP",
        "device_id": "DEV-1",
        "ip_address": "203.0.113.10",
        "merchant_category_code": None,
        "terminal_id": None,
        "session_id": "SES-1",
        "auth_method": "MPIN",
        "response_code": "0",
        "processing_time_ms": 120,
        "is_international": False,
        "fx_rate": None,
        "notes": None,
        "cust_avg_monthly_txn_value_npr": 100_000,
        "cust_avg_monthly_txn_count": 80,
        "cust_is_dormant": False,
        "cust_churn_risk_score": 0.1,
        "geo_latitude": 27.7,
        "geo_longitude": 85.3,
        "geo_is_vpn": False,
        "geo_is_tor": False,
        "geo_is_datacenter": False,
        "geo_velocity_flag": False,
        "geo_km_from_home_district": 2.0,
        "geo_prev_txn_km": 1.0,
        "geo_prev_txn_time_delta_min": 60,
        "geo_impossible_travel": False,
        "vel_txn_count_1m": 1,
        "vel_txn_count_5m": 1,
        "vel_txn_count_15m": 1,
        "vel_txn_count_1h": 1,
        "vel_txn_count_24h": 3,
        "vel_txn_count_7d": 10,
        "vel_z_score_amount": 0.3,
        "vel_dormancy_break": False,
        "vel_night_flag": True,
        "vel_new_counterparty_flag": False,
        "dev_num_accounts_seen_on_device": 1,
        "dev_risk_signals": "[]",
    }
    row.update(overrides)
    return row


def fake_models(xgb_score=0.75, iso_decision_score=0.36, feature_columns=None):
    return BehaviorModels(
        xgboost=FakeXGBoost(xgb_score),
        isolation_forest=FakeIsolationForest(iso_decision_score),
        lstm_model=object(),
        feature_columns=feature_columns or FEATURE_COLUMNS,
        loaded=True,
    )


def test_xgboost_loads_correctly(tmp_path):
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    joblib.dump(FakeXGBoost(), models_dir / "xgboost_model.pkl")
    (models_dir / "feature_columns.json").write_text(
        json.dumps({"feature_columns": FEATURE_COLUMNS}),
        encoding="utf-8",
    )

    models = load_models(models_dir)

    assert models.xgboost is not None
    assert models.feature_columns == FEATURE_COLUMNS


def test_isolation_forest_loads_correctly(tmp_path):
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    joblib.dump(FakeIsolationForest(), models_dir / "isolation_forest_model.pkl")
    (models_dir / "feature_columns.json").write_text(
        json.dumps({"feature_columns": FEATURE_COLUMNS}),
        encoding="utf-8",
    )

    models = load_models(models_dir)

    assert models.isolation_forest is not None


def test_feature_extraction_returns_correct_shape():
    configure_models(fake_models(), object())
    db = FakeConnection(feature_row=base_feature_row())

    vector = build_feature_vector("TXN-1", "ACC-1", db)

    assert vector.values.shape == (len(FEATURE_COLUMNS),)
    assert vector.feature_values["currency_NPR"] == 1.0
    assert vector.feature_values["hour_of_day"] == 23.0


def test_shap_explanation_generates_top_5_features(monkeypatch):
    configure_models(fake_models(), object())
    db = FakeConnection(feature_row=base_feature_row())
    vector = build_feature_vector("TXN-1", "ACC-1", db)
    monkeypatch.setattr(
        behavior_check,
        "compute_shap_values",
        lambda feature_vector, model, feature_names=None: np.asarray([0.1, -0.6, 0.3, 0.2, 0.5, 0.05, 0.4, -0.7, 0.01, 0.02]),
    )

    explanation = compute_shap_explanation(vector, FakeXGBoost())

    assert len(explanation) == 5
    assert explanation[0]["feature"] == "vel_z_score_amount"
    assert explanation[0]["direction"] == "decreases_fraud"


def test_cold_start_user_skips_lstm_and_gets_risk_score():
    configure_models(fake_models(xgb_score=0.7, iso_decision_score=0.2), object())
    db = FakeConnection(feature_row=base_feature_row(), transaction_count=12)

    result = evaluate_behavior("TXN-1", "ACC-1", db)

    assert result["model_scores"]["lstm"] is None
    assert result["models_used"] == ["xgboost", "isolation_forest"]
    assert result["risk_score"] == 0.66
    assert result["confidence"] == 0.75


def test_warm_user_includes_lstm_score_in_blend(monkeypatch):
    configure_models(fake_models(xgb_score=0.7, iso_decision_score=0.2), object())
    monkeypatch.setattr(behavior_check, "_predict_lstm", lambda account_id, db_connection: 0.9)
    db = FakeConnection(feature_row=base_feature_row(), transaction_count=87)

    result = evaluate_behavior("TXN-1", "ACC-1", db)

    assert result["model_scores"]["lstm"] == 0.9
    assert result["models_used"] == ["xgboost", "isolation_forest", "lstm"]
    assert result["risk_score"] == 0.73


def test_known_fraud_transaction_gets_high_risk(monkeypatch):
    configure_models(fake_models(xgb_score=0.95, iso_decision_score=0.8), object())
    monkeypatch.setattr(behavior_check, "_predict_lstm", lambda account_id, db_connection: 0.9)
    db = FakeConnection(
        feature_row=base_feature_row(
            amount_npr=500_000,
            counterparty_id="MRC-042",
            vel_z_score_amount=4.2,
            geo_is_vpn=True,
        ),
        transaction_count=100,
    )

    result = evaluate_behavior("TXN-FRAUD", "ACC-1", db)

    assert result["risk_score"] >= 0.9


def test_known_legitimate_transaction_gets_low_risk():
    configure_models(fake_models(xgb_score=0.05, iso_decision_score=-0.9), object())
    db = FakeConnection(feature_row=base_feature_row(amount_npr=500), transaction_count=10)

    result = evaluate_behavior("TXN-LEGIT", "ACC-1", db)

    assert result["risk_score"] <= 0.10
