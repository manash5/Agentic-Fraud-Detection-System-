"""Nightly batch job: 30d/90d account baselines -> Postgres + Redis cache.

The cold path of the §3 hybrid pattern. For an as-of date ``D`` it computes,
per account, trailing stats over windows that END at midnight of ``D`` (so a
baseline row never sees any transaction from its own day — no lookahead):

- ``avg_txn_amount_30d_npr`` / ``std_txn_amount_30d_npr`` / ``n_txn_30d``
  from ``transactions_raw`` over [D-30d, D)
- ``avg_km_from_home_90d`` / ``std_km_from_home_90d`` / ``n_geo_90d``
  from ``geo_events`` over [D-90d, D)

Results are upserted into ``account_baseline_daily`` (source of truth), then
mirrored into Redis ``account_baseline:{account_id}`` hashes with a 26h TTL —
the real-time z-score reads the live amount against this cached baseline
instead of recomputing 30-day stats per transaction. Redis being down only
skips the cache refresh; Postgres is always written first.

The same refresh also feeds the paper §IV-C-1 Velocity Agent
(``agents.velocity_agent``): the baseline hash additionally carries the
``hist_*`` fields it reads (hist_amount_avg/std, hist_txn_count_*_mean,
observation_count), and ``user_type_dist:{account_id}`` hashes get each
account's trailing-30d txn_type shares (txn_type stays out of the shared
feature tables — this Redis structure is its only consumer).

Run: ``uv run python -m feature_engineering.nightly_baseline_job [--as-of 2026-06-01]``
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timezone

import pandas as pd

from agents.velocity_agent import write_type_dist
from feature_engineering import db
from feature_engineering.config import load_config
from feature_engineering.redis_client import RedisUnavailable, VelocityStateStore

logger = logging.getLogger(__name__)

TYPE_DIST_SQL = """
SELECT account_id, txn_type, count(*) AS n
FROM transactions_raw
WHERE "timestamp" >= %(asof)s::date - make_interval(days => %(amt_days)s)
  AND "timestamp" < %(asof)s::date
  AND txn_type IS NOT NULL
GROUP BY account_id, txn_type
"""

BASELINE_SQL = """
WITH amt AS (
    SELECT account_id,
           avg(amount_npr) AS avg_txn_amount_30d_npr,
           stddev_samp(amount_npr) AS std_txn_amount_30d_npr,
           count(*) AS n_txn_30d
    FROM transactions_raw
    WHERE "timestamp" >= %(asof)s::date - make_interval(days => %(amt_days)s)
      AND "timestamp" < %(asof)s::date
    GROUP BY account_id
),
geo AS (
    SELECT account_id,
           avg(km_from_home_district) AS avg_km_from_home_90d,
           stddev_samp(km_from_home_district) AS std_km_from_home_90d,
           count(*) AS n_geo_90d
    FROM geo_events
    WHERE "timestamp" >= %(asof)s::date - make_interval(days => %(geo_days)s)
      AND "timestamp" < %(asof)s::date
    GROUP BY account_id
)
SELECT coalesce(amt.account_id, geo.account_id) AS account_id,
       amt.avg_txn_amount_30d_npr, amt.std_txn_amount_30d_npr,
       coalesce(amt.n_txn_30d, 0) AS n_txn_30d,
       geo.avg_km_from_home_90d, geo.std_km_from_home_90d,
       coalesce(geo.n_geo_90d, 0) AS n_geo_90d
