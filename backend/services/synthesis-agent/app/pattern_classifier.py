"""Fraud-pattern classifier from agent risk scores."""

from shared.schemas.risk import AgentVerdict, FraudPattern


def classify_fraud_pattern(
    velocity: AgentVerdict,
    geo: AgentVerdict,
    behavior: AgentVerdict,
) -> FraudPattern:
    """Heuristic pattern classifier driven by dominant agent signals.

    - Rapid transfers: velocity dominates
    - Fraud ring: geo dominates (graph/location context)
    - Money laundering: balanced elevation across all three
    - Novel pattern: behavior dominates with low agreement
    """
    scores = {
        "velocity": velocity.risk_score,
        "geo": geo.risk_score,
        "behavior": behavior.risk_score,
    }
    dominant = max(scores, key=scores.get)  # type: ignore[arg-type]
    spread = max(scores.values()) - min(scores.values())
    mean_score = sum(scores.values()) / 3

    if spread < 0.10 and mean_score >= 0.40:
        return FraudPattern.MONEY_LAUNDERING
    if dominant == "velocity" and scores["velocity"] >= 0.45:
        return FraudPattern.RAPID_TRANSFERS
    if dominant == "geo" and scores["geo"] >= 0.40:
        return FraudPattern.FRAUD_RING
    if dominant == "behavior":
        return FraudPattern.NOVEL_PATTERN
    return FraudPattern.NOVEL_PATTERN
