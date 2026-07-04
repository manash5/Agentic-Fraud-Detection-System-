"""Ensemble blend: one consolidated risk score + a confidence score that
reflects which models contributed (paper Section IV-C-3).

Blend
-----
``risk_score = sum(w_i * s_i) / sum(w_i)`` over the models that contributed,
where ``s_i`` is the calibrated (percentile) score by default and ``w_i``
comes from the history-dependent weight profile in config.yaml:

  - cold_start  (history < cold_start_below): XGBoost's profile-join features
    are weak and the LSTM abstains, so the Isolation Forest carries the blend.
  - medium      (up to lstm_min_history): supervised XGBoost dominates,
    Isolation Forest stays as the complementary detector.
  - rich        (>= lstm_min_history): the LSTM fires and shares weight with
    XGBoost; the Isolation Forest drops to a supporting role.

Weights are renormalized over contributing models, so an abstaining or failed
model's weight is redistributed proportionally.

Confidence (consumed downstream by the Synthesis Agent)
--------------------------------------------------------
``confidence = coverage(n_contributing) * agreement``

  - coverage: from config (default 1 model -> 0.50, 2 -> 0.75, 3 -> 1.00) —
    fewer opinions, less confidence, regardless of what they say.
  - agreement = 1 - clip(stddev(scores) / 0.5, 0, 1): population stddev of the
    contributing scores against the maximum possible stddev of values in
    [0,1]. For two models this reduces to 1 - |s1 - s2|. A single
    contributing model has no dispersion to measure, so agreement = 1 and
    confidence = coverage(1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from behavior_agent.scorers import ModelScore


@dataclass
class BehaviorVerdict:
    risk_score: float
    confidence: float
    weights_profile: str
    history_count: int
    model_breakdown: dict[str, dict[str, Any]]


def weight_profile_for_history(history_count: int, cfg: dict[str, Any]) -> str:
    if history_count < cfg["history"]["cold_start_below"]:
        return "cold_start"
    if history_count < cfg["history"]["lstm_min_history"]:
        return "medium"
    return "rich"


def blend(scores: list[ModelScore], history_count: int,
          cfg: dict[str, Any]) -> BehaviorVerdict:
    profile = weight_profile_for_history(history_count, cfg)
    weights: dict[str, float] = cfg["ensemble"]["weights"][profile]
    use_calibrated = cfg["ensemble"]["blend_on"] == "calibrated"

    contributing: list[tuple[float, float]] = []  # (weight, score)
    breakdown: dict[str, dict[str, Any]] = {}
    for s in scores:
        w = float(weights.get(s.name, 0.0))
        entry: dict[str, Any] = {
            "contributed": s.contributed,
            "score": s.calibrated_score if use_calibrated else s.raw_score,
            "raw_score": s.raw_score,
            "calibrated_score": s.calibrated_score,
            "configured_weight": w,
            **s.detail,
        }
        if s.contributed:
            contributing.append((w, entry["score"]))
        breakdown[s.name] = entry

    if not contributing:
        raise ValueError("no model contributed a score — cannot blend")

    total_w = sum(w for w, _ in contributing)
    if total_w <= 0:  # e.g. every configured weight for the firing models is 0
        contributing = [(1.0, sc) for _, sc in contributing]
        total_w = float(len(contributing))
    risk = sum(w * sc for w, sc in contributing) / total_w

    for name, entry in breakdown.items():
        entry["effective_weight"] = (
            entry["configured_weight"] / total_w if entry["contributed"] else 0.0)

    n = len(contributing)
    coverage = float(cfg["confidence"]["coverage_by_n_contributing"][n])
    if n >= 2:
        dispersion = float(np.std([sc for _, sc in contributing]))  # ddof=0
        agreement = 1.0 - min(dispersion / 0.5, 1.0)
    else:
        agreement = 1.0
    confidence = max(0.0, min(coverage * agreement, 1.0))

    return BehaviorVerdict(
        risk_score=float(min(max(risk, 0.0), 1.0)),
        confidence=confidence,
        weights_profile=profile,
        history_count=history_count,
        model_breakdown=breakdown,
    )
