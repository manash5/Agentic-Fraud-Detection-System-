"""Batch orchestrator: fit on the training window, transform everything.

Flow (all dates from feature_config.yaml `split:` — TIME-based, never random):

1. VelocityFeatureEngineer / GeoFeatureEngineer fit on a uniform sample of
   the TRAINING window only (winsor caps, one-hot categories, ISP frequency
   table, global distance stats, fit-time drift stats).
2. transform() runs chunked over the full requested period; every chunk is
   bulk-upserted into its dedicated table and audited in
   feature_pipeline_runs.
3. The nightly baseline job is invoked for the day after the last
   transaction, so account_baseline_daily + the Redis cache are ready for
   real-time scoring.

Run: ``uv run python -m feature_engineering.run_batch_pipeline``
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd

from feature_engineering import db, nightly_baseline_job
from feature_engineering.config import load_config
from feature_engineering.geo_features import GeoFeatureEngineer
from feature_engineering.transaction_features import TransactionFeatureEngineer
from feature_engineering.velocity_features import VelocityFeatureEngineer

logger = logging.getLogger(__name__)


def _fit_sample(engineer, conn, train_start: str, train_end_excl: str) -> pd.DataFrame:
    """Uniform row sample of the training window with history columns."""
    cfg = load_config()
    target = cfg["pipeline"]["fit_sample_rows"]
    chunks: list[pd.DataFrame] = []
    total = 0
    for chunk in engineer.attach_history_batch(conn, train_start, train_end_excl):
        total += len(chunk)
        chunks.append(chunk)
    df = pd.concat(chunks, ignore_index=True)
    if len(df) > target:
        df = df.sample(n=target, random_state=42).reset_index(drop=True)
    logger.info("fit sample: %s of %s training rows", len(df), total)
    return df


def run(start: str, end_excl: str) -> None:
    cfg = load_config()
    split = cfg["split"]
    train_start = split["train_start"]
    train_end_excl = (
        pd.Timestamp(split["train_end"]) + pd.Timedelta(days=1)
    ).strftime("%Y-%m-%d")

    for name, engineer_cls in (
        ("transaction", TransactionFeatureEngineer),
        ("velocity", VelocityFeatureEngineer),
        ("geo", GeoFeatureEngineer),
    ):
        engineer = engineer_cls()
        with db.get_conn() as conn:
            logger.info("[%s] fitting on train window %s..%s", name, train_start, train_end_excl)
            engineer.fit(_fit_sample(engineer, conn, train_start, train_end_excl))
            logger.info("[%s] transforming %s..%s", name, start, end_excl)
            written = 0
            for chunk in engineer.attach_history_batch(conn, start, end_excl):
                out = engineer.transform(chunk, conn=conn, write_to_db=True)
                written += len(out)
                logger.info("[%s] %s rows written (total %s)", name, len(out), written)

    # baselines as of the day after the last processed transaction
    as_of = (pd.Timestamp(end_excl)).date()
    logger.info("running nightly baseline job as of %s", as_of)
    nightly_baseline_job.run(as_of)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=cfg["split"]["train_start"])
    parser.add_argument(
        "--end",
        default=(
            pd.Timestamp(cfg["split"]["validation_end"]) + pd.Timedelta(days=1)
        ).strftime("%Y-%m-%d"),
        help="exclusive end date",
    )
    args = parser.parse_args()
    run(args.start, args.end)


if __name__ == "__main__":
    main()
