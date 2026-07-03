"""GeoFeatureEngineer — leakage-safe geolocation features (§5).

The pre-computed ``prev_txn_km`` / ``prev_txn_time_delta_min`` columns in
``geo_events`` are NOT trusted: recomputing the deltas from each account's
actual event history shows they match in 0% of sampled rows (median error
~8 days), and the shipped ``impossible_travel`` flag misses rows whose
implied speed exceeds 300,000 km/h. Everything here is therefore recomputed
from first principles using only strictly-prior events per account:

- ``prev_txn_km_recomputed`` — haversine distance to the account's previous
  geo event (``LAG`` over ``(account_id ORDER BY timestamp)``)
- ``implied_travel_speed_kmh`` — recomputed km over recomputed minutes,
  clipped for numeric stability; the flag uses the *unclipped* value
- ``impossible_travel_recomputed`` — speed > 900 km/h AND hop > 50 km (the
  km floor suppresses IP-geolocation jitter); the shipped flag is kept as
  ``impossible_travel_reported`` with a ``travel_flag_mismatch`` audit column
- ``distance_z_score`` — ``km_from_home_district`` against the account's OWN
  prior distribution (expanding window in batch; nightly baseline in
  real time), falling back to the global training distribution for accounts
  with fewer than ``geo.min_prior_events`` events
- ``isp_risk_encoding`` — frequency encoding of ``ip_isp`` learned on
  training data only. Target encoding is deliberately NOT used: the leakage
  policy forbids any feature derived from ``is_fraud``. Unseen ISPs at
  inference encode as 0.0 ("never seen" = maximally rare).
- ``geo_risk_composite`` — weighted sum of is_tor / impossible_travel /
  is_datacenter / is_vpn (weights + rationale in feature_config.yaml)
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

EARTH_RADIUS_KM = 6371.0088

RAW_INPUT_COLS = (
    "txn_id", "account_id", "timestamp", "latitude", "longitude",
    "ip_country", "ip_isp", "is_vpn", "is_tor", "is_datacenter",
    "km_from_home_district", "impossible_travel_reported",
)
HISTORY_COLS = (
    "prev_latitude", "prev_longitude", "prev_timestamp",
    "km_home_prior_avg", "km_home_prior_std", "n_geo_prior",
)
DERIVED_COLS = (
    "prev_txn_km_recomputed", "prev_txn_time_delta_min_recomputed",
    "implied_travel_speed_kmh", "impossible_travel_recomputed",
    "travel_flag_mismatch", "geo_risk_composite", "distance_z_score",
    "isp_risk_encoding", "is_foreign_ip",
)


def haversine_km(
    lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray
) -> np.ndarray:
    """Great-circle distance in km between two (lat, lon) arrays, in degrees."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlam = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2.0) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlam / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


