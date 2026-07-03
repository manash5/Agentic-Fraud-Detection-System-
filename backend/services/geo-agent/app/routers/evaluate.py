import time

from fastapi import APIRouter
from pydantic import BaseModel, Field

from shared.constants.service_names import GEO_AGENT
from shared.schemas.risk import AgentRiskResponse

router = APIRouter(prefix="/evaluate", tags=["evaluate"])


class GeoEvaluateRequest(BaseModel):
    transaction_id: str
    distance_from_home_km: float = Field(..., ge=0)
    is_new_location: bool = False
    ring_proximity_score: float = Field(0.0, ge=0.0, le=1.0)


@router.post("/risk", response_model=AgentRiskResponse)
async def evaluate_geo_risk(body: GeoEvaluateRequest) -> AgentRiskResponse:
    started = time.perf_counter()
    distance_factor = min(body.distance_from_home_km / 500.0, 1.0)
    new_loc = 0.3 if body.is_new_location else 0.0
    risk_score = min(max(0.4 * distance_factor + 0.3 * body.ring_proximity_score + new_loc, 0.0), 1.0)
    latency_ms = int((time.perf_counter() - started) * 1000)

    reasons: list[str] = []
    if body.is_new_location:
        reasons.append("transaction from new location")
    if body.ring_proximity_score > 0.5:
        reasons.append("proximity to known fraud ring")
    if distance_factor > 0.6:
        reasons.append("far from home location")

    return AgentRiskResponse(
        transaction_id=body.transaction_id,
        agent_name=GEO_AGENT,
        risk_score=risk_score,
        confidence_score=0.78,
        reasons=reasons or ["geo context within normal range"],
    )
