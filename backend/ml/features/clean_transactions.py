"""Cleaning rules for the raw transactions table.

Every rule below cites the data-quality issue it addresses from the project
data dictionary. Functions are written to be *chunk-safe*: they operate on an
arbitrary slice of ``transactions_raw`` so the caller can stream 2M rows through
in bounded memory (see :mod:`ml.features.build_features`).
"""

from __future__ import annotations

import pandas as pd

# Explicit malformed / non-geolocatable IPs called out in the data dictionary
# (~0.2% of rows). Loopback + null route.
MALFORMED_IPS: frozenset[str] = frozenset({"127.0.0.1", "0.0.0.0"})

# RFC-1918 private range that should never appear as a public txn IP.
_PRIVATE_IP_PREFIX = "10."

# Nepal-time timestamps mostly use `YYYY-MM-DD HH:MM:SS.mmm` (UTC+5:45); a
# minority (older ATM logs) drop the millisecond component.
_TIMESTAMP_FORMAT_MS = "%Y-%m-%d %H:%M:%S.%f"
_TIMESTAMP_FORMAT_SEC = "%Y-%m-%d %H:%M:%S"

# Nepal Standard Time offset from UTC. The data dictionary reports ~0.9% of ATM
# records logged in UTC instead of NPT; apply this offset to normalise such
# rows if they ever become individually identifiable (see tz_suspect below).
NPT_UTC_OFFSET: pd.Timedelta = pd.Timedelta(hours=5, minutes=45)

# Near-duplicate definition: same account + amount within this many seconds.
DUPLICATE_WINDOW_S: float = 5.0

# Structuring thresholds (just below NRB reporting limits) + fraud merchants.
STRUCTURING_THRESHOLDS: tuple[int, ...] = (9_999, 49_999, 99_999)
STRUCTURING_TOLERANCE: float = 600.0
FRAUD_MERCHANT_IDS: frozenset[str] = frozenset({"MERCH-8812", "MERCH-9041", "MERCH-7712"})


def clean_transactions(
    df: pd.DataFrame, *, duplicate_txn_ids: frozenset[str] | None = None
) -> pd.DataFrame:
    """Apply documented data-quality rules to a ``transactions_raw`` chunk.

    Parameters
    ----------
    df:
        Raw transactions DataFrame (a chunk is fine). The source is never
        mutated in place.
    duplicate_txn_ids:
        txn_ids pre-flagged by the *global* duplicate scan
        (:func:`compute_duplicate_txn_ids`). When omitted, duplicate detection
        falls back to the rows in ``df`` alone — fine for whole-table calls,
        but incomplete under chunked streaming because ``transactions_raw`` is
        not time-ordered, so members of a duplicate pair routinely land in
        different chunks.

    Returns
    -------
    pd.DataFrame
        Cleaned copy with derived boolean flags and normalised fields.
    """
    out = df.copy()

    # --- device_id: ~8.1% null for WEB/branch — keep null, expose presence flag ---
    out["has_device_id"] = out["device_id"].notna()

    # --- merchant_category_code: ~4.2% null (QR payments) — impute sentinel ---
    out["merchant_category_code"] = out["merchant_category_code"].fillna("UNKNOWN")

    # --- terminal_id / session_id: channel-dependent nulls are expected — no imputation ---
    out["has_terminal_id"] = out["terminal_id"].notna()
    out["has_session_id"] = out["session_id"].notna()

    # --- fx_rate: null for ~94% NPR rows — keep null, add presence flag ---
    out["has_fx_rate"] = out["fx_rate"].notna()

    # --- notes: ~78% null — too sparse for features; keep only a presence flag ---
    out["has_notes"] = out["notes"].notna() if "notes" in out.columns else False
    if "notes" in out.columns:
        out = out.drop(columns=["notes"])

    # --- ip_address: flag malformed loopback/null-route and RFC-1918 private IPs ---
    ip = out["ip_address"].astype(str)
    out["is_malformed_ip"] = ip.isin(MALFORMED_IPS) | ip.str.startswith(_PRIVATE_IP_PREFIX)

    # --- timestamp: parse Nepal-time strings (with/without milliseconds) ---
    out["timestamp"] = _parse_timestamps(out["timestamp"])

    # --- timezone ambiguity: ~0.9% of ATM records are logged in UTC, not NPT ---
    # No in-band discriminator exists for those rows (verified on the 2026-07
    # drop: a single timestamp format across all 2M rows, txn-vs-geo_events
    # timestamp deltas ~0 for every ATM row, uniform hour-of-day histograms
    # across channels, and no UTC device timezones). Blind-shifting every ATM
    # row by NPT_UTC_OFFSET would corrupt the ~99.1% that are already NPT, so
    # we flag instead of mutate: hour-of-day / night features derived downstream
    # carry ~0.9% noise wherever tz_suspect is True. If a future drop exposes a
    # per-row marker (e.g. a ~+5:45 skew against geo_events timestamps), add
    # NPT_UTC_OFFSET to exactly those rows here, before any hour derivation.
    out["tz_suspect"] = out["channel"] == "ATM"

    # --- amount_npr: normalise mixed 2 vs 4 decimal precision to 4 dp ---
    # The data dictionary documents channel-dependent 2 vs 4 dp precision
    # (~12% of rows) and prescribes 4 dp; rounding to 2 dp would silently
    # destroy the documented precision on 4 dp rows.
    out["amount_npr"] = pd.to_numeric(out["amount_npr"], errors="coerce").round(4)

    # --- amount_npr: flag non-positive amounts as invalid (do NOT drop) ---
    out["is_invalid_amount"] = out["amount_npr"].le(0) | out["amount_npr"].isna()

    # --- duplicate detection: flag (never drop) near-duplicate bursts ---
    if duplicate_txn_ids is not None:
        out["is_possible_duplicate"] = out["txn_id"].isin(duplicate_txn_ids)
    else:
        out["is_possible_duplicate"] = flag_possible_duplicates(out)

    # --- structuring-pattern amounts (just below reporting thresholds) ---
    out["is_structuring_amount"] = _is_structuring_amount(out["amount_npr"])

    # --- known high-risk merchant counterparties ---
    out["is_fraud_merchant"] = out["counterparty_id"].isin(FRAUD_MERCHANT_IDS)

    return out


