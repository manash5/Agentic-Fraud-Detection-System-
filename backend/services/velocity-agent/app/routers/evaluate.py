import time

from fastapi import APIRouter
from pydantic import BaseModel, Field

from shared.constants.service_names import VELOCITY_AGENT
from shared.schemas.risk import AgentRiskResponse

router = APIRouter(prefix="/evaluate", tags=["evaluate"])


class VelocityEvaluateRequest(BaseModel):
    transaction_id: str
    txn_count_1h: int = Field(..., ge=0)
    txn_count_24h: int = Field(..., ge=0)
    amount: float = Field(..., ge=0)


@router.post("/risk", response_model=AgentRiskResponse)
async def evaluate_velocity_risk(body: VelocityEvaluateRequest) -> AgentRiskResponse:
    started = time.perf_counter()
    burst_ratio = body.txn_count_1h / max(body.txn_count_24h, 1)
    amount_factor = min(body.amount / 50_000.0, 1.0)
    risk_score = min(max(0.5 * burst_ratio + 0.5 * amount_factor, 0.0), 1.0)
    latency_ms = int((time.perf_counter() - started) * 1000)

    reasons: list[str] = []
    if body.txn_count_1h >= 5:
        reasons.append("high hourly transaction count")
    if amount_factor > 0.7:
        reasons.append("large transaction amount")

    return AgentRiskResponse(
        transaction_id=body.transaction_id,
        agent_name=VELOCITY_AGENT,
        risk_score=risk_score,
        confidence_score=0.80,
        reasons=reasons or ["velocity within normal range"],
    )
