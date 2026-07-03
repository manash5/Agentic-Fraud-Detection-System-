from __future__ import annotations

import json
import logging
import math
import time
import traceback
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import text

from app.model_loader import BehaviorModels
from shared.explainability.shap_utils import compute_shap_values


logger = logging.getLogger("behavior-agent")

SEQUENCE_WINDOW = 64
LSTM_SEQUENCE_FEATURES = (
    "amount_npr",
    "hour_of_day",
    "is_night",
    "amount_ratio",
    "vel_z_score_amount",
)
SHAP_TIMEOUT_SECONDS = 2.0


class TransactionNotFoundError(Exception):
    """Raised when a transaction cannot be found for behavior scoring."""


class ModelNotConfiguredError(Exception):
    """Raised when the Behavior Agent was not configured with loaded models."""


@dataclass
class FeatureVector:
    values: np.ndarray
    feature_names: list[str]
    feature_values: dict[str, float]
    null_count: int
    missing_feature_count: int
    raw: dict[str, Any]

    @property
    def is_incomplete(self) -> bool:
        if not self.feature_names:
            return True
        return self.missing_feature_count > 0 or self.null_count > max(len(self.feature_names) * 0.25, 5)

    @property
    def has_nulls(self) -> bool:
        return self.null_count > 0 or self.missing_feature_count > 0


_MODELS: BehaviorModels | None = None
_LSTM_MODEL: Any | None = None


def configure_models(models: BehaviorModels, lstm_model: Any | None = None) -> None:
    global _MODELS, _LSTM_MODEL
    _MODELS = models
    _LSTM_MODEL = lstm_model


def evaluate_behavior(txn_id: str, account_id: str, db_connection) -> dict[str, Any]:
    if _MODELS is None:
        raise ModelNotConfiguredError("Behavior models are not configured")

    feature_vector = build_feature_vector(txn_id, account_id, db_connection)
    transaction_count = _fetch_transaction_count(account_id, db_connection)
    is_warm_user = transaction_count >= 50

    xgb_score = _predict_xgboost(_MODELS.xgboost, feature_vector.values)
    isoforest_score = _predict_isolation_forest(_MODELS.isolation_forest, feature_vector.values)
    model_scores: dict[str, float | None] = {
        "xgboost": xgb_score,
        "isolation_forest": isoforest_score,
        "lstm": None,
    }
    models_used = ["xgboost", "isolation_forest"]
    lstm_failed = False

    if is_warm_user and _LSTM_MODEL is not None:
        try:
            lstm_score = _predict_lstm(account_id, db_connection)
            model_scores["lstm"] = lstm_score
            models_used.append("lstm")
            logger.debug("LSTM used for account_id=%s (50+ transactions)", account_id)
        except Exception:
            lstm_failed = True
            logger.error("LSTM prediction failed for account_id=%s:\n%s", account_id, traceback.format_exc())

    risk_score = _blend_model_scores(xgb_score, isoforest_score, model_scores["lstm"])
    shap_explanation = compute_shap_explanation(feature_vector, _MODELS.xgboost)
    is_dormant = _bool_value(feature_vector.raw.get("cust_is_dormant"))
    confidence = _calculate_confidence(
        has_lstm=model_scores["lstm"] is not None,
        feature_vector=feature_vector,
        is_dormant=is_dormant,
        lstm_failed=lstm_failed,
    )

    return {
        "txn_id": txn_id,
        "risk_score": risk_score,
        "confidence": confidence,
        "model_scores": model_scores,
        "models_used": models_used,
        "shap_explanation": shap_explanation,
        "user_profile": {
            "account_has_50plus_transactions": is_warm_user,
            "is_dormant": is_dormant,
            "transaction_count": transaction_count,
        },
    }


