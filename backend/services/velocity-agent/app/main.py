"""Velocity Agent microservice — one endpoint over the paper §IV-C-1 agent.

Hot path is Redis ONLY (agents.velocity_agent): sliding-window counts,
cached baselines and txn_type distributions, 1-2 ms per evaluation. This
service never touches Postgres — the account baselines it reads are
precomputed by the nightly batch job
(``feature_engineering.nightly_baseline_job``), which is the only place
Postgres is involved, and it runs outside this service.

If Redis is unreachable the endpoint returns 503 instead of a made-up
score: the fallback policy belongs to the Synthesis / orchestration layer.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

BACKEND_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND_DIR))  # agents/, feature_engineering/, shared/

# docker-compose exports REDIS_HOST; feature_config reads FRAUD_REDIS_HOST.
if "REDIS_HOST" in os.environ:
    os.environ.setdefault("FRAUD_REDIS_HOST", os.environ["REDIS_HOST"])

from agents.velocity_agent import RedisUnavailableError, VelocityAgent
from shared.constants.service_names import VELOCITY_AGENT
from shared.routers.health import health_router
from shared.schemas.transaction import TransactionEvent

logger = logging.getLogger("velocity-agent")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Velocity Agent",
    version="0.2.0",
    description="Paper §IV-C-1 velocity risk agent — Redis-only hot path.",
)
app.include_router(health_router(VELOCITY_AGENT))

agent = VelocityAgent()


@app.on_event("startup")
def startup_check_redis() -> None:
    try:
        agent.client.ping()
        logger.info("✅ Velocity Agent connected to Redis")
    except Exception as exc:  # startup must not crash; /evaluate reports 503
        logger.warning("Velocity Agent could not reach Redis at startup: %s", exc)


class VelocityEvaluateRequest(BaseModel):
    txn_id: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    amount_npr: float = Field(..., gt=0)
    timestamp: datetime | None = Field(
        default=None, description="Event time; defaults to now (UTC)."
    )
    txn_type: str | None = Field(
        default=None, description="Declared type (e.g. ESEWA_P2P, CARD_POS)."
    )


class VelocityEvaluateResponse(BaseModel):
    txn_id: str
    agent_name: str = VELOCITY_AGENT
    risk_score: float = Field(..., ge=0.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    latency_ms: int = Field(..., ge=0)


@app.post("/evaluate", response_model=VelocityEvaluateResponse)
async def evaluate(body: VelocityEvaluateRequest) -> VelocityEvaluateResponse:
    started = time.perf_counter()
    event = TransactionEvent(
        transaction_id=body.txn_id,
        user_id=body.account_id,
        amount=body.amount_npr,
        timestamp=body.timestamp or datetime.now(timezone.utc),
        txn_type=body.txn_type,
    )
    try:
        risk_score, confidence = await agent.evaluate(event)
    except RedisUnavailableError as exc:
        logger.error("Redis unavailable while evaluating txn_id=%s: %s", body.txn_id, exc)
        raise HTTPException(status_code=503, detail="Redis unavailable") from None

    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Evaluated txn_id=%s risk=%.4f confidence=%.4f latency_ms=%s",
        body.txn_id, risk_score, confidence, latency_ms,
    )
    return VelocityEvaluateResponse(
        txn_id=body.txn_id,
        risk_score=risk_score,
        confidence=confidence,
        latency_ms=latency_ms,
    )
