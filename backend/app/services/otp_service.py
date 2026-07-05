"""Backend-owned OTP lifecycle: generate -> SMS via EasySendSMS -> verify -> complete.

Triggered ONLY by the state projector after synthesis returns an OTP decision
(never before the final fraud decision). Live challenge state lives in Redis
(`otp:{txn_id}`, TTL 180s); `app_otp_events` is the Postgres audit trail the
admin OTP Center reads. The training reference table `otp_logs` is never
written — OTP absence there is a trained-in model signal.

Dev fallback: with no EASYSENDSMS_API_KEY or OTP_DEV_MODE=1 the code is logged
and surfaced as `devCode` in the transfer status payload so the full flow works
without a live SMS account.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import HTTPException

from app.deps import OTP_KEY, OTP_RESEND_KEY, TXN_STATE_KEY, app_db, redis_client

logger = logging.getLogger("otp-service")

OTP_TTL_SECONDS = 180
MAX_ATTEMPTS = 3
MAX_RESENDS = 2
RESEND_WINDOW_SECONDS = 600

EASYSENDSMS_URL = "https://restapi.easysendsms.app/v1/rest/sms/send"


def _dev_mode() -> bool:
    return (os.environ.get("OTP_DEV_MODE", "0") == "1"
            or not os.environ.get("EASYSENDSMS_API_KEY"))


def _hash(code: str, salt: str) -> str:
    return hashlib.sha256((salt + code).encode()).hexdigest()


def _to_e164_nepal(mobile: str) -> str:
    digits = "".join(ch for ch in mobile if ch.isdigit())
    if digits.startswith("977"):
        return digits
    return "977" + digits.lstrip("0")


async def send_sms(mobile: str, text: str) -> bool:
    """One isolated EasySendSMS REST v1 call; False (never an exception) on failure."""
    api_key = os.environ.get("EASYSENDSMS_API_KEY", "")
    sender = os.environ.get("EASYSENDSMS_SENDER", "GIMEBANK")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                EASYSENDSMS_URL,
                headers={"apikey": api_key, "Content-Type": "application/json"},
                json={"from": sender, "to": _to_e164_nepal(mobile),
                      "text": text, "type": "0"},
            )
        if resp.status_code == 200:
            logger.info("OTP SMS dispatched to %s", mobile)
            return True
        logger.error("EasySendSMS returned %s: %s", resp.status_code, resp.text[:300])
        return False
    except httpx.HTTPError as exc:
        logger.error("EasySendSMS request failed: %s", exc)
        return False


async def initiate(txn_id: str, account_id: str, mobile: str,
                   amount: float, reason: str) -> dict[str, Any]:
    """Generate + store + send an OTP; returns the otp block for the status payload."""
    code = f"{secrets.randbelow(10**6):06d}"
    salt = secrets.token_hex(8)
    key = OTP_KEY.format(txn_id=txn_id)
    await redis_client.hset(key, mapping={
        "code_hash": _hash(code, salt),
        "salt": salt,
        "attempts": 0,
        "mobile": mobile,
        "account_id": account_id,
        "amount": amount,
    })
    await redis_client.expire(key, OTP_TTL_SECONDS)

    expires_at = (datetime.now(timezone.utc)
                  + timedelta(seconds=OTP_TTL_SECONDS)).isoformat()
    otp_block: dict[str, Any] = {
        "expiresAt": expires_at,
        "attemptsLeft": MAX_ATTEMPTS,
        "channel": "SMS",
    }

    if _dev_mode():
        logger.warning("OTP_DEV_MODE: OTP for %s is %s (no SMS sent)", txn_id, code)
        otp_block["devCode"] = code
    else:
        sent = await send_sms(
            mobile,
            f"Global IME Sentinel: OTP for NPR {amount:,.2f} transfer is {code}. "
            f"Valid 3 minutes. Never share this code.")
        if not sent:
            logger.warning("SMS send failed for %s — exposing devCode fallback", txn_id)
            otp_block["devCode"] = code
            otp_block["smsFailed"] = True

    async with app_db.pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO app_otp_events (txn_id, account_id, mobile, channel,
                                           trigger_reason, status)
               VALUES ($1,$2,$3,'SMS',$4,'SENT')""",
            txn_id, account_id, mobile, reason)
    return otp_block


