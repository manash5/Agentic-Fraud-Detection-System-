"""Geo Agent microservice — one endpoint over the paper §IV-C-2 Phase 1 agent.

Signals: travel feasibility + device fingerprint novelty. Redis-first hot
path with an asyncpg fallback on cache miss; no Neo4j (graph checks belong
to a future Graph Agent and are excluded, not stubbed). If Redis or
Postgres cannot answer, the endpoint returns 503 rather than a made-up
score — the fallback policy belongs to the orchestration layer.
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

from agents.geo_agent import GeoAgent, PostgresUnavailableError
from agents.velocity_agent import RedisUnavailableError
from shared.constants.service_names import GEO_AGENT
from shared.routers.health import health_router

logger = logging.getLogger("geo-agent")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Geo Agent",
    version="0.2.0",
    description="Paper §IV-C-2 geo risk agent, Phase 1: travel feasibility + device novelty.",
)
app.include_router(health_router(GEO_AGENT))

agent = GeoAgent()


@app.on_event("startup")
async def startup() -> None:
    try:
        await agent.connect()
        logger.info("✅ Geo Agent connected (Redis + asyncpg pool)")
    except Exception as exc:  # startup must not crash; /evaluate reports 503
        logger.warning("Geo Agent could not connect at startup: %s", exc)


@app.on_event("shutdown")
async def shutdown() -> None:
    await agent.close()


class GeoEvaluateRequest(BaseModel):
    txn_id: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    device_id: str = Field(..., min_length=1)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    timestamp: datetime | None = Field(
        default=None, description="Event time; defaults to now (UTC)."
    )


class GeoEvaluateResponse(BaseModel):
    txn_id: str
    agent_name: str = GEO_AGENT
    risk_score: float = Field(..., ge=0.0, le=1.0)
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    signals: dict[str, float]
    latency_ms: float = Field(..., ge=0.0)


@app.post("/evaluate", response_model=GeoEvaluateResponse)
async def evaluate(body: GeoEvaluateRequest) -> GeoEvaluateResponse:
    if agent.pg_pool is None:
        raise HTTPException(status_code=503, detail="geo agent: postgres unavailable")
    started = time.monotonic()
    try:
        risk_score, confidence, signals = await agent.evaluate(
            account_id=body.account_id,
            txn_id=body.txn_id,
            device_id=body.device_id,
            latitude=body.latitude,
            longitude=body.longitude,
            timestamp=body.timestamp or datetime.now(timezone.utc),
        )
    except RedisUnavailableError as exc:
        logger.error("Redis unavailable for txn_id=%s: %s", body.txn_id, exc)
        raise HTTPException(status_code=503, detail="geo agent: redis unavailable") from None
    except PostgresUnavailableError as exc:
        logger.error("Postgres unavailable for txn_id=%s: %s", body.txn_id, exc)
        raise HTTPException(status_code=503, detail="geo agent: postgres unavailable") from None

    latency_ms = (time.monotonic() - started) * 1000
    logger.info(
        "Evaluated txn_id=%s risk=%.4f confidence=%.4f latency_ms=%.2f",
        body.txn_id, risk_score, confidence, latency_ms,
    )
    return GeoEvaluateResponse(
        txn_id=body.txn_id,
        risk_score=risk_score,
        confidence_score=confidence,
        signals=signals,
        latency_ms=round(latency_ms, 3),
    )
