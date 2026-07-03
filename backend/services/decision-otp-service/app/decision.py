"""PASS / OTP / BLOCK threshold logic (τ_low=0.30, τ_high=0.70)."""

from shared.schemas.risk import DecisionAction

TAU_LOW: float = 0.30
TAU_HIGH: float = 0.70


def classify_risk(final_score: float) -> DecisionAction:
    """Map a synthesised fraud score to a decision action."""
    if final_score < TAU_LOW:
        return DecisionAction.PASS
    if final_score < TAU_HIGH:
        return DecisionAction.OTP
    return DecisionAction.BLOCK


def decision_rationale(final_score: float, action: DecisionAction) -> str:
    return (
        f"score={final_score:.4f} → {action.value} "
        f"(τ_low={TAU_LOW}, τ_high={TAU_HIGH})"
    )