async def _load_state(txn_id: str) -> dict[str, Any] | None:
    raw = await redis_client.get(TXN_STATE_KEY.format(txn_id=txn_id))
    return json.loads(raw) if raw else None


async def _save_state(txn_id: str, state: dict[str, Any]) -> None:
    await redis_client.setex(
        TXN_STATE_KEY.format(txn_id=txn_id), 3600, json.dumps(state, default=str))


async def complete_transaction(txn_id: str) -> Any:
    """Debit the account and mark the app transaction successful (PASS/OTP-verified
    share this exact path). Returns the updated app_transactions record."""
    async with app_db.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM app_transactions WHERE id = $1 FOR UPDATE", txn_id)
            if row is None:
                return None
            if row["status"] == "success":  # idempotent
                return row
            await conn.execute(
                "UPDATE app_accounts SET balance = balance - $1 WHERE id = $2",
                row["amount"], row["account_id"])
            await conn.execute(
                "UPDATE app_transactions SET status = 'success' WHERE id = $1", txn_id)
        return await conn.fetchrow("SELECT * FROM app_transactions WHERE id = $1", txn_id)


async def fail_transaction(txn_id: str, status: str = "failed") -> None:
    async with app_db.pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_transactions SET status = $2 WHERE id = $1", txn_id, status)


async def verify(txn_id: str, code: str) -> Any:
    """Validate the code; on success complete the transaction and return its row.

    Raises HTTPException: 410 expired, 429 locked, 400 wrong code.
    """
    key = OTP_KEY.format(txn_id=txn_id)
    stored = await redis_client.hgetall(key)
    if not stored:
        raise HTTPException(status_code=410,
                            detail="OTP expired. Request a new code or restart the transfer.")

    attempts = await redis_client.hincrby(key, "attempts", 1)
    if attempts > MAX_ATTEMPTS:
        await redis_client.delete(key)
        await fail_transaction(txn_id)
        state = await _load_state(txn_id)
        if state:
            state["status"] = "failed"
            state["failReason"] = "otp_locked"
            await _save_state(txn_id, state)
        async with app_db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE app_otp_events SET status='LOCKED', attempts=$2 "
                "WHERE txn_id=$1 AND status='SENT'", txn_id, attempts - 1)
        raise HTTPException(status_code=429,
                            detail="Too many incorrect attempts. Transaction cancelled.")

    if _hash(code.strip(), stored["salt"]) != stored["code_hash"]:
        remaining = MAX_ATTEMPTS - attempts
        async with app_db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE app_otp_events SET attempts=$2 WHERE txn_id=$1 AND status='SENT'",
                txn_id, attempts)
        raise HTTPException(
            status_code=400,
            detail=f"Incorrect code. {remaining} attempt{'s' if remaining != 1 else ''} left.")

    await redis_client.delete(key)
    row = await complete_transaction(txn_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    async with app_db.pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_otp_events SET status='VERIFIED', attempts=$2, verified_at=now() "
            "WHERE txn_id=$1 AND status='SENT'", txn_id, attempts)
    state = await _load_state(txn_id)
    if state:
        state["status"] = "completed"
        state.pop("otp", None)
        await _save_state(txn_id, state)
    return row


async def resend(txn_id: str) -> dict[str, Any]:
    """Re-issue the OTP (rate-limited); returns a fresh otp block."""
    key = OTP_KEY.format(txn_id=txn_id)
    stored = await redis_client.hgetall(key)
    if not stored:
        raise HTTPException(status_code=410,
                            detail="OTP session expired. Restart the transfer.")
    resend_key = OTP_RESEND_KEY.format(txn_id=txn_id)
    count = await redis_client.incr(resend_key)
    if count == 1:
        await redis_client.expire(resend_key, RESEND_WINDOW_SECONDS)
    if count > MAX_RESENDS:
        raise HTTPException(status_code=429, detail="Resend limit reached.")
    otp_block = await initiate(
        txn_id, stored["account_id"], stored["mobile"],
        float(stored.get("amount", 0)), "resend")
    state = await _load_state(txn_id)
    if state:
        state["otp"] = otp_block
        await _save_state(txn_id, state)
    return otp_block
