"""Per-model input builders for the Behavior Agent.

The three models were trained on DIFFERENT feature sets/sources, so each gets
its own builder that reproduces its notebook's exact preprocessing and column
order (from the saved manifest). A shared feature vector would silently break
them.

Failure policy: a missing transaction/profile/velocity/geo row, or a NULL in
a field the training run never saw NULL, raises :class:`MissingInputError`
naming the field — never a silent zero-fill. Where the notebook *defined* a
null path (OTP absent -> zeros + has_event=0, device absent -> dev_has_device=0,
mcc NULL -> "UNKNOWN"), that same path is reproduced here because it is the
trained-in semantic, not an imputation.

Postgres/manifest format bridges (verified in Step 0):
  - merchant_category_code stored as '4814' -> trained column '..._4814.0'
  - response_code stored as '00'/'05'       -> trained column 'response_code_0'/'rc_0'
  - device first_seen/last_seen are timestamptz; training compared UTC-naive
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time as dtime, timezone
from typing import Any

import numpy as np
import pandas as pd

from behavior_agent.artifacts import ModelBundle


class MissingInputError(Exception):
    """A required source row or field is absent for this transaction."""


class TxnNotFoundError(MissingInputError):
    """txn_id not present in transactions_raw (or not owned by account_id)."""


class UnknownCategoryError(MissingInputError):
    """A categorical value has no corresponding trained one-hot column."""


# Ordinal maps — exactly the XGBoost notebook's (Step 4).
RISK_MAP = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "WATCHLIST": 3}
KYC_MAP = {"BASIC": 0, "STANDARD": 1, "ENHANCED": 2}
INCOME_MAP = {"<15K": 0, "15K-30K": 1, "30K-75K": 2, "75K-200K": 3, "200K+": 4}
# LSTM Stage 0: auth strength is genuinely ordinal.
AUTH_STRENGTH = {"BIOMETRIC": 3, "MPIN": 2, "CARD_PIN": 2, "OTP_SMS": 1, "OTP_EMAIL": 1}


def _require(row: Any, field: str, txn_id: str) -> Any:
    val = row[field]
    if val is None:
        raise MissingInputError(
            f"txn {txn_id}: field '{field}' is NULL but the model was trained "
            f"with no NULLs in it — refusing to fabricate a value")
    return val


def _mcc_level(value: str | None) -> str:
    """'4814' / None -> '4814.0' / 'UNKNOWN' (training read the CSV column as
    float, so trained one-hot levels carry the '.0' suffix)."""
    return "UNKNOWN" if value is None else str(float(value))


def _rc_level(value: str) -> str:
    """'00' -> '0' (training read response_code as int)."""
    return str(int(value))


def _set_onehot(feats: dict[str, float], prefix: str, level: str,
                known: set[str], txn_id: str) -> None:
    col = f"{prefix}_{level}"
    if col not in known:
        raise UnknownCategoryError(
            f"txn {txn_id}: {prefix}='{level}' has no trained column '{col}'")
    feats[col] = 1.0


def _age_days(ts: datetime, since) -> float:
    """(timestamp - date).days, clipped >= 0 — matches the notebooks' pandas
    day arithmetic (date is midnight)."""
    delta = ts - datetime.combine(since, dtime())
    return float(max(delta.days, 0))


def _vector(feats: dict[str, float], feature_columns: list[str],
            model_name: str) -> np.ndarray:
    extra = set(feats) - set(feature_columns)
    assert not extra, f"{model_name}: built features not in manifest: {sorted(extra)}"
    vec = np.array([feats.get(c, 0.0) for c in feature_columns], dtype=np.float32)
    assert vec.shape[0] == len(feature_columns), (
        f"{model_name}: built {vec.shape[0]} features, manifest expects "
        f"{len(feature_columns)}")
    return vec


# =========================================================================
# Isolation Forest — transactions_raw only + all-time account aggregates
# =========================================================================

_ISO_SQL = """
SELECT t.txn_id, t.timestamp, t.amount_npr, t.processing_time_ms,
       t.is_international, t.txn_type, t.currency, t.channel, t.auth_method,
       t.merchant_category_code, t.response_code,
       (t.terminal_id IS NOT NULL)  AS has_terminal,
       (t.session_id IS NOT NULL)   AS has_session,
       (t.device_id IS NULL)        AS device_id_missing,
       (t.notes IS NOT NULL)        AS has_notes,
       a.account_txn_count, a.account_amount_mean, a.account_amount_std
