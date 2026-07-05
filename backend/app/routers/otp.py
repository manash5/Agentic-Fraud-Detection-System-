"""OTP verify/resend endpoints. Codes are only ever issued by the state
projector after a final OTP decision; these endpoints close the loop."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.deps import app_db, get_current_user
from app.services import mappers, otp_service

router = APIRouter(prefix="/otp", tags=["otp"])


class VerifyBody(BaseModel):
    txnId: str = Field(..., min_length=1)
    code: str = Field(..., min_length=6, max_length=6)


class ResendBody(BaseModel):
    txnId: str = Field(..., min_length=1)


async def _assert_ownership(txn_id: str, user: dict) -> None:
    async with app_db.pool.acquire() as conn:
        owner = await conn.fetchval(
            "SELECT customer_id FROM app_transactions WHERE id = $1", txn_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if owner != user["customerId"]:
        raise HTTPException(status_code=403, detail="Not your transaction")


@router.post("/verify")
async def verify_otp(body: VerifyBody,
                     user: dict = Depends(get_current_user)) -> dict[str, Any]:
    await _assert_ownership(body.txnId, user)
    row = await otp_service.verify(body.txnId, body.code)
    return {"verified": True, "txn": mappers.row_to_transaction(row)}


@router.post("/resend")
async def resend_otp(body: ResendBody,
                     user: dict = Depends(get_current_user)) -> dict[str, Any]:
    await _assert_ownership(body.txnId, user)
    otp_block = await otp_service.resend(body.txnId)
    return {"sent": True, "otp": otp_block}