class GeoFeatureEngineer:
    """Fit/transform geolocation feature builder.

    ``fit`` learns (training data only): the ``ip_isp`` frequency table, the
    global ``km_from_home_district`` mean/std (fallback for thin-history
    accounts), the distance winsor cap, and fit-time stats for drift checks.
    """

    TABLE = load_config()["pipeline"]["tables"]["geo"]

    def __init__(
        self,
        cfg: dict[str, Any] | None = None,
        state_store: VelocityStateStore | None = None,
    ) -> None:
        self.cfg = cfg or load_config()
        self._store = state_store
        # learned at fit()
        self.isp_freq_: dict[str, float] | None = None
        self.global_km_home_mean_: float | None = None
        self.global_km_home_std_: float | None = None
        self.distance_cap_: float | None = None
        self.fit_stats_: StatsDict | None = None

    # ------------------------------------------------------------------ #
    # history features: batch SQL                                          #
    # ------------------------------------------------------------------ #

    HISTORY_SQL = """
    SELECT txn_id, account_id, "timestamp", latitude, longitude, ip_country,
        ip_isp, is_vpn, is_tor, is_datacenter, km_from_home_district,
        impossible_travel AS impossible_travel_reported,
        lag(latitude) OVER w AS prev_latitude,
        lag(longitude) OVER w AS prev_longitude,
        lag("timestamp") OVER w AS prev_timestamp,
        avg(km_from_home_district) OVER wprior AS km_home_prior_avg,
        stddev_samp(km_from_home_district) OVER wprior AS km_home_prior_std,
        count(*) OVER wprior AS n_geo_prior
    FROM geo_events
    WINDOW w AS (PARTITION BY account_id ORDER BY "timestamp", txn_id),
           wprior AS (PARTITION BY account_id ORDER BY "timestamp", txn_id
                      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
    """

    def attach_history_batch(
        self,
        conn: psycopg2.extensions.connection,
        start: str | datetime,
        end: str | datetime,
    ) -> Iterator[pd.DataFrame]:
        """Yield chunks of geo events in [start, end) with prior-only history.

        ``LAG``/expanding windows scan the full per-account history, so only
        strictly-prior events ever feed a row's features — no lookahead.
        """
        sql = (
            f"SELECT * FROM ({self.HISTORY_SQL}) h "
            'WHERE "timestamp" >= %s AND "timestamp" < %s'
        )
        chunk_rows = self.cfg["pipeline"]["batch_chunk_rows"]
        with conn.cursor(name=f"geo_hist_{id(self)}") as cur:
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
        """Compute the §5 geo features from raw + history columns.

        Pure and vectorized; safe on a single row. First-ever events for an
        account get speed/distance deltas of 0 (no prior evidence, not an
        anomaly) and lean on the global distance distribution.
        """
        self._check_fitted()
        missing = [c for c in RAW_INPUT_COLS + HISTORY_COLS if c not in df.columns]
        if missing:
            raise ValueError(f"derive() missing columns: {missing}")
        g = self.cfg["geo"]
        out = df.copy()

        lat = pd.to_numeric(out["latitude"], errors="coerce").to_numpy(dtype=float)
        lon = pd.to_numeric(out["longitude"], errors="coerce").to_numpy(dtype=float)
        plat = pd.to_numeric(out["prev_latitude"], errors="coerce").to_numpy(dtype=float)
        plon = pd.to_numeric(out["prev_longitude"], errors="coerce").to_numpy(dtype=float)
        has_prev = ~(np.isnan(plat) | np.isnan(plon) | np.isnan(lat) | np.isnan(lon))

        km = np.zeros(len(out))
        km[has_prev] = haversine_km(
            lat[has_prev], lon[has_prev], plat[has_prev], plon[has_prev]
        )
        km = np.minimum(km, g["prev_txn_km_cap"])
        out["prev_txn_km_recomputed"] = km

        ts = pd.to_datetime(out["timestamp"])
        prev_ts = pd.to_datetime(out["prev_timestamp"], errors="coerce")
        delta_min = (ts - prev_ts).dt.total_seconds().to_numpy(dtype=float) / 60.0
        delta_min = np.where(has_prev, delta_min, np.nan)
        out["prev_txn_time_delta_min_recomputed"] = np.where(
            np.isnan(delta_min), 0.0, np.maximum(delta_min, 0.0)
        )

        # speed only when the gap is long enough to be physically meaningful
        valid = has_prev & (delta_min >= g["min_time_delta_min"])
        speed_raw = np.zeros(len(out))
        speed_raw[valid] = km[valid] / (delta_min[valid] / 60.0)
        out["implied_travel_speed_kmh"] = np.minimum(
            speed_raw, g["implied_speed_clip_kmh"]
        )

        # flag from the UNCLIPPED speed; km floor filters IP-geolocation jitter
        impossible = (
            valid
            & (speed_raw > g["impossible_speed_kmh"])
            & (km > g["impossible_min_km"])
        )
        out["impossible_travel_recomputed"] = impossible.astype(np.int16)
        reported = (
            out["impossible_travel_reported"]
            .map({True: 1, False: 0, "True": 1, "False": 0})
            .fillna(0)
            .astype(np.int16)
        )
        out["travel_flag_mismatch"] = (
            out["impossible_travel_recomputed"] != reported
        ).astype(np.int16)

        w = g["risk_weights"]
        flags = {
            name: pd.Series(out[col]).map(
                {True: 1.0, False: 0.0, "True": 1.0, "False": 0.0}
            ).fillna(0.0).to_numpy()
            for name, col in (
                ("is_tor", "is_tor"),
                ("is_datacenter", "is_datacenter"),
                ("is_vpn", "is_vpn"),
            )
        }
        out["geo_risk_composite"] = (
            w["is_tor"] * flags["is_tor"]
            + w["impossible_travel"] * out["impossible_travel_recomputed"].to_numpy()
            + w["is_datacenter"] * flags["is_datacenter"]
            + w["is_vpn"] * flags["is_vpn"]
        )

        km_home = (
            pd.to_numeric(out["km_from_home_district"], errors="coerce")
            .clip(upper=self.distance_cap_)
        )
        prior_avg = pd.to_numeric(out["km_home_prior_avg"], errors="coerce")
        prior_std = pd.to_numeric(out["km_home_prior_std"], errors="coerce")
        n_prior = pd.to_numeric(out["n_geo_prior"], errors="coerce").fillna(0)
        thin = (n_prior < g["min_prior_events"]) | prior_avg.isna() | prior_std.isna()
        mean = prior_avg.where(~thin, self.global_km_home_mean_)
        std = prior_std.where(~thin, self.global_km_home_std_).clip(
            lower=g["distance_std_floor_km"]
        )
        z = ((km_home - mean) / std).clip(-g["distance_z_clip"], g["distance_z_clip"])
        out["distance_z_score"] = z.fillna(0.0)

        out["isp_risk_encoding"] = (
            out["ip_isp"]
            .astype(str)
            .map(self.isp_freq_)
            .fillna(g["isp_encoding"]["unseen_value"])
        )
        out["is_foreign_ip"] = (
            out["ip_country"].astype(str) != g["home_country"]
        ).astype(np.int16)

        return out

    # ------------------------------------------------------------------ #
    # fit / transform                                                      #
    # ------------------------------------------------------------------ #

    def fit(self, df: pd.DataFrame, save: bool = True) -> "GeoFeatureEngineer":
        """Learn training-only parameters (time-based training window ONLY).

        ``save=False`` skips writing the JSON artifacts (tests).
        """
        if df.empty:
            raise ValueError("fit() requires a non-empty training dataframe")
        missing = [c for c in RAW_INPUT_COLS + HISTORY_COLS if c not in df.columns]
        if missing:
            raise ValueError(
                f"fit() missing columns {missing}; run attach_history_batch() first"
            )
        g = self.cfg["geo"]
        freq = df["ip_isp"].astype(str).value_counts(normalize=True)
        self.isp_freq_ = {k: float(v) for k, v in freq.items()}
        km_home = pd.to_numeric(df["km_from_home_district"], errors="coerce")
        self.distance_cap_ = float(
            km_home.quantile(self.cfg["outliers"]["distance_winsor_pct"])
        )
        km_home_w = km_home.clip(upper=self.distance_cap_)
        self.global_km_home_mean_ = float(km_home_w.mean())
        self.global_km_home_std_ = float(
            max(km_home_w.std(), g["distance_std_floor_km"])
        )
        derived = self.derive(df)
        self.fit_stats_ = compute_feature_stats(derived, self.feature_columns())
        log_stats(self.fit_stats_, "geo fit")
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
        """Derive geo features and upsert to ``geo_features_engineered``."""
        started = datetime.now(timezone.utc)
        derived = self.derive(df)
        stats = compute_feature_stats(derived, self.feature_columns())
        if len(df) >= self.cfg["drift"]["min_rows"]:
            log_stats(stats, "geo transform")
            if self.fit_stats_:
                warn_on_drift(self.fit_stats_, stats, self.cfg)
        else:
            logger.debug("geo transform: %s rows, source=%s", len(df), source)
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
        event: Mapping[str, Any],
        conn: psycopg2.extensions.connection | None = None,
        write_to_db: bool = True,
    ) -> dict[str, Any]:
        """Score a single new geo event at inference time.

        The previous event comes from an indexed ``geo_events`` lookup; the
        account's distance baseline comes from the nightly Redis hash, then
        ``account_baseline_daily``, then the global training distribution.
        """
        ts = pd.Timestamp(event["timestamp"])
        prev = self._prev_event_realtime(event["account_id"], ts, conn)
        baseline = self._distance_baseline_realtime(event["account_id"], ts, conn)
        row = {
            "txn_id": event["txn_id"],
            "account_id": event["account_id"],
            "timestamp": ts,
            "latitude": event.get("latitude"),
            "longitude": event.get("longitude"),
            "ip_country": event.get("ip_country"),
            "ip_isp": event.get("ip_isp"),
            "is_vpn": event.get("is_vpn", False),
            "is_tor": event.get("is_tor", False),
            "is_datacenter": event.get("is_datacenter", False),
            "km_from_home_district": event.get("km_from_home_district"),
            "impossible_travel_reported": event.get("impossible_travel", False),
            **prev,
            **baseline,
        }
        out = self.transform(
            pd.DataFrame([row]), conn=conn, write_to_db=write_to_db,
            source="realtime",
        )
        return out.iloc[0].to_dict()

    def _prev_event_realtime(
        self,
        account_id: str,
        ts: pd.Timestamp,
        conn: psycopg2.extensions.connection | None,
    ) -> dict[str, Any]:
        """Latest strictly-earlier geo event for the account (or none)."""
        empty = {"prev_latitude": None, "prev_longitude": None, "prev_timestamp": None}
        if conn is None:
            return empty
        with conn.cursor() as cur:
            cur.execute(
                """SELECT latitude, longitude, "timestamp" FROM geo_events
                   WHERE account_id = %s AND "timestamp" < %s
                   ORDER BY "timestamp" DESC LIMIT 1""",
                (account_id, ts.to_pydatetime()),
            )
            row = cur.fetchone()
        if row is None:
            return empty
        return {
            "prev_latitude": row[0],
            "prev_longitude": row[1],
            "prev_timestamp": row[2],
        }

    def _distance_baseline_realtime(
        self,
        account_id: str,
        ts: pd.Timestamp,
        conn: psycopg2.extensions.connection | None,
    ) -> dict[str, Any]:
        """Distance baseline: Redis hash -> account_baseline_daily -> global."""
        try:
            cached = self.store.get_baseline(account_id)
        except RedisUnavailable:
            cached = None
        if cached is not None and "avg_km_from_home_90d" in cached:
            return {
                "km_home_prior_avg": cached["avg_km_from_home_90d"],
                "km_home_prior_std": cached.get("std_km_from_home_90d"),
                "n_geo_prior": cached.get("n_geo_90d", 0.0),
            }
        if conn is not None:
            db.ensure_table(conn, db.BASELINE_TABLE_DDL)  # tolerate first boot
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT avg_km_from_home_90d, std_km_from_home_90d, n_geo_90d
                       FROM account_baseline_daily
                       WHERE account_id = %s AND baseline_date <= %s
                       ORDER BY baseline_date DESC LIMIT 1""",
                    (account_id, ts.date()),
                )
                row = cur.fetchone()
            if row is not None:
                return {
                    "km_home_prior_avg": row[0],
                    "km_home_prior_std": row[1],
                    "n_geo_prior": float(row[2] or 0),
                }
        # derive() falls back to the fitted global distribution
        return {"km_home_prior_avg": None, "km_home_prior_std": None, "n_geo_prior": 0.0}

    @property
    def store(self) -> VelocityStateStore:
        if self._store is None:
            self._store = VelocityStateStore(cfg=self.cfg)
        return self._store

    # ------------------------------------------------------------------ #
    # schema / artifacts                                                   #
    # ------------------------------------------------------------------ #

    def feature_columns(self) -> list[str]:
        """Ordered engineered columns written to the output table."""
        return list(DERIVED_COLS)

    def table_ddl(self) -> str:
        """Idempotent DDL for the geo output table (FK to transactions_raw)."""
        int_cols = {"impossible_travel_recomputed", "travel_flag_mismatch", "is_foreign_ip"}
        col_lines = ",\n".join(
            f'    "{c}" {"INTEGER" if c in int_cols else "DOUBLE PRECISION"}'
            for c in self.feature_columns()
        )
        return f"""