FROM transactions_raw t
CROSS JOIN LATERAL (
    SELECT count(*)                 AS account_txn_count,
           avg(x.amount_npr)        AS account_amount_mean,
           stddev_samp(x.amount_npr) AS account_amount_std
    FROM transactions_raw x
    WHERE x.account_id = t.account_id
) a
WHERE t.txn_id = $1
"""


async def build_isolation_forest_input(txn_id: str, conn: Any,
                                       bundle: ModelBundle) -> np.ndarray:
    """Feature row per isolation_forest_feature_list.json, saved scaler applied.

    Account aggregates (count/mean/std of amount) are computed over ALL of the
    account's rows in transactions_raw — the training notebook used a whole-file
    groupby, so this reproduces the trained feature, not a point-in-time proxy.
    """
    row = await conn.fetchrow(_ISO_SQL, txn_id)
    if row is None:
        raise TxnNotFoundError(f"txn {txn_id} not found in transactions_raw")

    known = set(bundle.iso_features)
    feats: dict[str, float] = {}

    amount = float(_require(row, "amount_npr", txn_id))
    ts: datetime = row["timestamp"]
    feats["amount_log"] = math.log1p(amount)
    # Step-0 verified: skew ~= 0 at train, so processing_time_feat is the RAW ms.
    feats["processing_time_feat"] = float(_require(row, "processing_time_ms", txn_id))
    feats["hour_of_day"] = float(ts.hour)
    feats["day_of_week"] = float(ts.weekday())
    feats["is_weekend"] = float(ts.weekday() >= 5)
    feats["is_night"] = float(ts.hour < 6 or ts.hour >= 22)
    feats["is_international"] = float(_require(row, "is_international", txn_id))
    for flag in ("has_terminal", "has_session", "device_id_missing", "has_notes"):
        feats[flag] = float(row[flag])

    mean = float(_require(row, "account_amount_mean", txn_id))
    std = row["account_amount_std"]  # NULL for single-txn accounts (stddev_samp)
    std = float(std) if std is not None else 0.0
    feats["account_txn_count"] = float(row["account_txn_count"])
    feats["account_amount_mean"] = mean
    feats["account_amount_std"] = std
    feats["account_cold_start"] = float(std == 0.0)
    feats["amount_zscore_vs_account"] = (amount - mean) / std if std > 0 else 0.0

    _set_onehot(feats, "txn_type", _require(row, "txn_type", txn_id), known, txn_id)
    _set_onehot(feats, "currency", _require(row, "currency", txn_id), known, txn_id)
    _set_onehot(feats, "channel", _require(row, "channel", txn_id), known, txn_id)
    _set_onehot(feats, "auth_method", _require(row, "auth_method", txn_id), known, txn_id)
    _set_onehot(feats, "merchant_category_code",
                _mcc_level(row["merchant_category_code"]), known, txn_id)
    _set_onehot(feats, "response_code",
                _rc_level(_require(row, "response_code", txn_id)), known, txn_id)

    vec = _vector(feats, bundle.iso_features, "isolation_forest")

    # Scale the continuous block with the SAVED scaler, in its fitted order
    # (named frame keeps sklearn's feature-name check satisfied).
    scaled_cols = bundle.iso_scaled_cols
    idx = [bundle.iso_features.index(c) for c in scaled_cols]
    block = pd.DataFrame(vec[idx].reshape(1, -1).astype(np.float64),
                         columns=scaled_cols)
    vec[idx] = bundle.iso_scaler.transform(block)[0].astype(np.float32)
    return vec


# =========================================================================
# XGBoost — transactions_raw JOIN customer_profiles
# =========================================================================

_XGB_SQL = """
SELECT t.txn_id, t.timestamp, t.amount_npr, t.processing_time_ms,
       t.is_international, t.txn_type, t.currency, t.channel, t.auth_method,
       t.merchant_category_code, t.response_code,
       (t.terminal_id IS NOT NULL)  AS has_terminal,
       (t.session_id IS NOT NULL)   AS has_session,
       (t.device_id IS NULL)        AS device_id_missing,
       (t.notes IS NOT NULL)        AS has_notes,
       p.account_id                 AS profile_account_id,
       p.customer_since, p.age_group, p.district, p.province,
       p.occupation_category, p.monthly_income_band_npr, p.kyc_tier,
       p.risk_tier, p.avg_monthly_txn_count, p.avg_monthly_txn_value_npr,
       p.primary_channel, p.international_txn_history, p.has_linked_esewa,
       p.has_linked_khalti, p.num_beneficiaries_registered, p.is_dormant,
       p.churn_risk_score
