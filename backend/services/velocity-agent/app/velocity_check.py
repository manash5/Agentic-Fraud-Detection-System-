from __future__ import annotations

from typing import Any

from sqlalchemy import text


class TransactionNotFoundError(Exception):
    """Raised when no velocity snapshot exists for a transaction."""


SNAPSHOT_FIELDS = (
    "z_score_amount",
    "txn_count_1m",
    "txn_count_1h",
    "txn_count_24h",
    "new_counterparty_flag",
    "dormancy_break",
    "weekend_flag",
    "night_flag",
    "unique_counterparties_1h",
)


def evaluate_velocity(txn_id: str, db_connection) -> dict[str, Any]:
    snapshot = _fetch_velocity_snapshot(txn_id, db_connection)
    if snapshot is None:
        raise TransactionNotFoundError("Transaction not found")

    account_id = snapshot.get("account_id")
    customer = _fetch_customer(account_id, db_connection) if account_id else None

    breakdown = _calculate_breakdown(snapshot, customer)
    risk_score = min(round(sum(breakdown.values()), 4), 1.0)
    confidence = _calculate_confidence(snapshot, customer)

    return {
        "txn_id": txn_id,
        "risk_score": risk_score,
        "confidence": confidence,
        "breakdown": breakdown,
    }


def _fetch_velocity_snapshot(txn_id: str, db_connection) -> dict[str, Any] | None:
    result = db_connection.execute(
        text(
            """
            SELECT
                v.txn_id,
                COALESCE(v.account_id, t.account_id) AS account_id,
                v.z_score_amount,
                v.txn_count_1m,
                v.txn_count_1h,
                v.txn_count_24h,
                v.new_counterparty_flag,
                v.dormancy_break,
                v.weekend_flag,
                v.night_flag,
                v.unique_counterparties_1h,
                t.amount_npr,
                t.txn_type
            FROM velocity_snapshots v
            LEFT JOIN transactions t ON t.txn_id = v.txn_id
            WHERE v.txn_id = :txn_id
            """
        ),
        {"txn_id": txn_id},
    )
    return _row_to_dict(result.fetchone())


def _fetch_customer(account_id: str, db_connection) -> dict[str, Any] | None:
    result = db_connection.execute(
        text(
            """
            SELECT
                account_id,
                avg_monthly_txn_value_npr,
                avg_monthly_txn_count
            FROM customers
            WHERE account_id = :account_id
            """
        ),
        {"account_id": account_id},
    )
    return _row_to_dict(result.fetchone())


def _calculate_breakdown(
    snapshot: dict[str, Any],
    customer: dict[str, Any] | None,
) -> dict[str, float]:
    z_score = _float_or_zero(snapshot.get("z_score_amount"))
    txn_count_1m = _int_or_zero(snapshot.get("txn_count_1m"))
    txn_count_1h = _int_or_zero(snapshot.get("txn_count_1h"))
    avg_monthly_txn_count = _float_or_none(
        customer.get("avg_monthly_txn_count") if customer else None
    )

    z_score_risk = 0.0
    if z_score > 5.0:
        z_score_risk = 0.40
    elif z_score > 3.5:
        z_score_risk = 0.30

    txn_count_risk = 0.0
    if txn_count_1m >= 3:
        txn_count_risk += 0.25
    if avg_monthly_txn_count is not None and txn_count_1h > avg_monthly_txn_count / 24:
        txn_count_risk += 0.15
    txn_count_risk = min(txn_count_risk, 0.30)

    new_counterparty_risk = (
        0.20 if _bool_value(snapshot.get("new_counterparty_flag")) else 0.0
    )
    dormancy_break_risk = (
        0.25
        if _bool_value(snapshot.get("dormancy_break")) and z_score > 3.0
        else 0.0
    )
    weekend_night_risk = (
        0.10
        if _bool_value(snapshot.get("weekend_flag")) and _bool_value(snapshot.get("night_flag"))
        else 0.0
    )
    unique_recipients_risk = (
        0.15 if _int_or_zero(snapshot.get("unique_counterparties_1h")) >= 3 else 0.0
    )

    return {
        "z_score_risk": z_score_risk,
        "txn_count_risk": txn_count_risk,
        "new_counterparty_risk": new_counterparty_risk,
        "dormancy_break_risk": dormancy_break_risk,
        "weekend_night_risk": weekend_night_risk,
        "unique_recipients_risk": unique_recipients_risk,
    }


def _calculate_confidence(
    snapshot: dict[str, Any],
    customer: dict[str, Any] | None,
) -> float:
    avg_monthly_txn_count = _float_or_none(
        customer.get("avg_monthly_txn_count") if customer else None
    )

    if customer is None or avg_monthly_txn_count is None:
        confidence = 0.50
    elif _bool_value(snapshot.get("dormancy_break")) or avg_monthly_txn_count < 20:
        confidence = 0.50
    elif avg_monthly_txn_count < 50:
        confidence = 0.75
    else:
        confidence = 0.95

    null_fields = sum(1 for field in SNAPSHOT_FIELDS if snapshot.get(field) is None)
    if null_fields >= 2:
        confidence -= 0.10

    return round(max(confidence, 0.0), 2)


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
    return _float_or_none(value) or 0.0


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _int_or_zero(value: Any) -> int:
    if value is None:
        return 0
    return int(value)
