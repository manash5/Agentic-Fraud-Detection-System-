"""Build models/behavior_score_calibration.json — the percentile reference
grids the scorers use to put all three model outputs on one [0,1] scale.

No retraining happens here; this only *scores* existing data with the
already-trained models:

  - isolation_forest: quantiles of the 2M training anomaly scores the
    notebook saved to datasets_processed/transactions_scored_isoforest.csv.
  - xgboost: quantiles of the 80k validation P(fraud) the notebook saved to
    datasets_processed/val_scored_xgboost.csv.
  - lstm: no scored reference was saved at train time, so this script scores
    a random sample of eligible windows (accounts with >= lstm_min_history
    txns) through the real input builder against Postgres and takes the
    quantiles of those probabilities. Sample size + seed are recorded in the
    output for provenance.

Run once from ``backend/`` (requires the reference tables loaded)::

    uv run python -m behavior_agent.build_calibration
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import asyncpg
import numpy as np
import pandas as pd

from behavior_agent.artifacts import load_bundle
from behavior_agent.config import BACKEND_DIR, load_config, model_path, pg_connect_kwargs
from behavior_agent.input_builders import MissingInputError, build_lstm_input
from behavior_agent.scorers import run_lstm

N_QUANTILES = 1001
LSTM_SAMPLE_SIZE = 1000
SEED = 42

_LSTM_SAMPLE_SQL = """
WITH eligible AS (
    SELECT account_id
    FROM transactions_raw
    GROUP BY account_id
    HAVING count(*) >= $1
)
SELECT t.account_id, t.txn_id
FROM transactions_raw t
JOIN eligible e USING (account_id)
ORDER BY md5(t.txn_id)          -- deterministic pseudo-random order
LIMIT $2
"""


def _grid(scores: np.ndarray) -> list[float]:
    return np.quantile(scores, np.linspace(0.0, 1.0, N_QUANTILES)).tolist()


async def main() -> None:
    cfg = load_config()
    bundle = load_bundle(cfg, require_calibration=False)
    out: dict = {"built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "n_quantiles": N_QUANTILES, "models": {}}

    iso_csv = BACKEND_DIR / "datasets_processed" / "transactions_scored_isoforest.csv"
    iso_scores = pd.read_csv(iso_csv, usecols=["anomaly_score"])["anomaly_score"].values
    out["models"]["isolation_forest"] = {
        "quantiles": _grid(iso_scores),
        "reference": f"{iso_csv.name} ({len(iso_scores):,} training anomaly scores)"}
    print(f"[isolation_forest] grid from {len(iso_scores):,} training scores")

    xgb_csv = BACKEND_DIR / "datasets_processed" / "val_scored_xgboost.csv"
    xgb_scores = pd.read_csv(xgb_csv, usecols=["fraud_proba"])["fraud_proba"].values
    out["models"]["xgboost"] = {
        "quantiles": _grid(xgb_scores),
        "reference": f"{xgb_csv.name} ({len(xgb_scores):,} validation probabilities)"}
    print(f"[xgboost] grid from {len(xgb_scores):,} validation scores")

    min_hist = cfg["history"]["lstm_min_history"]
    conn = await asyncpg.connect(**pg_connect_kwargs(cfg["database"]["dsn"]))
    try:
        rows = await conn.fetch(_LSTM_SAMPLE_SQL, min_hist, LSTM_SAMPLE_SIZE)
        probs, failures = [], 0
        t0 = time.time()
        for r in rows:
            try:
                li = await build_lstm_input(r["account_id"], r["txn_id"], conn, bundle)
            except MissingInputError:
                failures += 1
                continue
            probs.append(run_lstm(li, bundle))
        print(f"[lstm] scored {len(probs)} sampled windows "
              f"({failures} skipped for missing inputs) in {time.time()-t0:.1f}s")
    finally:
        await conn.close()
    if len(probs) < 200:
        raise RuntimeError(
            f"only {len(probs)} LSTM reference scores — too few for a stable grid")
    out["models"]["lstm"] = {
        "quantiles": _grid(np.asarray(probs)),
        "reference": (f"{len(probs)} windows sampled from accounts with >= "
                      f"{min_hist} txns (md5(txn_id) order, limit {LSTM_SAMPLE_SIZE})")}

    path: Path = model_path(cfg, "calibration")
    with open(path, "w") as f:
        json.dump(out, f)
    print(f"[done] wrote {path}")


if __name__ == "__main__":
    asyncio.run(main())
