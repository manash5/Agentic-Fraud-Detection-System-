from __future__ import annotations

import logging
import time
from typing import Any

from app.agents.redis_cache import get_velocity_data


logger = logging.getLogger("fraud-detection-pipeline.velocity")


class TransactionVelocityNotFoundError(Exception):
    """Raised when no velocity snapshot exists for a transaction."""


VELOCITY_DATA_FIELDS = (
    "z_score_amount",
    "txn_count_1m",
    "txn_count_1h",
    "txn_count_24h",
    "new_counterparty_flag",
    "dormancy_break",
    "unique_counterparties_1h",
    "avg_monthly_txn_count",
)


def evaluate_velocity(
    txn_id: str,
    account_id: str,
    redis_conn,
    db_connection,
) -> dict[str, Any]:
    started = time.perf_counter()
    snapshot = get_velocity_data(account_id, txn_id, redis_conn, db_connection)
    if snapshot is None:
        raise TransactionVelocityNotFoundError("Transaction velocity data not found")

    source = snapshot.pop("_source", "postgres_fallback")
    breakdown = _calculate_breakdown(snapshot)
    risk_score = min(round(sum(breakdown.values()), 4), 1.0)
    confidence = _calculate_confidence(snapshot)
    latency_ms = int((time.perf_counter() - started) * 1000)

    response = {
        "risk_score": risk_score,
        "confidence": confidence,
        "latency_ms": latency_ms,
        "source": source,
        "breakdown": breakdown,
        "velocity_data": _velocity_data_response(snapshot),
    }

    logger.debug(
        "DEBUG: Velocity eval txn_id=%s risk=%s confidence=%s source=%s latency_ms=%s",
        txn_id,
        risk_score,
        confidence,
        source,
        latency_ms,
    )
    return response


def _calculate_breakdown(snapshot: dict[str, Any]) -> dict[str, float]:
    z_score = _float_or_zero(snapshot.get("z_score_amount"))
    txn_count_1m = _int_or_zero(snapshot.get("txn_count_1m"))
    txn_count_1h = _int_or_zero(snapshot.get("txn_count_1h"))
    new_counterparty = _bool_value(snapshot.get("new_counterparty_flag"))
    dormancy_break = _bool_value(snapshot.get("dormancy_break"))
    unique_counterparties_1h = _int_or_zero(snapshot.get("unique_counterparties_1h"))
    night_flag = _bool_value(snapshot.get("vel_night_flag", snapshot.get("night_flag")))

    z_score_risk = 0.0
    if z_score > 5.0:
        z_score_risk = 0.40
    elif z_score > 3.5:
        z_score_risk = 0.30

    breakdown = {
        "z_score_risk": z_score_risk,
        "txn_count_1m_risk": 0.25 if txn_count_1m >= 3 else 0.0,
        "txn_count_1h_risk": 0.15 if txn_count_1h > 20 else 0.0,
        "new_counterparty_risk": 0.20 if new_counterparty else 0.0,
        "dormancy_break_risk": 0.25 if dormancy_break and z_score > 3.0 else 0.0,
        "unique_recipients_risk": 0.15 if unique_counterparties_1h >= 3 else 0.0,
        "night_activity_risk": 0.10 if night_flag and new_counterparty else 0.0,
    }
    return breakdown


def _calculate_confidence(snapshot: dict[str, Any]) -> float:
    avg_monthly_txn_count = _float_or_none(snapshot.get("avg_monthly_txn_count"))

    if _bool_value(snapshot.get("dormancy_break")):
        confidence = 0.60
    elif avg_monthly_txn_count is None or avg_monthly_txn_count < 20:
        confidence = 0.60
    elif avg_monthly_txn_count < 50:
        confidence = 0.80
    else:
        confidence = 0.95

    null_fields = sum(1 for field in VELOCITY_DATA_FIELDS if snapshot.get(field) is None)
    if snapshot.get("vel_night_flag", snapshot.get("night_flag")) is None:
        null_fields += 1
    if null_fields >= 2:
        confidence -= 0.15

    return round(max(confidence, 0.0), 2)


def _velocity_data_response(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "z_score_amount": _float_or_none(snapshot.get("z_score_amount")),
        "txn_count_1m": _int_or_none(snapshot.get("txn_count_1m")),
        "txn_count_1h": _int_or_none(snapshot.get("txn_count_1h")),
        "txn_count_24h": _int_or_none(snapshot.get("txn_count_24h")),
        "new_counterparty_flag": _bool_value(snapshot.get("new_counterparty_flag")),
        "dormancy_break": _bool_value(snapshot.get("dormancy_break")),
        "unique_counterparties_1h": _int_or_none(snapshot.get("unique_counterparties_1h")),
    }


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    return bool(value)


def _float_or_zero(value: Any) -> float:
    return _float_or_none(value) or 0.0


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _int_or_zero(value: Any) -> int:
    return _int_or_none(value) or 0


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
