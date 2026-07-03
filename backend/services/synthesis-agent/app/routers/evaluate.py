from fastapi import APIRouter
from pydantic import BaseModel

from app.synthesis import synthesise
from shared.schemas.risk import AgentVerdict, SynthesisResult, TransactionType

router = APIRouter(prefix="/evaluate", tags=["evaluate"])


class SynthesisRequest(BaseModel):
    transaction_id: str
    transaction_type: TransactionType = TransactionType.P2P_TRANSFER
    velocity: AgentVerdict
    geo: AgentVerdict
    behavior: AgentVerdict


class SynthesisResponse(BaseModel):
    transaction_id: str
    result: SynthesisResult


@router.post("/synthesise", response_model=SynthesisResponse)
async def synthesise_risk(body: SynthesisRequest) -> SynthesisResponse:
    result = synthesise(
        body.transaction_type,
        body.velocity,
        body.geo,
        body.behavior,
    )
    return SynthesisResponse(transaction_id=body.transaction_id, result=result)