FROM transactions_raw t
LEFT JOIN customer_profiles p ON p.account_id = t.account_id
WHERE t.txn_id = $1
"""


async def build_xgboost_input(txn_id: str, conn: Any,
                              bundle: ModelBundle) -> np.ndarray:
    """Transaction + profile feature row per model_feature_list.json.

    The training join was asserted orphan-free and needed zero fallback fills,
    so a missing profile or an unexpected NULL here is a loud error.
    """
    row = await conn.fetchrow(_XGB_SQL, txn_id)
    if row is None:
        raise TxnNotFoundError(f"txn {txn_id} not found in transactions_raw")
    if row["profile_account_id"] is None:
        raise MissingInputError(
            f"txn {txn_id}: no customer_profiles row for its account — the "
            f"XGBoost model requires the profile join")

    known = set(bundle.xgb_features)
    feats: dict[str, float] = {}
    ts: datetime = row["timestamp"]
    amount = float(_require(row, "amount_npr", txn_id))

    feats["processing_time_ms"] = float(_require(row, "processing_time_ms", txn_id))
    feats["is_international"] = float(_require(row, "is_international", txn_id))
    feats["amount_log"] = math.log1p(amount)
    feats["hour_of_day"] = float(ts.hour)
    feats["day_of_week"] = float(ts.weekday())
    feats["is_weekend"] = float(ts.weekday() >= 5)
    feats["is_night"] = float(ts.hour < 6 or ts.hour >= 22)
    for flag in ("has_terminal", "has_session", "device_id_missing", "has_notes"):
        feats[flag] = float(row[flag])

    # Profile numerics/booleans — training saw no NULLs in any of these.
    for col in ("avg_monthly_txn_count", "avg_monthly_txn_value_npr",
                "num_beneficiaries_registered", "churn_risk_score"):
        feats[col] = float(_require(row, col, txn_id))
    for col in ("international_txn_history", "has_linked_esewa",
                "has_linked_khalti", "is_dormant"):
        feats[col] = float(_require(row, col, txn_id))

    feats["account_age_days"] = _age_days(ts, _require(row, "customer_since", txn_id))
    avg_val = feats["avg_monthly_txn_value_npr"]
    feats["amount_vs_profile_ratio"] = min(amount / avg_val, 100.0) if avg_val > 0 else 0.0

    for col, mapping, src in (("risk_tier_ord", RISK_MAP, "risk_tier"),
                              ("kyc_tier_ord", KYC_MAP, "kyc_tier"),
                              ("income_band_ord", INCOME_MAP, "monthly_income_band_npr")):
        level = _require(row, src, txn_id)
        if level not in mapping:
            raise UnknownCategoryError(f"txn {txn_id}: {src}='{level}' not in ordinal map")
        feats[col] = float(mapping[level])

    for prefix, src in (("txn_type", "txn_type"), ("currency", "currency"),
                        ("channel", "channel"), ("auth_method", "auth_method"),
                        ("age_group", "age_group"), ("district", "district"),
                        ("province", "province"),
                        ("occupation_category", "occupation_category"),
                        ("primary_channel", "primary_channel")):
        _set_onehot(feats, prefix, _require(row, src, txn_id), known, txn_id)
    _set_onehot(feats, "merchant_category_code",
                _mcc_level(row["merchant_category_code"]), known, txn_id)
    _set_onehot(feats, "response_code",
                _rc_level(_require(row, "response_code", txn_id)), known, txn_id)

    return _vector(feats, bundle.xgb_features, "xgboost")


# =========================================================================
# LSTM — sequence window over transactions_raw + device/velocity/geo/otp,
#        static branch over customer_profiles + account_graph_nodes
# =========================================================================

@dataclass
class LSTMInput:
    seq: np.ndarray        # (seq_len, n_seq_feat) float32, left-padded, scaled
    length: int            # number of real (unpadded) steps
    static: np.ndarray     # (n_static_feat,) float32, encoded + scaled
    history_count: int     # account txns up to and including this txn


_LSTM_SEQ_SQL = """
WITH hist AS (
    SELECT t.txn_id, t.timestamp, t.amount_npr, t.processing_time_ms,
           t.is_international, t.fx_rate, t.auth_method, t.txn_type, t.channel,
           t.response_code, t.merchant_category_code, t.device_id,
           v.txn_id AS v_txn_id, v.txn_count_1m, v.txn_count_5m, v.txn_count_15m,
           v.txn_count_1h, v.txn_count_24h, v.txn_count_7d,
           v.total_amount_1h_npr, v.total_amount_24h_npr,
           v.unique_counterparties_1h, v.unique_counterparties_24h,
           v.new_counterparty_flag, v.max_single_txn_24h_npr,
           v.avg_txn_amount_30d_npr, v.std_txn_amount_30d_npr, v.z_score_amount,
           v.dormancy_break, v.weekend_flag, v.night_flag,
           g.txn_id AS g_txn_id, g.is_vpn, g.is_tor, g.is_datacenter,
           g.velocity_flag, g.km_from_home_district, g.prev_txn_km,
           g.prev_txn_time_delta_min, g.impossible_travel, g.ip_country,
           o.txn_id AS o_txn_id, o.resolution_time_ms AS otp_resolution_time_ms,
           o.attempt_count_ch1 AS otp_attempt_count_ch1,
           o.attempt_count_ch2 AS otp_attempt_count_ch2,
           o.sim_swap_suspected AS otp_sim_swap_suspected,
           d.device_id AS d_device_id, d.first_seen, d.last_seen, d.timezone,
           d.risk_signals, d.is_rooted_or_jailbroken, d.vpn_detected,
           d.tor_exit_node, d.biometric_enrolled, d.is_shared_device,
           d.num_accounts_seen_on_device,
           count(*) OVER () AS history_count
    FROM transactions_raw t
    LEFT JOIN velocity_snapshots v ON v.txn_id = t.txn_id
    LEFT JOIN geo_events g ON g.txn_id = t.txn_id
    LEFT JOIN otp_logs o ON o.txn_id = t.txn_id
    LEFT JOIN device_fingerprints d ON d.device_id = t.device_id
    WHERE t.account_id = $1
      AND (t.timestamp, t.txn_id) <= ($2::timestamp, $3)
    ORDER BY t.timestamp DESC, t.txn_id DESC
    LIMIT $4
)
SELECT * FROM hist ORDER BY timestamp, txn_id
"""

_LSTM_STATIC_SQL = """
SELECT p.age_group, p.province, p.occupation_category,
       p.monthly_income_band_npr, p.kyc_tier, p.district,
       p.has_linked_esewa, p.has_linked_khalti, p.num_beneficiaries_registered,
       p.customer_since,
       n.id AS node_id, n.degree_in, n.degree_out,
       n.total_received_npr, n.total_sent_npr
