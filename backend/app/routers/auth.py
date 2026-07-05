"""Auth endpoints: mPIN login -> Redis session token, plus transfer-time re-auth."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.deps import app_db, create_session, destroy_session, get_current_user
from app.demo_profiles import DEMO_PROFILES, get_profile, public_view

logger = logging.getLogger("auth-router")

router = APIRouter(prefix="/auth", tags=["auth"])

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_mpin(mpin: str, salt: str) -> str:
    return hashlib.scrypt(mpin.encode(), salt=salt.encode(), **_SCRYPT).hex()


def make_mpin_hash(mpin: str, salt: str) -> str:
    """salt$hash format stored in app_users.mpin_hash (shared with the seed)."""
    return f"{salt}${hash_mpin(mpin, salt)}"


def verify_mpin_hash(mpin: str, stored: str) -> bool:
    salt, _, digest = stored.partition("$")
    return bool(digest) and hash_mpin(mpin, salt) == digest


class LoginRequest(BaseModel):
    mobile: str = Field(..., min_length=10, max_length=10)
    mpin: str = Field(..., min_length=4, max_length=4)


class BiometricLoginRequest(BaseModel):
    mobile: str = Field(..., min_length=10, max_length=10)


class VerifyMpinRequest(BaseModel):
    mpin: str = Field(..., min_length=4, max_length=4)


class ProfileLoginRequest(BaseModel):
    profileId: str = Field(..., min_length=1)


async def _login_payload(customer_row: Any) -> dict[str, Any]:
    user = {
        "customerId": customer_row["id"],
        "name": customer_row["name"],
        "accountNumber": customer_row["account_number"],
        "mobile": customer_row["mobile"],
    }
    token = await create_session(user)
    return {"token": token, "user": user}


@router.get("/demo-profiles")
async def demo_profiles() -> list[dict[str, Any]]:
    """The fixed demo profiles shown on the profile-picker login screen."""
    return [public_view(p) for p in DEMO_PROFILES]


@router.post("/login-profile")
async def login_profile(body: ProfileLoginRequest) -> dict[str, Any]:
    """One-click demo login as a fixed profile (no mPIN). Returns a session plus
    the profile's prefill so the dashboard can pre-populate the transfer form."""
    profile = get_profile(body.profileId)
    if profile is None:
        raise HTTPException(status_code=404, detail="Unknown demo profile.")
    async with app_db.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM app_customers WHERE id = $1", profile["customerId"])
    if row is None:
        raise HTTPException(
            status_code=503,
            detail="Demo profiles not seeded — run scripts.seed_demo_profiles.")
    payload = await _login_payload(row)
    payload["profile"] = public_view(profile)
    return payload


@router.post("/login-mpin")
async def login_mpin(body: LoginRequest) -> dict[str, Any]:
    async with app_db.pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT u.mpin_hash, c.*
               FROM app_users u JOIN app_customers c ON c.id = u.customer_id
               WHERE u.mobile = $1""",
            body.mobile.strip())
    if row is None or not verify_mpin_hash(body.mpin, row["mpin_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect mobile number or mPIN.")
    return await _login_payload(row)


@router.post("/login-biometric")
async def login_biometric(body: BiometricLoginRequest) -> dict[str, Any]:
    """Trusted-device biometric unlock: device biometrics already verified the
    user locally; the backend requires a registered mobile and issues a session."""
    async with app_db.pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT c.* FROM app_users u JOIN app_customers c ON c.id = u.customer_id
               WHERE u.mobile = $1""",
            body.mobile.strip())
    if row is None:
        raise HTTPException(status_code=401, detail="No registered user for this device.")
    return await _login_payload(row)


@router.get("/customer-preview")
async def customer_preview(mobile: str) -> dict[str, str]:
    """Login-screen name preview for a registered mobile number."""
    async with app_db.pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT c.name FROM app_users u JOIN app_customers c ON c.id = u.customer_id
               WHERE u.mobile = $1""",
            mobile.strip())
    if row is None:
        raise HTTPException(status_code=404, detail="Not registered")
    return {"name": row["name"]}


@router.post("/verify-mpin")
async def verify_mpin(body: VerifyMpinRequest,
                      user: dict = Depends(get_current_user)) -> dict[str, bool]:
    """Transfer-time re-authentication before submitting to the pipeline."""
    async with app_db.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT mpin_hash FROM app_users WHERE customer_id = $1",
            user["customerId"])
    if row is None or not verify_mpin_hash(body.mpin, row["mpin_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect mPIN.")
    return {"verified": True}


@router.post("/logout")
async def logout(user: dict = Depends(get_current_user)) -> dict[str, bool]:
    await destroy_session(user["_token"])
    return {"ok": True}