CREATE TABLE IF NOT EXISTS {self.TABLE} (
    txn_id TEXT PRIMARY KEY REFERENCES transactions_raw(txn_id),
    account_id TEXT NOT NULL,
{col_lines},
    source TEXT,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

    def _check_fitted(self) -> None:
        if self.isp_freq_ is None or self.distance_cap_ is None:
            raise RuntimeError(
                "GeoFeatureEngineer is not fitted; call fit() or load_artifacts()"
            )

    def save_artifacts(self, path: str | Path | None = None) -> Path:
        """Persist fitted parameters as JSON for the inference service."""
        ARTIFACTS_DIR.mkdir(exist_ok=True)
        path = Path(path or ARTIFACTS_DIR / "geo_fit.json")
        path.write_text(
            json.dumps(
                {
                    "isp_freq": self.isp_freq_,
                    "global_km_home_mean": self.global_km_home_mean_,
                    "global_km_home_std": self.global_km_home_std_,
                    "distance_cap": self.distance_cap_,
                    "fit_stats": self.fit_stats_,
                },
                indent=2,
            )
        )
        return path

    @classmethod
    def load_artifacts(
        cls, path: str | Path | None = None, cfg: dict[str, Any] | None = None
    ) -> "GeoFeatureEngineer":
        """Rebuild a fitted engineer from :meth:`save_artifacts` output."""
        path = Path(path or ARTIFACTS_DIR / "geo_fit.json")
        blob = json.loads(path.read_text())
        eng = cls(cfg=cfg)
        eng.isp_freq_ = blob["isp_freq"]
        eng.global_km_home_mean_ = blob["global_km_home_mean"]
        eng.global_km_home_std_ = blob["global_km_home_std"]
        eng.distance_cap_ = blob["distance_cap"]
        eng.fit_stats_ = blob.get("fit_stats")
        return eng
