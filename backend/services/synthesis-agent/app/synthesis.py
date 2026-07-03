"""Confidence-weighted synthesis (Eq. 2) and disagreement-variance check."""

from __future__ import annotations

import statistics

from app.pattern_classifier import classify_fraud_pattern
from app.weights import blend_weights, get_layer1_weights, get_layer2_weights
from shared.schemas.risk import (
    AgentVerdict,
    DecisionAction,
    SynthesisResult,
    TransactionType,
)

DISAGREEMENT_VARIANCE_THRESHOLD: float = 0.04


def confidence_weighted_score(
    weights: tuple[float, float, float],
    verdicts: tuple[AgentVerdict, AgentVerdict, AgentVerdict],
) -> float:
    """Eq. 2: Σ(w_i · s_i · c_i) / Σ(w_i · c_i)."""
    w_v, w_g, w_b = weights
    agents = verdicts
    numer = (
        w_v * agents[0].risk_score * agents[0].confidence
        + w_g * agents[1].risk_score * agents[1].confidence
        + w_b * agents[2].risk_score * agents[2].confidence
    )
    denom = (
        w_v * agents[0].confidence
        + w_g * agents[1].confidence
        + w_b * agents[2].confidence
    )
    if denom <= 0:
        return 0.0
    return min(max(numer / denom, 0.0), 1.0)


def disagreement_variance(
    velocity: AgentVerdict,
    geo: AgentVerdict,
    behavior: AgentVerdict,
) -> float:
    """Population variance of the three agent risk scores."""
    scores = [velocity.risk_score, geo.risk_score, behavior.risk_score]
    if len(scores) < 2:
        return 0.0
    return statistics.pvariance(scores)


def synthesise(
    transaction_type: TransactionType,
    velocity: AgentVerdict,
    geo: AgentVerdict,
    behavior: AgentVerdict,
) -> SynthesisResult:
    """Full two-layer synthesis pipeline."""
    pattern = classify_fraud_pattern(velocity, geo, behavior)
    layer1 = get_layer1_weights(transaction_type)
    layer2 = get_layer2_weights(pattern)
    blended = blend_weights(layer1, layer2)

    final_score = confidence_weighted_score(
        (blended.velocity, blended.geo, blended.behavior),
        (velocity, geo, behavior),
    )
    disagreement = disagreement_variance(velocity, geo, behavior)

    # High disagreement triggers OTP review band regardless of score.
    if disagreement >= DISAGREEMENT_VARIANCE_THRESHOLD:
        decision = DecisionAction.OTP
    elif final_score < 0.30:
        decision = DecisionAction.PASS
    elif final_score < 0.70:
        decision = DecisionAction.OTP
    else:
        decision = DecisionAction.BLOCK

    return SynthesisResult(
        final_score=final_score,
        weights_applied=blended,
        fraud_pattern=pattern,
        disagreement_score=disagreement,
        decision=decision,
    )