def build_feature_vector(txn_id: str, account_id: str, db_connection) -> FeatureVector:
    if _MODELS is None:
        raise ModelNotConfiguredError("Behavior models are not configured")

    row = _fetch_feature_row(txn_id, account_id, db_connection)
    if row is None:
        raise TransactionNotFoundError("Transaction not found")

    features = _engineer_features(row)
    values: list[float] = []
    null_count = 0
    missing_feature_count = 0
    feature_values: dict[str, float] = {}

    for feature_name in _MODELS.feature_columns:
        if feature_name not in features:
            missing_feature_count += 1
            value = 0.0
        else:
            raw_value = features[feature_name]
            if raw_value is None:
                null_count += 1
                value = 0.0
            else:
                value = _float_or_zero(raw_value)
        values.append(value)
        feature_values[feature_name] = value

    return FeatureVector(
        values=np.asarray(values, dtype=float),
        feature_names=list(_MODELS.feature_columns),
        feature_values=feature_values,
        null_count=null_count,
        missing_feature_count=missing_feature_count,
        raw=features,
    )


def compute_shap_explanation(
    feature_vector: FeatureVector,
    model: Any,
    *,
    timeout_seconds: float = SHAP_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    if model is None:
        return []

    started = time.perf_counter()
    try:
        shap_values = compute_shap_values(feature_vector.values, model, feature_vector.feature_names)
    except Exception:
        logger.warning("SHAP computation failed:\n%s", traceback.format_exc())
        return []

    elapsed = time.perf_counter() - started
    if elapsed > timeout_seconds:
        logger.warning("SHAP computation exceeded %ss timeout", timeout_seconds)
        return []

    ranked = sorted(
        zip(feature_vector.feature_names, shap_values, strict=True),
        key=lambda item: abs(float(item[1])),
        reverse=True,
    )[:5]
    return [
        {
            "feature": name,
            "shap_value": round(float(value), 6),
            "direction": "increases_fraud" if float(value) >= 0 else "decreases_fraud",
            "feature_value": feature_vector.feature_values.get(name, 0.0),
        }
        for name, value in ranked
    ]


def _fetch_feature_row(txn_id: str, account_id: str, db_connection) -> dict[str, Any] | None:
    result = db_connection.execute(
        text(
            """
            SELECT
                t.txn_id,
                COALESCE(t.account_id, :account_id) AS account_id,
                t.timestamp,
                t.counterparty_id,
                t.txn_type,
                t.amount_npr,
                t.currency,
                t.channel,
                t.device_id,
                t.ip_address::text AS ip_address,
                t.merchant_category_code,
                t.terminal_id,
                t.session_id,
                t.auth_method,
                t.response_code,
                t.processing_time_ms,
                t.is_international,
                t.fx_rate,
                t.notes,
                c.avg_monthly_txn_value_npr AS cust_avg_monthly_txn_value_npr,
                c.avg_monthly_txn_count AS cust_avg_monthly_txn_count,
                c.is_dormant AS cust_is_dormant,
                c.churn_risk_score AS cust_churn_risk_score,
                g.latitude AS geo_latitude,
                g.longitude AS geo_longitude,
                g.is_vpn AS geo_is_vpn,
                g.is_tor AS geo_is_tor,
                g.is_datacenter AS geo_is_datacenter,
                g.velocity_flag AS geo_velocity_flag,
                g.km_from_home_district AS geo_km_from_home_district,
                g.prev_txn_km AS geo_prev_txn_km,
                g.prev_txn_time_delta_min AS geo_prev_txn_time_delta_min,
                g.impossible_travel AS geo_impossible_travel,
                v.txn_count_1m AS vel_txn_count_1m,
                v.txn_count_5m AS vel_txn_count_5m,
                v.txn_count_15m AS vel_txn_count_15m,
                v.txn_count_1h AS vel_txn_count_1h,
                v.txn_count_24h AS vel_txn_count_24h,
                v.txn_count_7d AS vel_txn_count_7d,
                v.z_score_amount AS vel_z_score_amount,
                v.dormancy_break AS vel_dormancy_break,
                v.night_flag AS vel_night_flag,
                v.new_counterparty_flag AS vel_new_counterparty_flag,
                d.num_accounts_seen_on_device AS dev_num_accounts_seen_on_device,
                d.risk_signals AS dev_risk_signals
            FROM transactions t
            LEFT JOIN customers c ON c.account_id = t.account_id
            LEFT JOIN geo_events g ON g.txn_id = t.txn_id
            LEFT JOIN velocity_snapshots v ON v.txn_id = t.txn_id
            LEFT JOIN device_fingerprints d ON d.device_id = t.device_id
            WHERE t.txn_id = :txn_id
              AND (:account_id IS NULL OR t.account_id = :account_id)
            """
        ),
        {"txn_id": txn_id, "account_id": account_id},
    )
    return _row_to_dict(result.fetchone())


def _fetch_transaction_count(account_id: str, db_connection) -> int:
    result = db_connection.execute(
        text("SELECT COUNT(*) AS transaction_count FROM transactions WHERE account_id = :account_id"),
        {"account_id": account_id},
    )
    row = _row_to_dict(result.fetchone())
    return _int_or_zero(row.get("transaction_count") if row else None)


def _fetch_lstm_sequence_rows(account_id: str, db_connection) -> list[dict[str, Any]]:
    result = db_connection.execute(
        text(
            """
            SELECT
                t.amount_npr,
                t.timestamp,
                c.avg_monthly_txn_value_npr AS cust_avg_monthly_txn_value_npr,
                v.z_score_amount AS vel_z_score_amount
            FROM transactions t
            LEFT JOIN customers c ON c.account_id = t.account_id
            LEFT JOIN velocity_snapshots v ON v.txn_id = t.txn_id
            WHERE t.account_id = :account_id
            ORDER BY t.timestamp DESC
            LIMIT :limit
            """
        ),
        {"account_id": account_id, "limit": SEQUENCE_WINDOW},
    )
    rows = result.fetchall() if hasattr(result, "fetchall") else []
    return [_row_to_dict(row) or {} for row in reversed(rows)]


def _engineer_features(row: dict[str, Any]) -> dict[str, Any]:
    features = dict(row)
    timestamp = row.get("timestamp")
    hour = getattr(timestamp, "hour", None)
    day = getattr(timestamp, "weekday", lambda: None)()

    amount = _float_or_zero(row.get("amount_npr"))
    avg_amount = _float_or_zero(row.get("cust_avg_monthly_txn_value_npr"))

    features.update(
        {
            "has_device_id": row.get("device_id") is not None,
            "has_terminal_id": row.get("terminal_id") is not None,
            "has_session_id": row.get("session_id") is not None,
            "has_fx_rate": row.get("fx_rate") is not None,
            "has_notes": bool(row.get("notes")),
            "is_malformed_ip": _is_malformed_ip(row.get("ip_address")),
            "is_possible_duplicate": False,
            "hour_of_day": hour,
            "day_of_week": day,
            "is_weekend": day in (5, 6) if day is not None else None,
            "is_night": (hour >= 22 or hour < 5) if hour is not None else None,
            "type_encoded": _encode_txn_type(row.get("txn_type")),
            "amount_ratio": amount / avg_amount if avg_amount else 0.0,
            "is_structuring_amount": _is_structuring_amount(amount),
            "is_fraud_merchant": str(row.get("counterparty_id") or "") in {"MRC-042", "MERCH-FRAUD-001"},
            "geo_is_malformed_ip": _is_malformed_ip(row.get("ip_address")),
            "dev_risk_signal_count": _risk_signal_count(row.get("dev_risk_signals")),
            "has_otp_log": False,
            "rule_confidence": 0.0,
        }
    )

    _add_one_hot(features, "currency", row.get("currency"))
    _add_one_hot(features, "channel", row.get("channel"))
    _add_one_hot(features, "auth_method", row.get("auth_method"))
    _add_one_hot(features, "response_code", row.get("response_code"))
    return features


def _predict_xgboost(model: Any, features: np.ndarray) -> float:
    if model is None:
        raise ModelNotConfiguredError("XGBoost model is not loaded")
    proba = model.predict_proba(features.reshape(1, -1))[0][1]
    return _clip01(proba)


def _predict_isolation_forest(model: Any, features: np.ndarray) -> float:
    if model is None:
        raise ModelNotConfiguredError("Isolation Forest model is not loaded")
    anomaly_score = model.decision_function(features.reshape(1, -1))[0]
    return _clip01((float(anomaly_score) + 1.0) / 2.0)


def _predict_lstm(account_id: str, db_connection) -> float:
    if _LSTM_MODEL is None:
        raise ModelNotConfiguredError("LSTM model is not loaded")

    sequence = _build_lstm_sequence(account_id, db_connection)

    import torch

    tensor = torch.tensor(sequence, dtype=torch.float32).unsqueeze(0)
    _LSTM_MODEL.eval()
    with torch.no_grad():
        output = _LSTM_MODEL(tensor)
        score = torch.sigmoid(output).reshape(-1)[0].item()
    return _clip01(score)


def _build_lstm_sequence(account_id: str, db_connection) -> np.ndarray:
    rows = _fetch_lstm_sequence_rows(account_id, db_connection)
    vectors = [_lstm_row_to_vector(row) for row in rows]
    if len(vectors) >= SEQUENCE_WINDOW:
        return np.asarray(vectors[-SEQUENCE_WINDOW:], dtype=np.float32)

    pad_len = SEQUENCE_WINDOW - len(vectors)
    padding = np.zeros((pad_len, len(LSTM_SEQUENCE_FEATURES)), dtype=np.float32)
    if not vectors:
        return padding
    return np.vstack([padding, np.asarray(vectors, dtype=np.float32)])


def _lstm_row_to_vector(row: dict[str, Any]) -> list[float]:
    timestamp = row.get("timestamp")
    hour = getattr(timestamp, "hour", 0) or 0
    amount = _float_or_zero(row.get("amount_npr"))
    avg_amount = _float_or_zero(row.get("cust_avg_monthly_txn_value_npr"))
    return [
        amount,
        float(hour),
        1.0 if hour >= 22 or hour < 5 else 0.0,
        amount / avg_amount if avg_amount else 0.0,
        _float_or_zero(row.get("vel_z_score_amount")),
    ]


def _blend_model_scores(
    xgb_score: float,
    isoforest_score: float,
    lstm_score: float | None,
) -> float:
    if lstm_score is None:
        risk = 0.6 * xgb_score + 0.4 * isoforest_score
    else:
        risk = 0.4 * xgb_score + 0.3 * isoforest_score + 0.3 * lstm_score
    return round(min(risk, 1.0), 4)


def _calculate_confidence(
    *,
    has_lstm: bool,
    feature_vector: FeatureVector,
    is_dormant: bool,
    lstm_failed: bool,
) -> float:
    if feature_vector.is_incomplete:
        confidence = 0.40 if feature_vector.null_count > len(feature_vector.feature_names) * 0.35 else 0.50
    elif has_lstm and not feature_vector.has_nulls:
        confidence = 0.95
    elif has_lstm:
        confidence = 0.85
    else:
        confidence = 0.75

    if lstm_failed:
        confidence = min(confidence, 0.65)
    if is_dormant:
        confidence -= 0.10
    return round(max(confidence, 0.0), 2)


def _add_one_hot(features: dict[str, Any], prefix: str, value: Any) -> None:
    if value is None:
        return
    features[f"{prefix}_{str(value).strip()}"] = True


def _encode_txn_type(value: Any) -> int:
    known = {
        "ATM_WITHDRAWAL": 0,
        "BILL_PAYMENT": 1,
        "MERCHANT_PAYMENT": 2,
        "P2P_TRANSFER": 3,
        "atm_withdrawal": 0,
        "bill_payment": 1,
        "merchant_payment": 2,
        "p2p_transfer": 3,
    }
    return known.get(str(value), 0)


def _is_structuring_amount(amount: float) -> bool:
    return any(abs(amount - threshold) <= 500 for threshold in (100_000, 500_000, 1_000_000))


def _is_malformed_ip(value: Any) -> bool:
    if not value:
        return True
    return str(value).count(".") != 3 and ":" not in str(value)


def _risk_signal_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    text_value = str(value).strip()
    if not text_value:
        return 0
    try:
        parsed = json.loads(text_value)
        if isinstance(parsed, list | dict):
            return len(parsed)
    except Exception:
        pass
    return len([item for item in text_value.split(",") if item.strip()])


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    return dict(row)


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    return bool(value)


def _float_or_zero(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return number


def _int_or_zero(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _clip01(value: Any) -> float:
    return min(max(float(value), 0.0), 1.0)
