"""Feature preparation for the real 400k-row labeled fraud dataset.

Loads ``feature_table_labeled.csv``, engineers the critical features from the
hackathon data dictionary (log amount, compound night/dormancy/geo signals),
drops IDs and label-leakage columns, one-hot encodes remaining low-cardinality
categoricals, and returns a fully numeric, null-free (X, y) pair.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ml.training.common import DEFAULT_LABELED_TABLE, MODELS_DIR
from ml.training.data_utils import LEAKAGE_RISK_COLS

FEATURE_COLUMNS_PATH = MODELS_DIR / "feature_columns.json"

# IDs, timestamps, high-cardinality strings, and label-leakage columns.
DROP_COLUMNS: tuple[str, ...] = (
    "txn_id",
    "account_id",
    "timestamp",
    "counterparty_id",
    "ip_address",
    "terminal_id",
    "session_id",
    "device_id",
    "notes",
    # Label leakage — only known after fraud confirmation.
    "fraud_type",
    "fraud_confidence",
    "confirmed_by",
    "fraud_date_confirmed",
    "recovery_status",
    "financial_loss_npr",
)

# POST-DECISION OTP columns. An OTP challenge only fires *after* the initial
# risk decision already flagged the transaction, so every otp_* column (and
# has_otp_log) leaks the decision — and hence the label — into the classifier.
# They are intentionally kept in the feature table for the SIM-swap escalation /
# OTP interlock logic, but must never be trained on. The Behavior Agent also
# zeroes these at inference (behavior_check.py), so training on them additionally
# introduces train/serve skew. LEAKAGE_RISK_COLS is the canonical set; the
# "otp_" prefix guard below also catches any future OTP-derived columns
# (e.g. one-hot expansions) before they can leak in.
LEAKAGE_COLUMNS: frozenset[str] = LEAKAGE_RISK_COLS

LABEL_COLUMN = "is_fraud"

NUMERIC_NULL_SENTINEL = -999.0
MAX_CATEGORICAL_CARDINALITY = 30


def _to_bool_int(series: pd.Series) -> pd.Series:
    """Coerce a possibly-object True/False column to 0/1 (nulls → 0)."""
    if series.dtype == bool:
        return series.astype("int8")
    mapped = series.map(
        {True: 1, False: 0, "True": 1, "False": 0, "true": 1, "false": 0, 1: 1, 0: 0}
    )
    return mapped.fillna(0).astype("int8")


def _is_boolean_like(series: pd.Series) -> bool:
    if series.dtype == bool:
        return True
    if series.dtype != object:
        return False
    values = set(series.dropna().unique().tolist())
    return bool(values) and values.issubset({True, False, "True", "False", "true", "false"})


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add the top-signal features from the data dictionary (idempotent)."""
    out = df

    amount = pd.to_numeric(out["amount_npr"], errors="coerce")
    z_score = pd.to_numeric(out["vel_z_score_amount"], errors="coerce").fillna(0)
    hour = pd.to_numeric(out["hour_of_day"], errors="coerce")

    is_night = _to_bool_int(out["is_night"])
    new_counterparty = _to_bool_int(out["vel_new_counterparty_flag"])
    is_dormant = _to_bool_int(out["cust_is_dormant"])
    structuring = _to_bool_int(out["is_structuring_amount"])
    impossible_travel = _to_bool_int(out["geo_impossible_travel"])
    is_vpn = _to_bool_int(out["geo_is_vpn"])

    out["log_amount_npr"] = np.log1p(amount.clip(lower=0)).fillna(0)
    out["amount_x_zscore"] = (amount.fillna(0) * z_score).astype(float)
    out["is_high_risk_hour"] = hour.isin([1, 2, 3, 4]).astype("int8")
    out["is_night_new_counterparty"] = (is_night & new_counterparty).astype("int8")
    out["is_dormant_high_zscore"] = (is_dormant & (z_score > 3)).astype("int8")
    out["is_structuring_night"] = (structuring & is_night).astype("int8")
    out["impossible_travel_vpn"] = (impossible_travel & is_vpn).astype("int8")

    count_1m = pd.to_numeric(out["vel_txn_count_1m"], errors="coerce").fillna(0)
    count_1h = pd.to_numeric(out["vel_txn_count_1h"], errors="coerce").fillna(0)
    out["vel_count_ratio_1m_1h"] = (count_1m / (count_1h + 1)).astype(float)

    return out