FROM customer_profiles p
LEFT JOIN account_graph_nodes n ON n.id = p.account_id
WHERE p.account_id = $1
"""

# ohe.feature_names_in_ order, fixed at train time (Stage 5).
_STATIC_CAT_COLS = ["age_group", "province", "occupation_category",
                    "monthly_income_band_npr", "kyc_tier"]


def _utc_naive(ts: datetime | None) -> datetime | None:
    """Training parsed device timestamps with utc=True then dropped the tz."""
    if ts is None:
        return None
    return ts.astimezone(timezone.utc).replace(tzinfo=None)


def _seq_row_features(r: Any, ts: datetime, known: set[str]) -> dict[str, float]:
    """The 79 per-timestep features for one historical txn, exactly as the
    LSTM notebook's Stage 0 built feature_table.csv."""
    txn_id = r["txn_id"]
    f: dict[str, float] = {}

    # `currency` was a string coerced to NaN->0 at train — a constant-zero
    # feature. Reproduce the constant rather than inventing an encoding.
    f["currency"] = 0.0
    f["processing_time_ms"] = float(r["processing_time_ms"] or 0)
    f["is_international"] = float(bool(r["is_international"]))
    f["has_fx_rate"] = float(r["fx_rate"] is not None)
    f["fx_rate"] = float(r["fx_rate"]) if r["fx_rate"] is not None else 1.0
    f["auth_strength"] = float(AUTH_STRENGTH.get(r["auth_method"], 0))
    _set_onehot(f, "txn_type", r["txn_type"], known, txn_id)
    _set_onehot(f, "channel", r["channel"], known, txn_id)
    _set_onehot(f, "rc", _rc_level(r["response_code"]), known, txn_id)
    _set_onehot(f, "mcc", _mcc_level(r["merchant_category_code"]), known, txn_id)

    # -- device fingerprint: dev_has_device keys off the TXN's device_id (as
    # at train); an unresolved fingerprint leaves the dev_* features at 0 --
    f["dev_has_device"] = float(r["device_id"] is not None)
    if r["d_device_id"] is not None:
        fs, ls = _utc_naive(r["first_seen"]), _utc_naive(r["last_seen"])
        true_first = min(fs, ls)                       # ~50% reversed at source
        f["dev_age_days"] = max((ts - true_first).total_seconds() / 86400.0, 0.0)
        f["dev_ts_was_reversed"] = float(fs > ls)
        f["dev_timezone_mismatch"] = float(r["timezone"] != "Asia/Kathmandu")
        signals = set(r["risk_signals"] or [])
        for sig in ("ROOTED", "VPN_ACTIVE", "NEW_DEVICE"):
            f[f"dev_risk_{sig}"] = float(sig in signals)
        for col in ("is_rooted_or_jailbroken", "vpn_detected", "tor_exit_node",
                    "biometric_enrolled", "is_shared_device"):
            f[f"dev_{col}"] = float(bool(r[col]))
        f["dev_num_accounts_seen_on_device"] = float(r["num_accounts_seen_on_device"] or 0)

    # -- velocity snapshot (required: training had 100% coverage) --
    if r["v_txn_id"] is None:
        raise MissingInputError(
            f"txn {txn_id}: no velocity_snapshots row — LSTM sequence features "
            f"txn_count_*/z_score_amount cannot be built")
    for col in ("txn_count_1m", "txn_count_5m", "txn_count_15m", "txn_count_1h",
                "txn_count_24h", "txn_count_7d", "total_amount_1h_npr",
                "total_amount_24h_npr", "unique_counterparties_1h",
                "unique_counterparties_24h", "max_single_txn_24h_npr",
                "avg_txn_amount_30d_npr", "std_txn_amount_30d_npr", "z_score_amount"):
        f[col] = float(r[col]) if r[col] is not None else 0.0
    for col in ("new_counterparty_flag", "dormancy_break", "weekend_flag", "night_flag"):
        f[col] = float(bool(r[col]))

    # -- geo event (required: training had 100% coverage) --
    if r["g_txn_id"] is None:
        raise MissingInputError(
            f"txn {txn_id}: no geo_events row — LSTM sequence features "
            f"km_from_home_district/impossible_travel cannot be built")
    for col in ("is_vpn", "is_tor", "is_datacenter", "velocity_flag", "impossible_travel"):
        f[col] = float(bool(r[col]))
    for col in ("km_from_home_district", "prev_txn_km", "prev_txn_time_delta_min"):
        f[col] = float(r[col]) if r[col] is not None else 0.0
    f["geo_is_domestic_ip"] = float(r["ip_country"] == "Nepal")

    # -- OTP (sparse by design: ~2% of txns; absence IS the signal) --
    has_otp = r["o_txn_id"] is not None
    f["otp_has_event"] = float(has_otp)
    f["otp_resolution_time_ms"] = float(r["otp_resolution_time_ms"] or 0)
    f["otp_attempt_count_ch1"] = float(r["otp_attempt_count_ch1"] or 0)
    f["otp_attempt_count_ch2"] = float(r["otp_attempt_count_ch2"] or 0)
    f["otp_sim_swap_suspected"] = float(bool(r["otp_sim_swap_suspected"]))

    amount = float(r["amount_npr"]) if r["amount_npr"] is not None else 0.0
    f["log_amount_npr"] = math.log1p(max(amount, 0.0))
    return f


