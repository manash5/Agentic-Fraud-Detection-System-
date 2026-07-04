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
    """Per-agent weight quad (velocity, geo, graph, behavior).

    The paper (§IV-E, Tables I & II) defines three agents — velocity, geo,
    behavior. This implementation splits the paper's Geo Agent into ``geo``
    (travel + device) and ``graph`` (Neo4j network checks). Absent agents
    appear in audit output with weight ``0.0``; fusion renormalizes over those
    that reported.
    """

    velocity: float = Field(..., ge=0.0, le=1.0)
    geo: float = Field(..., ge=0.0, le=1.0)
    graph: float = Field(..., ge=0.0, le=1.0)
    behavior: float = Field(..., ge=0.0, le=1.0)


class Layer1Weights(BaseModel):
    """Table I — transaction-type base weights (velocity/geo/graph/behavior).

    Derived from the paper's Table I by carving a ``graph`` share out of the
    original Geo weight (the graph checks used to live inside the Geo Agent).
    """

    p2p_transfer: AgentWeights = AgentWeights(
        velocity=0.35, geo=0.20, graph=0.20, behavior=0.25)
    merchant_payment: AgentWeights = AgentWeights(
        velocity=0.25, geo=0.25, graph=0.25, behavior=0.25)
    atm_withdrawal: AgentWeights = AgentWeights(
        velocity=0.30, geo=0.30, graph=0.25, behavior=0.15)
    bill_payment: AgentWeights = AgentWeights(
        velocity=0.20, geo=0.25, graph=0.20, behavior=0.35)


class Layer2Weights(BaseModel):
    """Table II — fraud-pattern effectiveness weights (velocity/geo/graph/behavior).

    The graph agent is the strongest detector of coordinated fraud, so it
    carries the dominant share for ``fraud_ring`` and a large share for
    ``money_laundering`` (circular money flow); velocity leads ``rapid_transfers``
    and behavior leads ``novel_pattern``.
    """

    rapid_transfers: AgentWeights = AgentWeights(
        velocity=0.50, geo=0.10, graph=0.15, behavior=0.25)
    fraud_ring: AgentWeights = AgentWeights(
        velocity=0.15, geo=0.30, graph=0.40, behavior=0.15)
    money_laundering: AgentWeights = AgentWeights(
        velocity=0.25, geo=0.25, graph=0.25, behavior=0.25)
    novel_pattern: AgentWeights = AgentWeights(
        velocity=0.25, geo=0.25, graph=0.20, behavior=0.30)


class BlendedWeights(AgentWeights):
    """Final 50/50 blended weights actually applied during synthesis.

    Agents absent from a given request appear here with weight ``0.0``.
    """


class SynthesisResult(BaseModel):
    """Full audit-grade output of the Synthesis Agent (paper §IV-E, §V-A).

    Carries everything the NRB audit trail needs: the fused score, both
    selected weight layers, the blended weights, the detected pattern, the
    disagreement statistic, which agents contributed, and the final verdict.
    """

    final_score: float = Field(..., ge=0.0, le=1.0)
    weights_applied: BlendedWeights
    fraud_pattern: FraudPattern
    disagreement_score: float = Field(..., ge=0.0)
    decision: DecisionAction
    layer1_weights: AgentWeights
    layer2_weights: AgentWeights
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
