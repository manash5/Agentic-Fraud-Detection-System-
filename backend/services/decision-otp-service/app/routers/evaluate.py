from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.decision import TAU_HIGH, TAU_LOW, classify_risk, decision_rationale
from app.otp_interlock import OTPInterlock
from shared.schemas.risk import DecisionAction

router = APIRouter(prefix="/evaluate", tags=["evaluate"])

_interlock = OTPInterlock()


class DecisionRequest(BaseModel):
    transaction_id: str
    final_score: float = Field(..., ge=0.0, le=1.0)


class DecisionResponse(BaseModel):
    transaction_id: str
    final_score: float
    decision: DecisionAction
    rationale: str
    thresholds: dict[str, float]


class OTPInitRequest(BaseModel):
    transaction_id: str
    user_id: str
    phone: str
    email: str


class OTPVerifyRequest(BaseModel):
    transaction_id: str
    sms_code: str | None = None
    email_code: str | None = None


class OTPStatusResponse(BaseModel):
    transaction_id: str
    sms_status: str
    email_status: str
    both_verified: bool
    auto_block: bool
    final_decision: DecisionAction


@router.post("/decision", response_model=DecisionResponse)
async def evaluate_decision(body: DecisionRequest) -> DecisionResponse:
    action = classify_risk(body.final_score)
    return DecisionResponse(
        transaction_id=body.transaction_id,
        final_score=body.final_score,
        decision=action,
        rationale=decision_rationale(body.final_score, action),
        thresholds={"tau_low": TAU_LOW, "tau_high": TAU_HIGH},
    )


@router.post("/otp/initiate", response_model=OTPStatusResponse)
async def initiate_otp(body: OTPInitRequest) -> OTPStatusResponse:
    challenge = await _interlock.initiate(
        body.transaction_id, body.user_id, body.phone, body.email
    )
    return _to_status(challenge.transaction_id, challenge)


@router.post("/otp/verify", response_model=OTPStatusResponse)
async def verify_otp(body: OTPVerifyRequest) -> OTPStatusResponse:
    try:
        challenge = await _interlock.verify(
            body.transaction_id,
            sms_code=body.sms_code,
            email_code=body.email_code,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_status(challenge.transaction_id, challenge)


def _to_status(transaction_id: str, challenge) -> OTPStatusResponse:
    if challenge.both_verified:
        final = DecisionAction.PASS
    elif challenge.should_auto_block:
        final = DecisionAction.BLOCK
    else:
        final = DecisionAction.OTP
    return OTPStatusResponse(
        transaction_id=transaction_id,
        sms_status=challenge.sms.status.value,
        email_status=challenge.email.status.value,
        both_verified=challenge.both_verified,
        auto_block=challenge.should_auto_block,
        final_decision=final,
    )