async def build_lstm_input(account_id: str, txn_id: str, conn: Any,
                           bundle: ModelBundle) -> LSTMInput:
    """Sequence window (last N txns ending at txn_id) + static branch, using
    the SAVED preprocessors. Accounts with < N history get the same
    left-pad + mask used in training."""
    seq_len = bundle.lstm_seq_len
    seq_features: list[str] = bundle.lstm_manifest["seq_features"]
    known = set(seq_features)

    cur = await conn.fetchrow(
        "SELECT timestamp, account_id FROM transactions_raw WHERE txn_id = $1", txn_id)
    if cur is None:
        raise TxnNotFoundError(f"txn {txn_id} not found in transactions_raw")
    if cur["account_id"] != account_id:
        raise TxnNotFoundError(
            f"txn {txn_id} belongs to account {cur['account_id']}, not {account_id}")

    rows = await conn.fetch(_LSTM_SEQ_SQL, account_id, cur["timestamp"], txn_id, seq_len)
    if not rows or rows[-1]["txn_id"] != txn_id:
        raise MissingInputError(
            f"txn {txn_id}: could not anchor the sequence window on it")
    history_count = int(rows[0]["history_count"])

    ts_end: datetime = cur["timestamp"]
    mat = np.zeros((len(rows), len(seq_features)), dtype=np.float32)
    for i, r in enumerate(rows):
        feats = _seq_row_features(r, r["timestamp"], known)
        mat[i] = [feats.get(c, 0.0) for c in seq_features]

    scaled = bundle.lstm_preproc["seq_scaler"].transform(
        mat.astype(np.float64)).astype(np.float32)
    length = scaled.shape[0]
    padded = np.zeros((seq_len, len(seq_features)), dtype=np.float32)
    padded[seq_len - length:] = scaled  # LEFT-pad: real steps sit at the end

    # ---- static branch ----
    srow = await conn.fetchrow(_LSTM_STATIC_SQL, account_id)
    if srow is None:
        raise MissingInputError(
            f"account {account_id}: no customer_profiles row — LSTM static "
            f"branch cannot be built")
    if srow["node_id"] is None:
        raise MissingInputError(
            f"account {account_id}: no account_graph_nodes row — LSTM static "
            f"branch needs degree_in/out and totals")

    # Categorical block: training filled missing profile categoricals as 'UNK'
    # (an unseen OHE level -> all-zeros via handle_unknown='ignore').
    cat_df = pd.DataFrame(
        [[srow[c] if srow[c] is not None else "UNK" for c in _STATIC_CAT_COLS]],
        columns=_STATIC_CAT_COLS)
    ohe_vec = bundle.lstm_preproc["ohe"].transform(cat_df)[0]

    district_freq = float(bundle.lstm_preproc["district_freq"].get(srow["district"], 0.0))
    if srow["customer_since"] is None:
        raise MissingInputError(f"account {account_id}: customer_since is NULL")
    # Training computed account_age_days at the account's latest txn in the
    # table; at serve time the current txn's timestamp is the point-in-time
    # equivalent (documented in the README).
    num_row = np.array([[
        float(bool(srow["has_linked_esewa"])),
        float(bool(srow["has_linked_khalti"])),
        float(srow["num_beneficiaries_registered"] or 0),
        _age_days(ts_end, srow["customer_since"]),
        float(srow["degree_in"] or 0),
        float(srow["degree_out"] or 0),
        float(srow["total_received_npr"] or 0),
        float(srow["total_sent_npr"] or 0),
        district_freq,
    ]], dtype=np.float64)
    num_scaled = bundle.lstm_preproc["static_scaler"].transform(num_row)[0]

    static = np.concatenate([ohe_vec, num_scaled]).astype(np.float32)
    expected_static = len(bundle.lstm_manifest["static_features"])
    if static.shape[0] != expected_static:
        raise MissingInputError(
            f"account {account_id}: static branch built {static.shape[0]} features, "
            f"manifest expects {expected_static}")

    return LSTMInput(seq=padded, length=length, static=static,
                     history_count=history_count)


async def account_history_count(account_id: str, txn_id: str, conn: Any) -> int:
    """Account txns up to and including txn_id — drives LSTM gating and the
    history-dependent blend weights, so it's computed even when the LSTM
    input is never built."""
    row = await conn.fetchrow(
        """
        SELECT count(*) AS n
        FROM transactions_raw h, transactions_raw t
        WHERE t.txn_id = $2 AND h.account_id = $1 AND t.account_id = $1
          AND (h.timestamp, h.txn_id) <= (t.timestamp, t.txn_id)
        """,
        account_id, txn_id)
    if row is None or row["n"] == 0:
        raise TxnNotFoundError(
            f"txn {txn_id} not found for account {account_id}")
    return int(row["n"])
