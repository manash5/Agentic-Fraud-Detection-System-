"""Assemble the model-ready feature table from the raw backend datasets.

The real dataset is ~2M transactions across several multi-hundred-MB CSVs, so
the transaction stream is processed in **chunks** (default 100k rows). Supporting
tables that comfortably fit in RAM (customers, devices, OTP logs) plus the
per-txn side tables (geo, velocity, baseline, labels) are loaded once, trimmed
to the columns we need, and indexed by their join key for fast per-chunk joins.

Feature chunks are written to disk incrementally so peak memory stays bounded
regardless of the total row count.
"""

from __future__ import annotations

import gc
import json
from collections.abc import Iterable
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from ml.features.clean_transactions import (
    FRAUD_MERCHANT_IDS,  # noqa: F401  (re-exported for callers/tests)
    clean_otp_logs,
    clean_transactions,
    compute_duplicate_txn_ids,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = BACKEND_ROOT / "datasets"
DEFAULT_CHUNKSIZE = 100_000

# --- Columns pulled from each side table (documented in README) ---------------
GEO_FEATURE_COLS: tuple[str, ...] = (
    "latitude",
    "longitude",
    "is_vpn",
    "is_tor",
    "is_datacenter",
    "velocity_flag",
    "km_from_home_district",
    "prev_txn_km",
    "prev_txn_time_delta_min",
    "impossible_travel",
    "ip_country",
)

VELOCITY_FEATURE_COLS: tuple[str, ...] = (
    "txn_count_1m",
    "txn_count_5m",
    "txn_count_15m",
    "txn_count_1h",
    "txn_count_24h",
    "txn_count_7d",
    "total_amount_1h_npr",
    "total_amount_24h_npr",
    "unique_counterparties_1h",
    "unique_counterparties_24h",
    "new_counterparty_flag",
    "z_score_amount",
    "dormancy_break",
    "night_flag",
)

CUSTOMER_FEATURE_COLS: tuple[str, ...] = (
    "risk_tier",
    "kyc_tier",
    "avg_monthly_txn_value_npr",
    "avg_monthly_txn_count",
    "is_dormant",
    "churn_risk_score",
)

DEVICE_COL_MAP: dict[str, str] = {
    "is_rooted_or_jailbroken": "dev_is_rooted",
    "vpn_detected": "dev_vpn_detected",
    "tor_exit_node": "dev_tor_exit_node",
    "biometric_enrolled": "dev_biometric_enrolled",
    "num_accounts_seen_on_device": "dev_num_accounts_on_device",
    "is_shared_device": "dev_is_shared",
    "locale": "dev_locale",
}

# Low-cardinality categoricals that get one-hot encoded with a *fixed* category
# list (so every chunk yields identical columns).
ONEHOT_TXN_COLS: tuple[str, ...] = ("txn_type", "currency", "channel", "auth_method")
ONEHOT_CUST_COLS: tuple[str, ...] = ("cust_kyc_tier", "cust_risk_tier")


# =============================================================================
# Lookup preparation
# =============================================================================
class _Lookups:
    """Preloaded, join-key-indexed side tables shared across chunks."""

    def __init__(self, root: Path) -> None:
        self.customers = self._load_customers(root)
        self.devices = self._load_devices(root)
        self.geo = self._load_indexed(
            root / "geo_events.csv",
            usecols=("txn_id", *GEO_FEATURE_COLS),
            prefix="geo_",
        )
        self.velocity = self._load_indexed(
            root / "velocity_snapshots.csv",
            usecols=("txn_id", *VELOCITY_FEATURE_COLS),
            prefix="vel_",
        )
        self.baseline = self._load_baseline(root)
        self.labels = self._load_labels(root)
        self.otp = self._load_otp(root)
        gc.collect()

    @staticmethod
    def _load_customers(root: Path) -> pd.DataFrame:
        cust = pd.read_csv(
            root / "customer_profiles.csv",
            usecols=["account_id", *CUSTOMER_FEATURE_COLS],
        )
        cust = cust.rename(columns={c: f"cust_{c}" for c in CUSTOMER_FEATURE_COLS})
        return cust.set_index("account_id")

    @staticmethod
    def _load_devices(root: Path) -> pd.DataFrame:
        with (root / "device_fingerprints.json").open(encoding="utf-8") as fh:
            records = json.load(fh)
        df = pd.DataFrame(records)
        df["dev_risk_signal_count"] = df["risk_signals"].apply(
            lambda x: len(x) if isinstance(x, list) else 0
        )
        keep = ["device_id", *DEVICE_COL_MAP.keys(), "dev_risk_signal_count"]
        df = df[keep].rename(columns=DEVICE_COL_MAP)
        return df.drop_duplicates("device_id").set_index("device_id")

    @staticmethod
    def _load_indexed(path: Path, usecols: Iterable[str], prefix: str) -> pd.DataFrame:
        usecols = list(usecols)
        parts: list[pd.DataFrame] = []
        for chunk in pd.read_csv(path, usecols=usecols, chunksize=DEFAULT_CHUNKSIZE):
            parts.append(chunk)
        df = pd.concat(parts, ignore_index=True)
        del parts
        rename = {c: f"{prefix}{c}" for c in usecols if c != "txn_id"}
        df = df.rename(columns=rename)
        return df.drop_duplicates("txn_id").set_index("txn_id")

    @staticmethod
    def _load_baseline(root: Path) -> pd.DataFrame:
        parts = [
            c
            for c in pd.read_csv(
                root / "rule_engine_baseline_predictions.csv",
                usecols=["txn_id", "baseline_decision", "rule_triggered", "confidence"],
                chunksize=DEFAULT_CHUNKSIZE,
            )
        ]
        df = pd.concat(parts, ignore_index=True)
        df = df.rename(
            columns={
                "baseline_decision": "rule_baseline_decision",
                "confidence": "rule_confidence",
            }
        )
        return df.drop_duplicates("txn_id").set_index("txn_id")

    @staticmethod
    def _load_labels(root: Path) -> pd.DataFrame:
        labels = pd.read_csv(
            root / "fraud_labels_train.csv",
            usecols=["txn_id", "is_fraud", "fraud_type", "fraud_confidence"],
        )
        return labels.drop_duplicates("txn_id").set_index("txn_id")

    @staticmethod
    def _load_otp(root: Path) -> pd.DataFrame:
        # POST-DECISION source: OTP events exist only for transactions the
        # initial risk decision already flagged. Joined for the SIM-swap /
        # OTP interlock logic — never as primary-model input (see
        # ml.training.data_utils.LEAKAGE_RISK_COLS).
        otp = clean_otp_logs(pd.read_csv(root / "otp_logs.csv"))
        otp = otp.groupby("txn_id", as_index=True).first()
        otp = otp[["trigger_reason", "final_decision", "sim_swap_suspected"]].rename(
            columns={
                "trigger_reason": "otp_trigger_reason",
                "final_decision": "otp_final_decision",
                "sim_swap_suspected": "otp_sim_swap_suspected",
            }
        )
        otp["has_otp_log"] = True
        return otp


# =============================================================================
# Category scan (for consistent one-hot columns across chunks)
# =============================================================================
def scan_categories(root: Path) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Discover the full category set for one-hot columns + txn_type encoding.

    Reads only the categorical columns (one narrow pass) so this stays cheap.
    """
    cats: dict[str, list[str]] = {}
    txn_cats: dict[str, set[str]] = {c: set() for c in ONEHOT_TXN_COLS}
    for chunk in pd.read_csv(
        root / "transactions_raw.csv",
        usecols=list(ONEHOT_TXN_COLS),
        chunksize=DEFAULT_CHUNKSIZE,
    ):
        for col in ONEHOT_TXN_COLS:
            txn_cats[col].update(chunk[col].dropna().unique().tolist())
    for col in ONEHOT_TXN_COLS:
        cats[col] = sorted(txn_cats[col])

    cust = pd.read_csv(
        root / "customer_profiles.csv", usecols=["kyc_tier", "risk_tier"]
    )
    cats["cust_kyc_tier"] = sorted(cust["kyc_tier"].dropna().unique().tolist())
    cats["cust_risk_tier"] = sorted(cust["risk_tier"].dropna().unique().tolist())

    txn_type_encoding = {t: i for i, t in enumerate(cats["txn_type"])}
    return cats, txn_type_encoding


def scan_duplicate_txn_ids(root: Path, chunksize: int = DEFAULT_CHUNKSIZE) -> frozenset[str]:
    """Flag near-duplicate txn_ids with one narrow pass over the full table.

    ``transactions_raw`` is not sorted by time or account, so members of a
    duplicate pair (same account + amount within 5s) routinely land in
    different processing chunks — per-chunk detection silently misses them.
    Like :func:`scan_categories`, this reads only the columns it needs, so the
    transient frame stays well inside the memory envelope already used by the
    side-table lookups; only the (small) flagged id-set is kept afterwards.
    """
    cols = ["txn_id", "account_id", "amount_npr", "timestamp"]
    parts = [
        chunk
        for chunk in pd.read_csv(
            root / "transactions_raw.csv", usecols=cols, chunksize=chunksize
        )
    ]
    narrow = pd.concat(parts, ignore_index=True)
    del parts
    dup_ids = compute_duplicate_txn_ids(narrow)
    del narrow
    gc.collect()
    return dup_ids


# =============================================================================
# Per-chunk feature engineering
# =============================================================================
def process_chunk(
    chunk: pd.DataFrame,
    lookups: _Lookups,
    categories: dict[str, list[str]],
    txn_type_encoding: dict[str, int],
    duplicate_txn_ids: frozenset[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Clean + engineer features for one transaction chunk.

    Parameters
    ----------
    duplicate_txn_ids:
        Output of :func:`scan_duplicate_txn_ids`; when provided, duplicate
        flags come from the global scan instead of chunk-local detection.

    Returns
    -------
    (features, cleaned_txn)
        The engineered feature frame and the cleaned raw-transaction frame
        (so callers can persist ``transactions_raw_cleaned.csv`` in one pass).
    """
    cleaned = clean_transactions(chunk, duplicate_txn_ids=duplicate_txn_ids)
    df = cleaned.copy()

    # --- temporal features (NPT; tz_suspect marks ATM rows whose hour may be
    # UTC-shifted — see the timezone note in clean_transactions) ---
    ts = df["timestamp"]
    df["hour_of_day"] = ts.dt.hour
    df["day_of_week"] = ts.dt.dayofweek
    df["is_weekend"] = df["day_of_week"].isin([5, 6])
    df["is_night"] = (df["hour_of_day"] >= 22) | (df["hour_of_day"] < 5)
    df["month"] = ts.dt.month

    # --- transaction-type integer encoding (mapping saved separately) ---
    df["type_encoded"] = df["txn_type"].map(txn_type_encoding).astype("Int64")

    # --- customer profile join (on account_id) + relative amount ratio ---
    df = df.merge(lookups.customers, on="account_id", how="left")
    denom = df["cust_avg_monthly_txn_value_npr"].replace(0, pd.NA)
    df["amount_ratio"] = (df["amount_npr"] / denom).fillna(0.0)

    # --- per-txn side tables (geo / velocity / baseline / labels / otp) ---
    df = df.set_index("txn_id")
    df = df.join(lookups.geo).join(lookups.velocity)
    df = df.join(lookups.baseline).join(lookups.labels).join(lookups.otp)
    df = df.reset_index()

    # --- device fingerprint join (on device_id) ---
    df = df.merge(lookups.devices, on="device_id", how="left")

    # --- cross-source derived flags ---
    df["geo_high_risk_country"] = df["geo_ip_country"].notna() & (
        df["geo_ip_country"] != "Nepal"
    )
    df["dev_locale_mismatch"] = (df["dev_locale"] == "en_US") & (
        df["geo_ip_country"] == "Nepal"
    )

    # --- documented hidden-pattern flags (explicit so the model need not infer) ---
    # Pattern 4 (~40x fraud lift): rooted/jailbroken device with an en_US
    # locale transacting from a Nepali IP. dev_locale_mismatch above captures
    # only the locale/IP half — without the rooted requirement it is a much
    # weaker signal, so this flag is separate.
    df["is_rooted_en_us_np_ip"] = (
        df["dev_is_rooted"].eq(True)
        & (df["dev_locale"] == "en_US")
        & (df["geo_ip_country"] == "Nepal")
    )
    # Pattern 5 (~8x lift): dormancy break immediately followed by an amount
    # far above the account's norm (z-score > 3).
    df["is_dormancy_large_transfer"] = df["vel_dormancy_break"].eq(True) & (
        df["vel_z_score_amount"] > 3
    )
    # Pattern 6 (~8.3x lift): beneficiary added <24h before a transfer. No
    # source table carries a beneficiary-add timestamp (geo_events only has
    # prev_txn_time_delta_min; account_graph_edges has is_first_transfer_to_target
    # but no add time), so the closest proxy is: first-ever transfer to this
    # counterparty AND account active within the last 24h. NOTE: in the current
    # drop prev_txn_time_delta_min is capped at 1440, so the time clause only
    # discriminates once future data carries uncapped deltas.
    df["is_new_beneficiary_recent"] = df["vel_new_counterparty_flag"].eq(True) & (
        df["geo_prev_txn_time_delta_min"] < 1440
    )

    # --- OTP columns: POST-DECISION, do not use as model input ---
    # An OTP challenge fires only after the initial risk decision has flagged
    # the transaction, so every otp_* column (and has_otp_log) leaks the
    # decision into any model trained on it. They are kept in the table for
    # the SIM-swap escalation / OTP interlock logic; training code must
    # exclude them via ml.training.data_utils.LEAKAGE_RISK_COLS.
    df["has_otp_log"] = df["has_otp_log"].eq(True)
    df["otp_sim_swap_suspected"] = df["otp_sim_swap_suspected"].eq(True)
    df["otp_final_decision"] = df["otp_final_decision"].fillna("NONE")
    df["otp_trigger_reason"] = df["otp_trigger_reason"].fillna("NONE")
    df["otp_failed"] = df["otp_final_decision"].isin(["BLOCKED", "ESCALATED"])

    # --- one-hot encode low-cardinality categoricals (fixed categories) ---
    df = _one_hot_fixed(df, categories)

    # --- label column last ---
    if "is_fraud" in df.columns:
        df["is_fraud"] = df.pop("is_fraud")

    return df, cleaned


def _one_hot_fixed(df: pd.DataFrame, categories: dict[str, list[str]]) -> pd.DataFrame:
    """One-hot encode using fixed category lists for column stability."""
    out = df
    for col in (*ONEHOT_TXN_COLS, *ONEHOT_CUST_COLS):
        if col not in out.columns:
            continue
        cat = pd.Categorical(out[col], categories=categories[col])
        dummies = pd.get_dummies(cat, prefix=col, dtype=bool)
        dummies.index = out.index
        out = pd.concat([out.drop(columns=[col]), dummies], axis=1)
    return out


# =============================================================================
# Streaming build
# =============================================================================
def build_feature_table(
    data_dir: Path | None = None,
    *,
    output_path: Path | None = None,
    labeled_output_path: Path | None = None,
    cleaned_txn_path: Path | None = None,
    chunksize: int = DEFAULT_CHUNKSIZE,
    progress: bool = True,
) -> tuple[dict[str, int], dict[str, object]]:
    """Build the feature table by streaming transactions in chunks.

    Writes ``feature_table.csv`` (all rows) and ``feature_table_labeled.csv``
    (rows with a known ``is_fraud`` label) incrementally.

    Returns
    -------
    (txn_type_encoding, summary)
        The txn_type integer mapping and a summary dict with row/fraud counts,
        feature count, and non-zero null counts.
    """
    root = data_dir or DEFAULT_DATA_DIR
    categories, txn_type_encoding = scan_categories(root)
    duplicate_txn_ids = scan_duplicate_txn_ids(root, chunksize=chunksize)
    lookups = _Lookups(root)

    total_rows = 0
    labeled_rows = 0
    fraud_rows = 0
    feature_columns: list[str] | None = None
    null_counts: pd.Series | None = None

    # Fresh output files (truncate any previous run).
    for path in (output_path, labeled_output_path, cleaned_txn_path):
        if path is not None and path.exists():
            path.unlink()

    reader = pd.read_csv(root / "transactions_raw.csv", chunksize=chunksize)
    iterator = tqdm(reader, desc="feature chunks", unit="chunk") if progress else reader

    for chunk in iterator:
        features, cleaned = process_chunk(
            chunk, lookups, categories, txn_type_encoding, duplicate_txn_ids
        )

        if feature_columns is None:
            feature_columns = features.columns.tolist()
            null_counts = pd.Series(0, index=feature_columns, dtype="int64")
        else:
            features = features.reindex(columns=feature_columns)

        # Accumulate stats.
        total_rows += len(features)
        null_counts = null_counts.add(features.isna().sum(), fill_value=0)
        if "is_fraud" in features.columns:
            labeled_mask = features["is_fraud"].notna()
            labeled_rows += int(labeled_mask.sum())
            fraud_rows += int(features.loc[labeled_mask, "is_fraud"].astype(bool).sum())

        # Stream to disk.
        header = total_rows == len(features)
        if output_path is not None:
            features.to_csv(output_path, mode="a", header=header, index=False)
        if labeled_output_path is not None and "is_fraud" in features.columns:
            labeled = features.loc[features["is_fraud"].notna()]
            if not labeled.empty:
                write_header = not labeled_output_path.exists()
                labeled.to_csv(
                    labeled_output_path, mode="a", header=write_header, index=False
                )
        if cleaned_txn_path is not None:
            cleaned.to_csv(
                cleaned_txn_path, mode="a", header=header, index=False
            )

        del features, cleaned, chunk
        gc.collect()

    summary = _build_summary(
        total_rows=total_rows,
        labeled_rows=labeled_rows,
        fraud_rows=fraud_rows,
        feature_columns=feature_columns or [],
        null_counts=null_counts,
    )
    _print_summary(summary)
    return txn_type_encoding, summary


def _build_summary(
    *,
    total_rows: int,
    labeled_rows: int,
    fraud_rows: int,
    feature_columns: list[str],
    null_counts: pd.Series | None,
) -> dict[str, object]:
    nonzero_nulls: dict[str, int] = {}
    if null_counts is not None:
        nz = null_counts[null_counts > 0].sort_values(ascending=False)
        nonzero_nulls = {str(k): int(v) for k, v in nz.items()}
    return {
        "total_rows": total_rows,
        "labeled_rows": labeled_rows,
        "fraud_rows": fraud_rows,
        "fraud_rate_labeled": (fraud_rows / labeled_rows) if labeled_rows else 0.0,
        "num_features": len(feature_columns),
        "feature_columns": feature_columns,
        "null_counts": nonzero_nulls,
    }


def _print_summary(summary: dict[str, object]) -> None:
    print("\n=== Feature Table Summary ===")
    print(f"Total rows:            {summary['total_rows']:>12,}")
    print(f"Labeled rows:          {summary['labeled_rows']:>12,}")
    rate = float(summary["fraud_rate_labeled"])  # type: ignore[arg-type]
    print(
        f"Fraud rows (labeled):  {summary['fraud_rows']:>12,} "
        f"({rate:.2%} of labeled)"
    )
    print(f"Features:              {summary['num_features']:>12,} columns")
    nulls = summary["null_counts"]
    print("Null counts (non-zero only):")
    if nulls:
        for col, count in nulls.items():  # type: ignore[union-attr]
            print(f"  {col}: {count:,}")
    else:
        print("  (none)")
