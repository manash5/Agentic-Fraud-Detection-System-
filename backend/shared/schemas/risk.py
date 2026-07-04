try:
    from enum import StrEnum
except ImportError:  # Python 3.10 compatibility for service-specific runtimes.
    from enum import Enum

    class StrEnum(str, Enum):
        pass

from pydantic import BaseModel, Field


class FraudPattern(StrEnum):
    RAPID_TRANSFERS = "rapid_transfers"
    FRAUD_RING = "fraud_ring"
    MONEY_LAUNDERING = "money_laundering"
    NOVEL_PATTERN = "novel_pattern"


class TransactionType(StrEnum):
    P2P_TRANSFER = "p2p_transfer"
    MERCHANT_PAYMENT = "merchant_payment"
    ATM_WITHDRAWAL = "atm_withdrawal"
    BILL_PAYMENT = "bill_payment"


class DecisionAction(StrEnum):
    PASS = "PASS"
    OTP = "OTP"
    BLOCK = "BLOCK"


class AgentVerdict(BaseModel):
    """Single-agent risk output with confidence and latency."""

    risk_score: float = Field(..., ge=0.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    latency_ms: int = Field(..., ge=0)


class AgentWeights(BaseModel):
    """Per-agent weight quad (velocity, geo, graph, behavior)."""

    velocity: float = Field(..., ge=0.0, le=1.0)
    geo: float = Field(..., ge=0.0, le=1.0)
    graph: float = Field(..., ge=0.0, le=1.0)
    behavior: float = Field(..., ge=0.0, le=1.0)


class Layer1Weights(BaseModel):
    """Table I — transaction-type base weights."""

    p2p_transfer: AgentWeights = AgentWeights(
        velocity=0.35, geo=0.20, graph=0.20, behavior=0.25)
    merchant_payment: AgentWeights = AgentWeights(
        velocity=0.25, geo=0.25, graph=0.25, behavior=0.25)
    atm_withdrawal: AgentWeights = AgentWeights(
        velocity=0.30, geo=0.30, graph=0.25, behavior=0.15)
    bill_payment: AgentWeights = AgentWeights(
        velocity=0.20, geo=0.25, graph=0.20, behavior=0.35)


class Layer2Weights(BaseModel):
    """Table II — fraud-pattern adjustment weights."""

    rapid_transfers: AgentWeights = AgentWeights(
        velocity=0.50, geo=0.10, graph=0.15, behavior=0.25)
    fraud_ring: AgentWeights = AgentWeights(
        velocity=0.15, geo=0.30, graph=0.40, behavior=0.15)
    money_laundering: AgentWeights = AgentWeights(
        velocity=0.25, geo=0.25, graph=0.25, behavior=0.25)
    novel_pattern: AgentWeights = AgentWeights(
        velocity=0.25, geo=0.25, graph=0.20, behavior=0.30)


class BlendedWeights(AgentWeights):
    """Final 50/50 blended weights applied during synthesis."""


class SynthesisResult(BaseModel):
    final_score: float = Field(..., ge=0.0, le=1.0)
    weights_applied: BlendedWeights
    fraud_pattern: FraudPattern
    disagreement_score: float = Field(..., ge=0.0)
    decision: DecisionAction
    layer1_weights: AgentWeights | None = None
    layer2_weights: AgentWeights | None = None
    agents_used: list[str] = Field(default_factory=list)
    otp_forced_by_disagreement: bool = False


class SHAPContribution(BaseModel):
    feature: str
    value: float
    direction: str = Field(..., description="positive increases fraud risk, negative decreases it")


class SHAPExplanation(BaseModel):
    feature_names: list[str]
    values: list[float]
    directions: list[str]
    base_value: float = 0.0

    @classmethod
    def from_contributions(
        cls,
        contributions: list[SHAPContribution],
        base_value: float = 0.0,
    ) -> "SHAPExplanation":
        return cls(
            feature_names=[c.feature for c in contributions],
            values=[c.value for c in contributions],
            directions=[c.direction for c in contributions],
            base_value=base_value,
        )


class AgentRiskResponse(BaseModel):
    """Legacy agent response shape — kept for backward compatibility."""

    transaction_id: str
    agent_name: str
    risk_score: float = Field(..., ge=0.0, le=1.0)
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    reasons: list[str] = []
    shap: SHAPExplanation | None = None
