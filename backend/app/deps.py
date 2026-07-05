"""Shared app-layer resources: .env loading, app DB pool, Redis, sessions.

Import this module FIRST in app/main.py — it loads backend/.env so every
config module that reads os.environ afterwards (FRAUD_DB_DSN, FRAUD_REDIS_*,
FRAUD_KAFKA_*, EASYSENDSMS_*) sees the values. Until now only the graph agent
loaded .env, which is why FRAUD_* had to be exported manually.

Never import torch (directly or transitively) here — the behavior agent's
artifacts loader controls the xgboost-before-torch import order.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

import asyncpg
import redis.asyncio as aioredis
from dotenv import load_dotenv
from fastapi import Header, HTTPException

BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")

from behavior_agent.config import pg_connect_kwargs  # noqa: E402

logger = logging.getLogger("app-deps")

SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "86400"))

# Redis key prefixes owned by the app layer. Disjoint from the agents' keys
# (user:*, account_baseline:*, geo:*, devices:*, velocity*).
SESSION_KEY = "session:{token}"
OTP_KEY = "otp:{txn_id}"
OTP_RESEND_KEY = "otp:resend:{txn_id}"
TXN_STATE_KEY = "txn:state:{txn_id}"
CUSTOMER_CACHE_KEY = "cache:customer:{customer_id}"
THRESHOLDS_KEY = "config:thresholds"


class AppDB:
    """asyncpg pool for the app_* tables (same fraud_detection_global DB)."""

    def __init__(self) -> None:
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self.pool is None:
            dsn = os.environ.get("FRAUD_DB_DSN", "dbname=fraud_detection_global")
            self.pool = await asyncpg.create_pool(
                min_size=1, max_size=8, **pg_connect_kwargs(dsn))
        from app.db_schema import ensure_schema
        async with self.pool.acquire() as conn:
            await ensure_schema(conn)

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None


app_db = AppDB()


def make_redis() -> aioredis.Redis:
    return aioredis.Redis(
        host=os.environ.get("FRAUD_REDIS_HOST", "localhost"),
        port=int(os.environ.get("FRAUD_REDIS_PORT", "6379")),
        decode_responses=True,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )


redis_client: aioredis.Redis = make_redis()


# -- sessions ------------------------------------------------------------------


async def create_session(user: dict[str, Any]) -> str:
    token = secrets.token_urlsafe(32)
    await redis_client.setex(
        SESSION_KEY.format(token=token), SESSION_TTL_SECONDS, json.dumps(user))
    return token


async def destroy_session(token: str) -> None:
    await redis_client.delete(SESSION_KEY.format(token=token))


async def get_current_user(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """FastAPI dependency: Bearer token -> session payload from Redis (401 otherwise).

    Sliding expiry: every authenticated request refreshes the TTL.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.removeprefix("Bearer ").strip()
    key = SESSION_KEY.format(token=token)
    try:
        raw = await redis_client.get(key)
    except Exception as exc:  # noqa: BLE001 — Redis down = cannot authenticate
        logger.error("Session lookup failed: %s", exc)
        raise HTTPException(status_code=503, detail="Session store unavailable") from None
    if raw is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    await redis_client.expire(key, SESSION_TTL_SECONDS)
    user: dict[str, Any] = json.loads(raw)
    user["_token"] = token
    return user
