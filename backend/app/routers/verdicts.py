"""Track-B model verdict endpoint, served from the synthesis_audit table."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.deps import app_db
from app.services import mappers

router = APIRouter(tags=["verdicts"])


@router.get("/verdicts/{txn_id}")
async def get_model_verdict(txn_id: str) -> dict[str, Any]:
    """Audit read for the analyst console (session-free like /admin)."""
    async with app_db.pool.acquire() as conn:
        audit = await conn.fetchrow(
            """SELECT * FROM synthesis_audit WHERE txn_id = $1
               ORDER BY created_at DESC LIMIT 1""", txn_id)
        txn_row = await conn.fetchrow(
            "SELECT * FROM app_transactions WHERE id = $1", txn_id)
    if audit is None:
        raise HTTPException(status_code=404,
                            detail="No pipeline verdict recorded for this transaction")
    txn = mappers.row_to_transaction(txn_row) if txn_row else None
    account_id = txn_row["account_id"] if txn_row else ""
    return mappers.audit_row_to_model_verdict(audit, txn, account_id=account_id)
