"""Individual model scorers — each returns a risk score in [0,1] plus whether
the model actually contributed.

Raw outputs live on different scales (XGBoost predict_proba is threshold-
calibrated near 0.01, the Isolation Forest emits an unbounded anomaly score,
the LSTM sigmoid is inflated by pos_weight=53.5), so each scorer also reports
a **calibrated** score: the percentile rank of the raw score against that
model's reference distribution (built once by ``behavior_agent.
build_calibration`` from training/validation scores). The ensemble blends
calibrated scores by default; both are reported in the breakdown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from behavior_agent.artifacts import ModelBundle
from behavior_agent.input_builders import (
    LSTMInput,
    build_isolation_forest_input,
    build_lstm_input,
    build_xgboost_input,
)


@dataclass
class ModelScore:
    name: str
    contributed: bool
    raw_score: float | None = None         # model-native output
    calibrated_score: float | None = None  # percentile vs reference distribution
    detail: dict[str, Any] = field(default_factory=dict)


def percentile_calibrate(raw: float, grid: np.ndarray) -> float:
    """Fraction of the reference distribution strictly below ``raw``.

    ``grid`` is the sorted quantile grid saved at calibration-build time; the
    result is a percentile in [0,1] meaning "how extreme is this score for
    this model", which puts all three models on one comparable scale.
    """
    return float(np.searchsorted(grid, raw, side="left") / len(grid))


async def score_xgboost(txn_id: str, conn: Any, bundle: ModelBundle) -> ModelScore:
    """Supervised branch: P(fraud) via predict_proba, already in [0,1]."""
    vec = await build_xgboost_input(txn_id, conn, bundle)
    proba = float(bundle.xgb_model.predict_proba(vec.reshape(1, -1))[0, 1])
    return ModelScore(
        name="xgboost", contributed=True, raw_score=proba,
        calibrated_score=percentile_calibrate(proba, bundle.calibration["xgboost"]),
        detail={"recommended_threshold": bundle.xgb_threshold},
    )


async def score_isolation_forest(txn_id: str, conn: Any,
                                 bundle: ModelBundle) -> ModelScore:
    """Cold-start / complementary branch: ``-score_samples`` normalized to
    [0,1] by percentile rank against the 2M training anomaly scores."""
    vec = await build_isolation_forest_input(txn_id, conn, bundle)
    frame = pd.DataFrame(vec.reshape(1, -1), columns=bundle.iso_features)
    anomaly = float(-bundle.iso_model.score_samples(frame)[0])
    return ModelScore(
        name="isolation_forest", contributed=True, raw_score=anomaly,
        calibrated_score=percentile_calibrate(
            anomaly, bundle.calibration["isolation_forest"]),
    )


def run_lstm(lstm_input: LSTMInput, bundle: ModelBundle) -> float:
    """Forward pass on a built window; returns sigmoid P(fraud)."""
    import torch  # deferred: see artifacts.py docstring (OpenMP clash)

    with torch.no_grad():
        seq = torch.from_numpy(lstm_input.seq).unsqueeze(0)
        length = torch.tensor([lstm_input.length], dtype=torch.long)
        static = torch.from_numpy(lstm_input.static).unsqueeze(0)
        logit = bundle.lstm_model(seq, length, static)
        return float(torch.sigmoid(logit)[0])


async def score_lstm(account_id: str, txn_id: str, conn: Any, bundle: ModelBundle,
                     history_count: int, min_history: int) -> ModelScore:
    """Sequence branch. Only fires for accounts with >= ``min_history`` txns
    (paper: below that the LSTM wasn't trained to be reliable — and only
    ~6.9% of accounts qualify, so abstention is the COMMON case)."""
    if history_count < min_history:
        return ModelScore(
            name="lstm", contributed=False,
            detail={"reason": f"history {history_count} < required {min_history}",
                    "history_count": history_count},
        )
    lstm_input = await build_lstm_input(account_id, txn_id, conn, bundle)
    proba = run_lstm(lstm_input, bundle)
    return ModelScore(
        name="lstm", contributed=True, raw_score=proba,
        calibrated_score=percentile_calibrate(proba, bundle.calibration["lstm"]),
        detail={"history_count": history_count, "window_length": lstm_input.length},
    )
