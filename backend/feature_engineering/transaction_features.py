"""TransactionFeatureEngineer — core per-transaction features (§3).

Processes ``transactions_raw`` itself into the ``transaction_features_engineered``
table — the natural join point the velocity and geo feature tables build on
(all three key on ``txn_id``). Nothing here needs per-account history, so every
feature is computable from the single transaction's own columns, which makes
``transform`` trivially safe on one new transaction at inference time.

Derived features:

- ``txn_hour`` / ``txn_day_of_week`` / ``txn_is_weekend`` — calendar features
  from ``timestamp``. In batch these are cross-checked against the
  ``weekend_flag`` / ``night_flag`` stored in ``velocity_snapshots``; a
  disagreement rate above the configured tolerance logs a one-line data-quality
  warning (the check, not blind trust).
- ``amount_log`` — ``log1p(amount_npr)``; raw NPR amounts are heavily
  right-skewed, so the log scale is what downstream models should consume.
- ``response_code_is_success`` — 1 iff the ISO-8583 code is an approval
  (``success_response_codes`` in config), since the raw code is categorical but
  success/failure is the signal.
- ``has_notes`` — 1 iff ``notes`` is non-null (the free-text itself is ~40% null
  and out of scope for a tabular model without a separate NLP step).
- ``is_international`` — passed through (already boolean) as an int.
- ``currency_is_foreign`` — 1 iff ``currency != home_currency``.
- one-hot ``channel_*`` / ``auth_method_*`` — low-cardinality, unordered levels;
  an unseen level at inference encodes as an all-zeros row, never an error.

Leakage: every feature uses only the transaction's own at-or-before-timestamp
columns; ``is_fraud`` is never read.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

import numpy as np
import pandas as pd
import psycopg2.extensions

from feature_engineering import db
from feature_engineering.config import ARTIFACTS_DIR, load_config
from feature_engineering.monitoring import (
    StatsDict,
    compute_feature_stats,
    log_stats,
    warn_on_drift,
)

logger = logging.getLogger(__name__)

RAW_INPUT_COLS = (
    "txn_id", "account_id", "timestamp", "amount_npr", "currency",
    "channel", "auth_method", "response_code", "is_international", "notes",
)
# Optional columns joined from velocity_snapshots purely for the data-quality
# cross-check; absent at single-transaction inference time (check is skipped).
CROSSCHECK_COLS = ("weekend_flag", "night_flag")
DERIVED_COLS = (
    "txn_hour", "txn_day_of_week", "txn_is_weekend", "amount_log",
    "response_code_is_success", "has_notes", "is_international",
    "currency_is_foreign",
)


def _to_bool_int(series: pd.Series) -> pd.Series:
    """Map a mixed boolean/'True'/'False'/null column to {0,1} ints (null->0)."""
    return (
        series.map({True: 1, False: 0, "True": 1, "False": 0, "t": 1, "f": 0})
        .fillna(0)
        .astype(np.int16)
    )


class TransactionFeatureEngineer:
    """Fit/transform builder for core per-transaction features.

    ``fit`` learns (training data only): the one-hot category lists for
    ``channel`` / ``auth_method`` and the fit-time feature statistics used for
    drift warnings at transform time. There is nothing account-specific to
    learn, so ``fit`` never touches per-account history.
    """

    TABLE = load_config()["pipeline"]["tables"]["transaction"]

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        self.cfg = cfg or load_config()
        t = self.cfg["transaction"]
        self.home_currency: str = t["home_currency"]
        self.success_codes: set[str] = {str(c) for c in t["success_response_codes"]}
        self.weekend_days: set[int] = set(t["weekend_days"])
        self.night_hours: dict[str, int] = self.cfg["velocity"]["night_hours"]
        self.crosscheck: dict[str, float] = t["flag_crosscheck"]
        # learned at fit()
        self.categories_: dict[str, list[str]] | None = None
        self.fit_stats_: StatsDict | None = None

    # ------------------------------------------------------------------ #
    # batch input (no per-account history; joins the cross-check flags)    #
    # ------------------------------------------------------------------ #

    def attach_history_batch(
        self,
        conn: psycopg2.extensions.connection,
        start: str | datetime,
        end: str | datetime,
    ) -> Iterator[pd.DataFrame]:
        """Yield chunks of transactions in [start, end) with cross-check flags.

        Named to match the other engineers so ``run_batch_pipeline`` can treat
        all three uniformly. There is no window history to attach — the LEFT
        JOIN only pulls ``velocity_snapshots.weekend_flag`` / ``night_flag`` for
        the §3 data-quality cross-check.
        """
        sql = (
            'SELECT t.txn_id, t.account_id, t."timestamp", t.amount_npr, '
            "t.currency, t.channel, t.auth_method, t.response_code, "
            "t.is_international, t.notes, vs.weekend_flag, vs.night_flag "
            "FROM transactions_raw t "
            "LEFT JOIN velocity_snapshots vs USING (txn_id) "
            'WHERE t."timestamp" >= %s AND t."timestamp" < %s'
        )
        chunk_rows = self.cfg["pipeline"]["batch_chunk_rows"]
        with conn.cursor(name=f"txn_batch_{id(self)}") as cur:
            cur.itersize = chunk_rows
            cur.execute(sql, (start, end))
            cols = None
            while True:
                rows = cur.fetchmany(chunk_rows)
                if not rows:
                    break
                if cols is None:
                    cols = [d.name for d in cur.description]
                yield pd.DataFrame(rows, columns=cols)

    # ------------------------------------------------------------------ #
    # pure derivation (no I/O — unit-testable, safe on a single row)       #
    # ------------------------------------------------------------------ #

    def derive(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute the §3 core features from the raw transaction columns.

        Pure and vectorized; safe on a single row. Requires ``fit`` (or loaded
        artifacts) for the one-hot category lists.
        """
        self._check_fitted()
        missing = [c for c in RAW_INPUT_COLS if c not in df.columns]
        if missing:
            raise ValueError(f"derive() missing columns: {missing}")
        out = df.copy()

        ts = pd.to_datetime(out["timestamp"])
        out["txn_hour"] = ts.dt.hour.astype(np.int16)
        dow = ts.dt.dayofweek  # Monday=0 ... Sunday=6
        out["txn_day_of_week"] = dow.astype(np.int16)
        out["txn_is_weekend"] = dow.isin(self.weekend_days).astype(np.int16)

        amount = pd.to_numeric(out["amount_npr"], errors="coerce").fillna(0.0)
        out["amount_log"] = np.log1p(amount.clip(lower=0.0))

        out["response_code_is_success"] = (
            out["response_code"].astype(str).isin(self.success_codes).astype(np.int16)
        )
        out["has_notes"] = out["notes"].notna().astype(np.int16)
        out["is_international"] = _to_bool_int(out["is_international"])
        out["currency_is_foreign"] = (
            out["currency"].astype(str) != self.home_currency
        ).astype(np.int16)

        for col, cats in self.categories_.items():
            values = out[col].astype(str)
            for cat in cats:
                out[f"{col}_{cat}"] = (values == cat).astype(np.int16)

        return out

    def _is_night(self, ts: pd.Series) -> pd.Series:
        """Timestamp-derived night flag (matches velocity's 22:00–05:59 rule)."""
        hours = pd.to_datetime(ts).dt.hour
        return (hours >= self.night_hours["start"]) | (hours < self.night_hours["end"])

    def crosscheck_flags(self, df: pd.DataFrame) -> list[str]:
        """Compare timestamp-derived weekend/night to the stored snapshot flags.

        Returns (and logs) a warning per flag whose disagreement rate exceeds
        the configured tolerance. Silently returns ``[]`` when the snapshot
        columns are absent (single-transaction inference) or all-null.
        """
        warnings: list[str] = []
        ts = pd.to_datetime(df["timestamp"])
        checks = (
            ("weekend_flag", ts.dt.dayofweek.isin(self.weekend_days),
             self.crosscheck["weekend_tolerance"]),
            ("night_flag", self._is_night(ts), self.crosscheck["night_tolerance"]),
        )
        for col, derived_bool, tol in checks:
            if col not in df.columns:
                continue
            stored = pd.Series(df[col]).map(
                {True: 1, False: 0, "True": 1, "False": 0, "t": 1, "f": 0}
            )
            mask = stored.notna().to_numpy()
            if not mask.any():
                continue
            disagree = float(
                (stored[mask].astype(int).to_numpy()
                 != derived_bool.to_numpy()[mask].astype(int)).mean()
            )
            if disagree > tol:
                msg = (
                    f"{col}: {disagree:.1%} of rows disagree with the "
                    f"timestamp-derived value (tolerance {tol:.0%})"
                )
                warnings.append(msg)
                logger.warning("transaction flag cross-check — %s", msg)
        return warnings

    # ------------------------------------------------------------------ #
    # fit / transform                                                      #
    # ------------------------------------------------------------------ #

    def fit(self, df: pd.DataFrame, save: bool = True) -> "TransactionFeatureEngineer":
        """Learn training-only parameters from ``df`` (TIME-based split only).

        ``df`` must carry the raw transaction columns and be restricted to the
        TRAINING window. Learns the one-hot category lists and fit-time stats.
        ``save=False`` skips writing the JSON artifact (tests).
        """
        if df.empty:
            raise ValueError("fit() requires a non-empty training dataframe")
        missing = [c for c in RAW_INPUT_COLS if c not in df.columns]
        if missing:
            raise ValueError(f"fit() missing columns {missing}")
        self.categories_ = {
            col: sorted(df[col].dropna().astype(str).unique())
            for col in self.cfg["encoding"]["one_hot_columns"]
        }
        derived = self.derive(df)
        self.fit_stats_ = compute_feature_stats(derived, self.feature_columns())
        log_stats(self.fit_stats_, "transaction fit")
        if save:
            self.save_artifacts()
        return self

    def transform(
        self,
        df: pd.DataFrame,
        conn: psycopg2.extensions.connection | None = None,
        write_to_db: bool = True,
        source: str = "batch",
    ) -> pd.DataFrame:
        """Derive core features for ``df`` and upsert to the output table.

        Runs the weekend/night data-quality cross-check when the snapshot flags
        are present. Emits drift warnings when transform-time stats stray from
        fit-time stats (batches only).
        """
        started = datetime.now(timezone.utc)
        derived = self.derive(df)
        if any(c in df.columns for c in CROSSCHECK_COLS):
            self.crosscheck_flags(df)
        stats = compute_feature_stats(derived, self.feature_columns())
        if len(df) >= self.cfg["drift"]["min_rows"]:
            log_stats(stats, "transaction transform")
            if self.fit_stats_:
                warn_on_drift(self.fit_stats_, stats, self.cfg)
        else:
            logger.debug("transaction transform: %s rows, source=%s", len(df), source)
        out = derived[["txn_id", "account_id"] + self.feature_columns()].copy()
        out["source"] = source
        if write_to_db:
            if conn is None:
                raise ValueError("transform(write_to_db=True) needs a connection")
            db.ensure_table(conn, self.table_ddl())
            n = db.bulk_upsert(conn, self.TABLE, out, ("txn_id",))
            db.record_run(conn, self.TABLE, n, started, notes=f"source={source}")
        return out

    def transform_one(
        self,
        txn: Mapping[str, Any],
        conn: psycopg2.extensions.connection | None = None,
        write_to_db: bool = True,
    ) -> dict[str, Any]:
        """Score a single new transaction at inference time.

        Needs no history and no Redis — every core feature comes from the
        transaction's own columns. The weekend/night cross-check is skipped
        (no stored snapshot flags for an unseen transaction).
        """
        row = {c: txn.get(c) for c in RAW_INPUT_COLS}
        row["timestamp"] = pd.Timestamp(txn["timestamp"])
        out = self.transform(
            pd.DataFrame([row]), conn=conn, write_to_db=write_to_db,
            source="realtime",
        )
        return out.iloc[0].to_dict()

    # ------------------------------------------------------------------ #
    # schema / artifacts                                                   #
    # ------------------------------------------------------------------ #

    def feature_columns(self) -> list[str]:
        """Ordered engineered columns written to the output table."""
        self._check_fitted()
        one_hot = [
            f"{col}_{cat}" for col, cats in self.categories_.items() for cat in cats
        ]
        return list(DERIVED_COLS) + one_hot

    def table_ddl(self) -> str:
        """Idempotent DDL for the transaction output table (FK to transactions_raw)."""
        float_cols = {"amount_log"}
        col_lines = []
        for c in self.feature_columns():
            sql_type = "DOUBLE PRECISION" if c in float_cols else "INTEGER"
            col_lines.append(f'    "{c}" {sql_type}')
        cols = ",\n".join(col_lines)
        return f"""
CREATE TABLE IF NOT EXISTS {self.TABLE} (
    txn_id TEXT PRIMARY KEY REFERENCES transactions_raw(txn_id),
    account_id TEXT NOT NULL,
{cols},
    source TEXT,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

    def _check_fitted(self) -> None:
        if self.categories_ is None:
            raise RuntimeError(
                "TransactionFeatureEngineer is not fitted; call fit() or load_artifacts()"
            )

    def save_artifacts(self, path: str | Path | None = None) -> Path:
        """Persist fitted parameters as JSON for the inference service."""
        ARTIFACTS_DIR.mkdir(exist_ok=True)
        path = Path(path or ARTIFACTS_DIR / "transaction_fit.json")
        path.write_text(
            json.dumps(
                {"categories": self.categories_, "fit_stats": self.fit_stats_},
                indent=2,
            )
        )
        return path

    @classmethod
    def load_artifacts(
        cls, path: str | Path | None = None, cfg: dict[str, Any] | None = None
    ) -> "TransactionFeatureEngineer":
        """Rebuild a fitted engineer from :meth:`save_artifacts` output."""
        path = Path(path or ARTIFACTS_DIR / "transaction_fit.json")
        blob = json.loads(path.read_text())
        eng = cls(cfg=cfg)
        eng.categories_ = blob["categories"]
        eng.fit_stats_ = blob.get("fit_stats")
        return eng