def _parse_timestamps(series: pd.Series) -> pd.Series:
    """Parse timestamps that may or may not carry a millisecond component."""
    parsed = pd.to_datetime(series, format=_TIMESTAMP_FORMAT_MS, errors="coerce")
    missing = parsed.isna() & series.notna()
    if missing.any():
        fallback = pd.to_datetime(
            series[missing], format=_TIMESTAMP_FORMAT_SEC, errors="coerce"
        )
        parsed.loc[missing] = fallback
    return parsed


def _is_structuring_amount(amount: pd.Series) -> pd.Series:
    """Vectorised: True when the amount sits within tolerance of a threshold."""
    amt = pd.to_numeric(amount, errors="coerce")
    flag = pd.Series(False, index=amt.index)
    for threshold in STRUCTURING_THRESHOLDS:
        flag |= (amt - threshold).abs() <= STRUCTURING_TOLERANCE
    return flag.fillna(False)


def flag_possible_duplicates(df: pd.DataFrame) -> pd.Series:
    """Mark rows sharing account_id + amount_npr within ±5 seconds.

    Vectorised (sort + neighbour diff) so it scales to the full 2M-row table.
    Detection is scoped to the rows passed in: because ``transactions_raw`` is
    not time-ordered, calling this per chunk silently misses pairs split across
    chunks — for streaming use, run :func:`compute_duplicate_txn_ids` once over
    the narrow full-table columns instead.
    """
    flags = pd.Series(False, index=df.index)
    if "timestamp" not in df.columns or df["timestamp"].isna().all():
        return flags

    work = df.loc[
        df["timestamp"].notna(), ["account_id", "amount_npr", "timestamp"]
    ].sort_values(["account_id", "amount_npr", "timestamp"])
    if len(work) < 2:
        return flags

    same_group = (work["account_id"] == work["account_id"].shift()) & (
        work["amount_npr"] == work["amount_npr"].shift()
    )
    delta_s = work["timestamp"].diff().dt.total_seconds().abs()
    close = same_group & (delta_s <= DUPLICATE_WINDOW_S)

    # Flag both members of each near-duplicate pair.
    dup_idx = work.index[close.to_numpy()]
    prev_idx = work.index[close.shift(-1, fill_value=False).to_numpy()]
    flags.loc[dup_idx] = True
    flags.loc[prev_idx] = True
    return flags


def compute_duplicate_txn_ids(frame: pd.DataFrame) -> frozenset[str]:
    """Global duplicate scan over a narrow ``transactions_raw`` projection.

    ``frame`` needs only [txn_id, account_id, amount_npr, timestamp] — cheap to
    hold in full even at 2M rows, which is what makes the scan global. Raw
    string timestamps/amounts are normalised here with the same rules as
    :func:`clean_transactions` so pair equality matches the cleaned table.
    Returns the txn_ids to mark ``is_possible_duplicate`` during chunked runs.
    """
    work = frame[["txn_id", "account_id", "amount_npr", "timestamp"]].copy()
    if not pd.api.types.is_datetime64_any_dtype(work["timestamp"]):
        work["timestamp"] = _parse_timestamps(work["timestamp"])
    work["amount_npr"] = pd.to_numeric(work["amount_npr"], errors="coerce").round(4)
    flags = flag_possible_duplicates(work)
    return frozenset(work.loc[flags, "txn_id"].astype(str))


def clean_geo_events(df: pd.DataFrame) -> pd.DataFrame:
    """Light cleaning for geo_events (malformed IP flag + timestamp parse)."""
    out = df.copy()
    ip = out["ip_address"].astype(str)
    out["is_malformed_ip"] = ip.isin(MALFORMED_IPS) | ip.str.startswith(_PRIVATE_IP_PREFIX)
    if "timestamp" in out.columns:
        out["timestamp"] = _parse_timestamps(out["timestamp"])
    return out


def clean_otp_logs(df: pd.DataFrame) -> pd.DataFrame:
    """Parse datetime columns in sparse OTP logs."""
    out = df.copy()
    datetime_cols = [c for c in out.columns if c.endswith("_at")]
    for col in datetime_cols:
        out[col] = pd.to_datetime(out[col], errors="coerce")
    return out
