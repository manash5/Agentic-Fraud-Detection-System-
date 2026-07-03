"""VelocityFeatureEngineer — leakage-safe velocity features (§3, §5).

Architecture:

- **Batch / historical (Postgres)** — sliding-window counts, amount sums and
  the trailing-30d baseline are recomputed from ``transactions_raw`` with SQL
  window functions restricted to rows at or before each transaction's
  timestamp (``RANGE ... PRECEDING AND CURRENT ROW``; the 30d baseline
  additionally ``EXCLUDE CURRENT ROW``). The pre-aggregated columns in
  ``velocity_snapshots`` are NOT used: empirically they are not internally
  consistent (window counts are non-monotone in 20%+ of rows, and
  ``z_score_amount`` disagrees with ``(amount-avg30)/std30`` in ~98% of rows),
  so trusting them would inject noise and potential lookahead.
- **Real-time (Redis)** — a single new transaction gets its window counts
  from :class:`~feature_engineering.redis_client.VelocityStateStore`
  (ZADD + ZCOUNT) and its 30d baseline from the nightly-refreshed
  ``account_baseline:{account_id}`` hash. If Redis is unreachable the
  engineer degrades to an indexed Postgres query on ``transactions_raw``
  (logged as a warning, never an error).

Window counts always INCLUDE the current transaction (minimum 1) — the Redis
path ZADDs before counting and the SQL frame ends at CURRENT ROW.

Usage::

    eng = VelocityFeatureEngineer()
    train = eng.attach_history_batch(conn, start, end)   # pulls windows via SQL
    eng.fit(train)                                       # train period ONLY
    out = eng.transform(chunk, conn=conn)                # derives + upserts
    feats = eng.transform_one(txn, conn=conn)            # single-txn inference
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
from feature_engineering.redis_client import RedisUnavailable, VelocityStateStore

logger = logging.getLogger(__name__)

RAW_INPUT_COLS = ("txn_id", "account_id", "timestamp", "amount_npr")
HISTORY_COLS = (
    "txn_count_1m", "txn_count_5m", "txn_count_15m", "txn_count_1h",
    "txn_count_24h", "txn_count_7d", "total_amount_1h_npr", "total_amount_24h_npr",
    "avg_txn_amount_30d_npr", "std_txn_amount_30d_npr", "n_txn_30d_prior",
)
DERIVED_COLS = (
    "z_score_amount", "velocity_acceleration", "amount_deviation_ratio",
    "structuring_proximity", "night_flag", "night_burst_interaction", "is_cold_start",
)


class VelocityFeatureEngineer:
    """Fit/transform velocity feature builder with Redis hot path + PG batch.

    ``fit`` learns (training data only): the amount winsorization cap and the
    fit-time feature statistics used for drift warnings at transform time.
    Categorical encoding of ``channel`` / ``auth_method`` lives in
    :class:`~feature_engineering.transaction_features.TransactionFeatureEngineer`
    (the ``transaction_features_engineered`` table); this table holds ONLY the
    window counts and the §6 derived velocity features.
    """

    TABLE = load_config()["pipeline"]["tables"]["velocity"]

    def __init__(
        self,
        cfg: dict[str, Any] | None = None,
        state_store: VelocityStateStore | None = None,
    ) -> None:
        self.cfg = cfg or load_config()
        self._store = state_store
        v = self.cfg["velocity"]
        self.windows_s: dict[str, int] = v["windows_s"]
        self.amount_windows_s: dict[str, int] = v["amount_windows_s"]
        # learned at fit()
        self.amount_cap_: float | None = None
        self.fit_stats_: StatsDict | None = None

    # ------------------------------------------------------------------ #
    # history features: batch SQL                                         #
    # ------------------------------------------------------------------ #

    def _history_sql(self) -> str:
        """Window-function SQL computing all history features per txn."""
        count_windows = ",\n        ".join(
            f"count(*) OVER (PARTITION BY account_id ORDER BY \"timestamp\" "
            f"RANGE BETWEEN INTERVAL '{secs} seconds' PRECEDING AND CURRENT ROW) "
            f"AS {name}"
            for name, secs in self.windows_s.items()
        )
        amount_windows = ",\n        ".join(
            f"sum(amount_npr) OVER (PARTITION BY account_id ORDER BY \"timestamp\" "
            f"RANGE BETWEEN INTERVAL '{secs} seconds' PRECEDING AND CURRENT ROW) "
            f"AS {name}"
            for name, secs in self.amount_windows_s.items()
        )
        days = self.cfg["velocity"]["baseline_window_days"]
        return f"""
    SELECT txn_id, account_id, "timestamp", amount_npr,
        {count_windows},
        {amount_windows},
        avg(amount_npr) OVER w30 AS avg_txn_amount_30d_npr,
        stddev_samp(amount_npr) OVER w30 AS std_txn_amount_30d_npr,
        count(*) OVER w30 AS n_txn_30d_prior
    FROM transactions_raw
    WINDOW w30 AS (PARTITION BY account_id ORDER BY "timestamp"
                   RANGE BETWEEN INTERVAL '{days} days' PRECEDING AND CURRENT ROW
                   EXCLUDE CURRENT ROW)
    """

    def attach_history_batch(
        self,
        conn: psycopg2.extensions.connection,
        start: str | datetime,
        end: str | datetime,
    ) -> Iterator[pd.DataFrame]:
        """Yield chunks of transactions in [start, end) with history columns.

        The window functions scan the FULL ``transactions_raw`` history (so a
        transaction near ``start`` still sees its 7d/30d past); only the
        emitted rows are filtered to the requested period.
        """
        sql = (
            f"SELECT * FROM ({self._history_sql()}) h "
            'WHERE "timestamp" >= %s AND "timestamp" < %s'
        )
        chunk_rows = self.cfg["pipeline"]["batch_chunk_rows"]
        with conn.cursor(name=f"vel_hist_{id(self)}") as cur:
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
    # pure derivation (no I/O — unit-testable)                             #
    # ------------------------------------------------------------------ #

    def derive(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute the §5 derived features from raw + history columns.

        Pure and vectorized; safe on a single row. Requires ``fit`` (or
        loaded artifacts) for the winsor cap and one-hot categories.
        """
        self._check_fitted()
        missing = [c for c in RAW_INPUT_COLS + HISTORY_COLS if c not in df.columns]
        if missing:
            raise ValueError(f"derive() missing columns: {missing}")
        v = self.cfg["velocity"]
        out = df.copy()

        amount = pd.to_numeric(out["amount_npr"], errors="coerce").fillna(0.0)
        amount_w = amount.clip(upper=self.amount_cap_)
        avg30 = pd.to_numeric(out["avg_txn_amount_30d_npr"], errors="coerce")
        std30 = pd.to_numeric(out["std_txn_amount_30d_npr"], errors="coerce")
        n30 = pd.to_numeric(out["n_txn_30d_prior"], errors="coerce").fillna(0)

        cold = (
            (n30 < v["cold_start"]["min_prior_txns_30d"])
            | avg30.isna()
            | std30.isna()
            | (std30 < v["cold_start"]["std_floor_npr"])
            | (avg30 < v["cold_start"]["std_floor_npr"])
        )
        out["is_cold_start"] = cold.astype(np.int16)

        z = (amount_w - avg30) / std30.clip(lower=v["cold_start"]["std_floor_npr"])
        z = z.clip(-v["z_score_clip"], v["z_score_clip"])
        out["z_score_amount"] = z.where(~cold, 0.0).fillna(0.0)

        c1h = pd.to_numeric(out["txn_count_1h"], errors="coerce").fillna(0.0)
        c24h = pd.to_numeric(out["txn_count_24h"], errors="coerce").fillna(0.0)
        out["velocity_acceleration"] = c1h / np.maximum(c24h / 24.0, v["epsilon"])

        ratio = amount_w / avg30
        ratio = ratio.where(~cold, 1.0)  # neutral for new accounts; is_cold_start carries the signal
        out["amount_deviation_ratio"] = (
            ratio.clip(0.0, v["amount_deviation_clip"]).fillna(1.0)
        )

        thresholds = np.asarray(v["structuring_thresholds_npr"], dtype=float)
        prox = np.min(
            np.abs(amount.to_numpy()[:, None] - thresholds[None, :]), axis=1
        )
        out["structuring_proximity"] = np.minimum(
            prox, v["structuring_proximity_cap_npr"]
        )

        hours = pd.to_datetime(out["timestamp"]).dt.hour
        night = (hours >= v["night_hours"]["start"]) | (hours < v["night_hours"]["end"])
        out["night_flag"] = night.astype(np.int16)
        out["night_burst_interaction"] = out["night_flag"] * pd.to_numeric(
            out["txn_count_1m"], errors="coerce"
        ).fillna(0.0)

        return out

    # ------------------------------------------------------------------ #
    # fit / transform                                                      #
    # ------------------------------------------------------------------ #

    def fit(self, df: pd.DataFrame, save: bool = True) -> "VelocityFeatureEngineer":
        """Learn training-only parameters from ``df``.

        ``df`` must be restricted to the TRAINING period (time-based split —
        never a random shuffle) and carry the raw + history columns (use
        :meth:`attach_history_batch`). Learns the amount winsor cap and fit-time
        feature statistics. ``save=False`` skips writing the JSON artifacts
        (tests).
        """
        if df.empty:
            raise ValueError("fit() requires a non-empty training dataframe")
        missing = [c for c in RAW_INPUT_COLS + HISTORY_COLS if c not in df.columns]
        if missing:
            raise ValueError(
                f"fit() missing columns {missing}; run attach_history_batch() first"
            )
        pct = self.cfg["outliers"]["amount_winsor_pct"]
        self.amount_cap_ = float(
            pd.to_numeric(df["amount_npr"], errors="coerce").quantile(pct)
        )
        derived = self.derive(df)
        self.fit_stats_ = compute_feature_stats(derived, self.feature_columns())
        log_stats(self.fit_stats_, "velocity fit")
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
        """Derive features for ``df`` and upsert them to the output table.

        ``df`` must carry the history columns (batch) — for single-transaction
        inference use :meth:`transform_one`, which fetches history from
        Redis/Postgres itself. Emits drift warnings when transform-time stats
        stray from fit-time stats.
        """
        started = datetime.now(timezone.utc)
        derived = self.derive(df)
        stats = compute_feature_stats(derived, self.feature_columns())
        if len(df) >= self.cfg["drift"]["min_rows"]:
            log_stats(stats, "velocity transform")
            if self.fit_stats_:
                warn_on_drift(self.fit_stats_, stats, self.cfg)
        else:
            logger.debug("velocity transform: %s rows, source=%s", len(df), source)
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

        Hot path: Redis sorted-set counters + cached nightly baseline.
        Degraded path (Redis down / cache miss): indexed Postgres queries.
        Never raises on Redis unavailability — logs a warning and falls back.
        """
        ts = pd.Timestamp(txn["timestamp"])
        windows, win_source = self._windows_realtime(txn, ts, conn)
        baseline, base_source = self._baseline_realtime(txn["account_id"], ts, conn)
        row = {
            "txn_id": txn["txn_id"],
            "account_id": txn["account_id"],
            "timestamp": ts,
            "amount_npr": float(txn["amount_npr"]),
            **windows,
            **baseline,
        }
        out = self.transform(
            pd.DataFrame([row]),
            conn=conn,
            write_to_db=write_to_db,
            source=f"realtime:{win_source}/{base_source}",
        )
        return out.iloc[0].to_dict()

    # ------------------------------------------------------------------ #
    # realtime helpers                                                     #
    # ------------------------------------------------------------------ #

    @property
    def store(self) -> VelocityStateStore:
        if self._store is None:
            self._store = VelocityStateStore(cfg=self.cfg)
        return self._store

    def _windows_realtime(
        self,
        txn: Mapping[str, Any],
        ts: pd.Timestamp,
        conn: psycopg2.extensions.connection | None,
    ) -> tuple[dict[str, float], str]:
        """Window counts for one txn: Redis first, Postgres fallback."""
        try:
            counts = self.store.record_and_count(
                txn["account_id"], txn["txn_id"], int(ts.value // 1_000_000),
                float(txn["amount_npr"]),
            )
            return counts, "redis"
        except RedisUnavailable as exc:
            logger.warning(
                "Redis unavailable for %s — falling back to Postgres (%s)",
                txn["txn_id"], exc,
            )
        if conn is None:
            raise RuntimeError(
                "Redis is down and no Postgres connection was provided; "
                "cannot compute velocity windows"
            )
        # transactions_raw (not velocity_snapshots) is the fallback: the
        # snapshot table's counts are empirically inconsistent (non-monotone
        # windows), while this indexed per-account query is correct and fast.
        count_exprs = ", ".join(
            f"count(*) FILTER (WHERE \"timestamp\" > %(ts)s - INTERVAL '{secs} seconds') "
            f"AS {name}"
            for name, secs in self.windows_s.items()
        )
        amount_exprs = ", ".join(
            f"coalesce(sum(amount_npr) FILTER "
            f"(WHERE \"timestamp\" > %(ts)s - INTERVAL '{secs} seconds'), 0) AS {name}"
            for name, secs in self.amount_windows_s.items()
        )
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT {count_exprs}, {amount_exprs} FROM transactions_raw '
                'WHERE account_id = %(acc)s AND "timestamp" <= %(ts)s '
                "AND \"timestamp\" > %(ts)s - INTERVAL '8 days'",
                {"acc": txn["account_id"], "ts": ts.to_pydatetime()},
            )
            row = cur.fetchone()
            names = [d.name for d in cur.description]
        windows = dict(zip(names, (float(v) for v in row)))
        # the current (not yet persisted) transaction counts toward every window
        for name in self.windows_s:
            windows[name] += 1
        for name in self.amount_windows_s:
            windows[name] += float(txn["amount_npr"])
        return windows, "pg_fallback"

    def _baseline_realtime(
        self,
        account_id: str,
        ts: pd.Timestamp,
        conn: psycopg2.extensions.connection | None,
    ) -> tuple[dict[str, float | None], str]:
        """30d baseline: Redis hash -> account_baseline_daily -> cold start."""
        try:
            cached = self.store.get_baseline(account_id)
        except RedisUnavailable:
            cached = None
        if cached is not None and "avg_txn_amount_30d_npr" in cached:
            return (
                {
                    "avg_txn_amount_30d_npr": cached["avg_txn_amount_30d_npr"],
                    "std_txn_amount_30d_npr": cached.get("std_txn_amount_30d_npr"),
                    "n_txn_30d_prior": cached.get("n_txn_30d", 0.0),
                },
                "redis",
            )
        if conn is not None:
            db.ensure_table(conn, db.BASELINE_TABLE_DDL)  # tolerate first boot
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT avg_txn_amount_30d_npr, std_txn_amount_30d_npr, n_txn_30d
                       FROM account_baseline_daily
                       WHERE account_id = %s AND baseline_date <= %s
                       ORDER BY baseline_date DESC LIMIT 1""",
                    (account_id, ts.date()),
                )
                row = cur.fetchone()
            if row is not None:
                return (
                    {
                        "avg_txn_amount_30d_npr": row[0],
                        "std_txn_amount_30d_npr": row[1],
                        "n_txn_30d_prior": float(row[2] or 0),
                    },
                    "pg_baseline",
                )
        # no baseline anywhere: treat as cold start (derive() zeroes the z-score)
        return (
            {
                "avg_txn_amount_30d_npr": None,
                "std_txn_amount_30d_npr": None,
                "n_txn_30d_prior": 0.0,
            },
            "cold_start",
        )

    # ------------------------------------------------------------------ #
    # schema / artifacts                                                   #
    # ------------------------------------------------------------------ #

    def feature_columns(self) -> list[str]:
        """Ordered engineered columns written to the output table."""
        self._check_fitted()
        return list(HISTORY_COLS) + list(DERIVED_COLS)

    def table_ddl(self) -> str:
        """Idempotent DDL for the velocity output table (FK to transactions_raw)."""
        int_cols = {
            *self.windows_s, "n_txn_30d_prior", "night_flag", "is_cold_start",
            "night_burst_interaction",
        }
        col_lines = []
        for c in self.feature_columns():
            sql_type = "INTEGER" if c in int_cols else "DOUBLE PRECISION"
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
        if self.amount_cap_ is None:
            raise RuntimeError(
                "VelocityFeatureEngineer is not fitted; call fit() or load_artifacts()"
            )

    def save_artifacts(self, path: str | Path | None = None) -> Path:
        """Persist fitted parameters as JSON for the inference service."""
        ARTIFACTS_DIR.mkdir(exist_ok=True)
        path = Path(path or ARTIFACTS_DIR / "velocity_fit.json")
        path.write_text(
            json.dumps(
                {"amount_cap": self.amount_cap_, "fit_stats": self.fit_stats_},
                indent=2,
            )
        )
        return path

    @classmethod
    def load_artifacts(
        cls, path: str | Path | None = None, cfg: dict[str, Any] | None = None
    ) -> "VelocityFeatureEngineer":
        """Rebuild a fitted engineer from :meth:`save_artifacts` output."""
        path = Path(path or ARTIFACTS_DIR / "velocity_fit.json")
        blob = json.loads(path.read_text())
        eng = cls(cfg=cfg)
        eng.amount_cap_ = blob["amount_cap"]
        eng.fit_stats_ = blob.get("fit_stats")
        return eng
