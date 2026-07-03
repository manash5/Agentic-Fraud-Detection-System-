"""Shared data loading helpers for offline model training."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FEATURE_TABLE = BACKEND_ROOT / "datasets_processed" / "feature_table.csv"
DEFAULT_MODELS_DIR = BACKEND_ROOT / "ml" / "models"
FEATURE_COLUMNS_PATH = DEFAULT_MODELS_DIR / "feature_columns.json"

# Columns excluded from the flat feature matrix (IDs, timestamps, free text, label metadata).
EXCLUDE_FROM_FEATURES: frozenset[str] = frozenset(
    {
        "txn_id",
        "timestamp",
        "account_id",
        "counterparty_id",
        "txn_type",
        "device_id",
        "ip_address",
        "merchant_category_code",
        "terminal_id",
        "session_id",
        "fraud_type",
        "fraud_confidence",
        "rule_baseline_decision",
        "rule_triggered",
        "cust_risk_tier",
        "cust_kyc_tier",
        "is_fraud",
    }
)

# POST-DECISION columns — do not use as model input. An OTP challenge fires
# only after the initial risk decision has already flagged the transaction,
# so these columns leak the decision (and hence the label) into the primary
# fraud classifier. They stay in the feature table for the SIM-swap
# escalation / OTP interlock logic, but must never feed the Behavior Agent
# models trained here.
LEAKAGE_RISK_COLS: frozenset[str] = frozenset(
    {
        "has_otp_log",
        "otp_trigger_reason",
        "otp_final_decision",
        "otp_sim_swap_suspected",
        "otp_failed",
    }
)

LSTM_SEQUENCE_FEATURES: tuple[str, ...] = (
    "amount_npr",
    "hour_of_day",
    "is_night",
    "amount_ratio",
    "vel_z_score_amount",
)


def load_feature_table(path: Path | None = None) -> pd.DataFrame:
    """Load the processed feature table."""
    table_path = path or DEFAULT_FEATURE_TABLE
    return pd.read_csv(table_path)


def derive_feature_columns(df: pd.DataFrame) -> list[str]:
    """Return numeric/boolean model feature column names.

    Post-decision OTP columns are excluded (``LEAKAGE_RISK_COLS``); the
    ``otp_`` prefix check also catches any future OTP-derived columns (e.g.
    one-hot expansions) before they can leak into training.
    """
    cols: list[str] = []
    for col in df.columns:
        if col in EXCLUDE_FROM_FEATURES or col in LEAKAGE_RISK_COLS:
            continue
        if col.startswith("otp_"):
            continue
        dtype = df[col].dtype
        if pd.api.types.is_bool_dtype(dtype):
            cols.append(col)
        elif pd.api.types.is_numeric_dtype(dtype):
            cols.append(col)
    return cols


def load_feature_columns(path: Path | None = None) -> list[str] | None:
    """Load persisted feature column list if available."""
    col_path = path or FEATURE_COLUMNS_PATH
    if not col_path.exists():
        return None
    with col_path.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload["feature_columns"]


def save_feature_columns(columns: list[str], path: Path | None = None) -> Path:
    """Persist the feature column list used by tree-based models."""
    col_path = path or FEATURE_COLUMNS_PATH
    col_path.parent.mkdir(parents=True, exist_ok=True)
    col_path.write_text(json.dumps({"feature_columns": columns}, indent=2), encoding="utf-8")
    return col_path


def prepare_xy(
    df: pd.DataFrame,
    feature_columns: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """Split a feature table into ``X``, ``y``, and the column list used."""
    if feature_columns is None:
        feature_columns = derive_feature_columns(df)

    missing = [c for c in feature_columns if c not in df.columns]
    if missing:
        raise KeyError(f"Feature columns missing from table: {missing}")

    X = df[feature_columns].copy()
    for col in X.columns:
        if pd.api.types.is_bool_dtype(X[col]):
            X[col] = X[col].astype(int)
    X = X.fillna(0)

    y = df["is_fraud"].astype(int)
    return X, y, feature_columns