def load_labeled_table(feature_table_path: str | Path | None = None) -> pd.DataFrame:
    path = Path(feature_table_path) if feature_table_path else DEFAULT_LABELED_TABLE
    df = pd.read_csv(path, low_memory=False)
    return engineer_features(df)


def prepare_features(
    feature_table_path: str | Path | None = None,
    *,
    df: pd.DataFrame | None = None,
    verbose: bool = True,
    save_columns: bool = True,
) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """Return (X, y, feature_cols) — fully numeric, null-free, model-ready.

    Pass an already-loaded ``df`` (from :func:`load_labeled_table`) to reuse it
    instead of re-reading the labeled table from disk.
    """
    if df is None:
        df = load_labeled_table(feature_table_path)

    y = _to_bool_int(df[LABEL_COLUMN]).astype(int)

    # Drop IDs / label-leakage columns, the label, the post-decision OTP
    # columns, and anything with an ``otp_`` prefix (catches one-hot expansions).
    drop_cols = {*DROP_COLUMNS, LABEL_COLUMN, *LEAKAGE_COLUMNS}
    drop_cols.update(c for c in df.columns if c.startswith("otp_"))
    X = df.drop(columns=[c for c in drop_cols if c in df.columns])

    dropped_high_cardinality: list[str] = []
    onehot_frames: list[pd.DataFrame] = []

    for col in list(X.columns):
        series = X[col]
        if _is_boolean_like(series):
            X[col] = _to_bool_int(series)
        elif pd.api.types.is_numeric_dtype(series):
            X[col] = pd.to_numeric(series, errors="coerce").fillna(NUMERIC_NULL_SENTINEL)
        else:  # categorical string
            filled = series.fillna("UNKNOWN").astype(str)
            if filled.nunique() > MAX_CATEGORICAL_CARDINALITY:
                dropped_high_cardinality.append(col)
                X = X.drop(columns=[col])
                continue
            dummies = pd.get_dummies(filled, prefix=col, dtype="int8")
            onehot_frames.append(dummies)
            X = X.drop(columns=[col])

    if onehot_frames:
        X = pd.concat([X, *onehot_frames], axis=1)

    feature_cols = X.columns.tolist()

    if save_columns:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        FEATURE_COLUMNS_PATH.write_text(
            json.dumps({"feature_columns": feature_cols}, indent=2), encoding="utf-8"
        )

    if verbose:
        n_fraud = int(y.sum())
        n_legit = int(len(y) - n_fraud)
        print("=== Feature Preparation ===")
        print(f"Total rows:          {len(y):>9,}")
        print(f"Fraud rows:          {n_fraud:>9,} ({n_fraud / len(y):.2%})")
        print(f"Legitimate rows:     {n_legit:>9,} ({n_legit / len(y):.2%})")
        print(f"Feature columns:     {len(feature_cols):>9,}")
        print(f"scale_pos_weight:    {n_legit / max(n_fraud, 1):>9.1f}")
        print(f"Null values remaining: {int(X.isna().sum().sum()):>7,}")
        if dropped_high_cardinality:
            print(f"Dropped high-cardinality categoricals: {dropped_high_cardinality}")

    return X, y, feature_cols


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare model-ready features")
    parser.add_argument("--feature-table", type=Path, default=DEFAULT_LABELED_TABLE)
    args = parser.parse_args()
    prepare_features(args.feature_table)


if __name__ == "__main__":
    main()