FROM amt FULL OUTER JOIN geo USING (account_id)
"""


def compute_baselines(conn, as_of: date) -> pd.DataFrame:
    """Trailing per-account stats for windows ending at midnight of ``as_of``."""
    cfg = load_config()
    with conn.cursor() as cur:
        cur.execute(
            BASELINE_SQL,
            {
                "asof": as_of,
                "amt_days": cfg["velocity"]["baseline_window_days"],
                "geo_days": cfg["geo"]["distance_baseline_window_days"],
            },
        )
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
    df = pd.DataFrame(rows, columns=cols)
    df.insert(1, "baseline_date", as_of)
    return df


def compute_type_dists(conn, as_of: date) -> dict[str, dict[str, float]]:
    """Per-account txn_type shares over the trailing baseline window."""
    cfg = load_config()
    with conn.cursor() as cur:
        cur.execute(
            TYPE_DIST_SQL,
            {"asof": as_of, "amt_days": cfg["velocity"]["baseline_window_days"]},
        )
        rows = cur.fetchall()
    counts: dict[str, dict[str, int]] = {}
    for account_id, txn_type, n in rows:
        counts.setdefault(account_id, {})[txn_type] = int(n)
    return {
        account_id: {t: n / sum(by_type.values()) for t, n in by_type.items()}
        for account_id, by_type in counts.items()
    }


def refresh_type_dists(
    dists: dict[str, dict[str, float]], store: VelocityStateStore | None = None
) -> int:
    """Mirror the txn_type distributions into Redis; returns accounts written."""
    store = store or VelocityStateStore()
    for account_id, dist in dists.items():
        write_type_dist(account_id, dist, store.client, cfg=store.cfg)
    return len(dists)


def refresh_redis(df: pd.DataFrame, store: VelocityStateStore | None = None) -> int:
    """Mirror the baseline rows into Redis hashes; returns accounts written."""
    store = store or VelocityStateStore()
    cfg = store.cfg
    window_days = cfg["velocity"]["baseline_window_days"]
    agent_windows_s = cfg["velocity_agent"]["windows_s"]
    written = 0
    for row in df.itertuples(index=False):
        n_txn = float(row.n_txn_30d or 0)
        baseline = {
            "avg_txn_amount_30d_npr": row.avg_txn_amount_30d_npr,
            "std_txn_amount_30d_npr": row.std_txn_amount_30d_npr,
            "n_txn_30d": row.n_txn_30d,
            "avg_km_from_home_90d": row.avg_km_from_home_90d,
            "std_km_from_home_90d": row.std_km_from_home_90d,
            "n_geo_90d": row.n_geo_90d,
            "baseline_date": str(row.baseline_date),
            # Paper §IV-C-1 Velocity Agent fields (agents.velocity_agent).
            # hist_txn_count_*_mean is the trailing-window average txns per
            # sliding window: n_txn / (number of windows in the period).
            "hist_amount_avg": row.avg_txn_amount_30d_npr,
            "hist_amount_std": row.std_txn_amount_30d_npr,
            "observation_count": int(n_txn),
        }
        for window, w_s in agent_windows_s.items():
            baseline[f"hist_txn_{window}_mean"] = n_txn * w_s / (window_days * 86400.0)
        store.set_baseline(
            row.account_id,
            # pd.notna also drops NaN (an account can have geo events but no
            # txns in the window), which would otherwise poison float parses.
            {k: v for k, v in baseline.items() if pd.notna(v)},
        )
        written += 1
    return written


def run(as_of: date, skip_redis: bool = False) -> None:
    """Compute baselines for ``as_of``, upsert to Postgres, refresh Redis."""
    started = datetime.now(timezone.utc)
    with db.get_conn() as conn:
        db.ensure_table(conn, db.BASELINE_TABLE_DDL)
        df = compute_baselines(conn, as_of)
        n = db.bulk_upsert(
            conn, "account_baseline_daily", df, ("account_id", "baseline_date")
        )
        db.record_run(
            conn, "account_baseline_daily", n, started, notes=f"as_of={as_of}"
        )
        type_dists = compute_type_dists(conn, as_of)
    logger.info("account_baseline_daily: upserted %s rows for %s", n, as_of)
    if skip_redis:
        return
    try:
        store = VelocityStateStore()
        store.warn_if_bad_eviction_policy()
        cached = refresh_redis(df, store)
        logger.info("Redis baseline cache refreshed for %s accounts", cached)
        cached = refresh_type_dists(type_dists, store)
        logger.info("Redis txn_type distributions refreshed for %s accounts", cached)
    except RedisUnavailable as exc:
        # Postgres is already written — the cache will self-heal on the next run.
        logger.warning("Redis refresh skipped (unavailable): %s", exc)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--as-of",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=date.today(),
        help="baseline date (windows end at this date's midnight); default today",
    )
    parser.add_argument("--skip-redis", action="store_true")
    args = parser.parse_args()
    run(args.as_of, skip_redis=args.skip_redis)


if __name__ == "__main__":
    main()
