"""Pre-handoff validation (§9) for the Synthesis agent.

1. Correlation matrix across the final engineered features (velocity + geo
   joined on txn_id); every pair with |r| > the configured threshold is
   printed for review.
2. Per-transaction latency benchmark of the real-time path
   (Redis read + Postgres fallback components) against the P95 budget.
3. Dumps N sample transactions with their full engineered feature vector to
   CSV for manual sanity-checking.

Run: ``uv run python -m feature_engineering.validate_features``
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from feature_engineering import db
from feature_engineering.config import ARTIFACTS_DIR, load_config
from feature_engineering.geo_features import GeoFeatureEngineer
from feature_engineering.transaction_features import TransactionFeatureEngineer
from feature_engineering.velocity_features import VelocityFeatureEngineer

logger = logging.getLogger(__name__)


def correlation_report(conn, threshold: float) -> pd.DataFrame:
    """|r| for every engineered feature pair above ``threshold``."""
    cfg = load_config()
    query = f"""
        SELECT v.*,
               tr.txn_hour, tr.txn_day_of_week, tr.txn_is_weekend, tr.amount_log,
               tr.response_code_is_success, tr.has_notes, tr.is_international,
               tr.currency_is_foreign,
               g.prev_txn_km_recomputed, g.prev_txn_time_delta_min_recomputed,
               g.implied_travel_speed_kmh, g.impossible_travel_recomputed,
               g.geo_risk_composite, g.distance_z_score, g.isp_risk_encoding,
               g.is_foreign_ip
        FROM (SELECT * FROM {cfg["pipeline"]["tables"]["velocity"]}
              TABLESAMPLE SYSTEM (10)) v
        JOIN {cfg["pipeline"]["tables"]["transaction"]} tr USING (txn_id)
        JOIN {cfg["pipeline"]["tables"]["geo"]} g USING (txn_id)
        LIMIT 200000
    """
    df = pd.read_sql(query, conn)
    numeric = df.select_dtypes(include=[np.number]).drop(
        columns=[c for c in ("n_txn_30d_prior",) if c not in df], errors="ignore"
    )
    corr = numeric.corr()
    pairs = []
    cols = corr.columns
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            r = corr.iloc[i, j]
            if abs(r) > threshold:
                pairs.append({"feature_a": cols[i], "feature_b": cols[j], "corr": round(float(r), 4)})
    report = pd.DataFrame(pairs).sort_values("corr", key=abs, ascending=False) if pairs else pd.DataFrame(columns=["feature_a", "feature_b", "corr"])
    return report


def latency_benchmark(conn, n: int = 200) -> dict[str, float]:
    """P50/P95/P99 (ms) of scoring one new txn through all three engineers."""
    txn_eng = TransactionFeatureEngineer.load_artifacts()
    vel = VelocityFeatureEngineer.load_artifacts()
    geo = GeoFeatureEngineer.load_artifacts()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT t.txn_id, t.account_id, t."timestamp", t.amount_npr, t.channel,
                      t.auth_method, t.currency, t.response_code, t.is_international,
                      t.notes, g.latitude, g.longitude, g.ip_country, g.ip_isp,
                      g.is_vpn, g.is_tor, g.is_datacenter, g.km_from_home_district,
                      g.impossible_travel
               FROM transactions_raw t JOIN geo_events g USING (txn_id)
               ORDER BY t."timestamp" DESC LIMIT %s""",
            (n,),
        )
        rows = cur.fetchall()
        names = [d.name for d in cur.description]
    timings = []
    for row in rows:
        txn = dict(zip(names, row))
        t0 = time.perf_counter()
        txn_eng.transform_one(
            {**txn, "txn_id": "BENCH-" + txn["txn_id"]}, conn=conn, write_to_db=False
        )
        vel.transform_one(
            {**txn, "txn_id": "BENCH-" + txn["txn_id"]}, conn=conn, write_to_db=False
        )
        geo.transform_one(
            {**txn, "txn_id": "BENCH-" + txn["txn_id"],
             "impossible_travel": txn["impossible_travel"]},
            conn=conn, write_to_db=False,
        )
        timings.append((time.perf_counter() - t0) * 1000)
    arr = np.asarray(timings)
    return {
        "n": len(arr),
        "p50_ms": float(np.percentile(arr, 50)),
        "p95_ms": float(np.percentile(arr, 95)),
        "p99_ms": float(np.percentile(arr, 99)),
        "max_ms": float(arr.max()),
    }


def sample_dump(conn, n: int, out_path: Path) -> pd.DataFrame:
    """N transactions with the full engineered feature vector, for eyeballing."""
    cfg = load_config()
    df = pd.read_sql(
        f"""SELECT t.txn_id, t."timestamp", t.account_id, t.amount_npr, t.channel,
                   tr.*, v.*, g.*
            FROM transactions_raw t
            JOIN {cfg["pipeline"]["tables"]["transaction"]} tr USING (txn_id)
            JOIN {cfg["pipeline"]["tables"]["velocity"]} v USING (txn_id)
            JOIN {cfg["pipeline"]["tables"]["geo"]} g USING (txn_id)
            ORDER BY md5(t.txn_id) LIMIT %s""",
        conn,
        params=(n,),
    )
    df = df.loc[:, ~df.columns.duplicated()]
    df.to_csv(out_path, index=False)
    return df


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench-n", type=int, default=200)
    args = parser.parse_args()

    ARTIFACTS_DIR.mkdir(exist_ok=True)
    with db.get_conn() as conn:
        threshold = cfg["validation"]["correlation_flag_threshold"]
        report = correlation_report(conn, threshold)
        print(f"\n=== feature pairs with |corr| > {threshold} ===")
        print(report.to_string(index=False) if not report.empty else "(none)")
        report.to_csv(ARTIFACTS_DIR / "correlation_flags.csv", index=False)

        stats = latency_benchmark(conn, args.bench_n)
        budget = cfg["validation"]["latency_budget_p95_ms"]
        verdict = "PASS" if stats["p95_ms"] < budget else "FAIL"
        print(
            f"\n=== per-txn latency (velocity+geo, n={stats['n']}) ===\n"
            f"p50={stats['p50_ms']:.1f}ms p95={stats['p95_ms']:.1f}ms "
            f"p99={stats['p99_ms']:.1f}ms max={stats['max_ms']:.1f}ms "
            f"-> {verdict} (budget p95 < {budget}ms)"
        )

        sample = sample_dump(
            conn, cfg["validation"]["sample_rows"], ARTIFACTS_DIR / "sample_features.csv"
        )
        print(f"\n=== {len(sample)} sample transactions -> {ARTIFACTS_DIR / 'sample_features.csv'} ===")


if __name__ == "__main__":
    main()
