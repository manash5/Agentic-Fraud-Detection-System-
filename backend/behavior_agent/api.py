"""Behavior Agent FastAPI app.

    POST /agents/behavior/evaluate   {account_id, txn_id}
    GET  /agents/behavior/health

Run from ``backend/``::

    uv run uvicorn behavior_agent.api:app --port 8001

All models + scalers + preprocessors are loaded ONCE at startup (the paper's
"preloaded in memory" requirement for the 100ms budget); the asyncpg pool is
created at startup and closed at shutdown. Failures are debuggable by status:

    503 "behavior agent: model artifacts missing (...)"  -> export/build models
    503 "behavior agent: postgres unavailable"           -> check the database
    404                                                  -> unknown txn/account
    422                                                  -> txn exists but no model could score
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agents.behavior_agent import (
    AllModelsFailedError,
    BehaviorAgent,
    ModelMissingError,
    PostgresUnavailableError,
    TxnNotFoundError,
)

logger = logging.getLogger("behavior-api")

agent = BehaviorAgent()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Model loading failures are remembered (not raised) so the endpoint can
    # answer with a distinct 503 instead of the process dying at boot.
    app.state.model_error = None
    app.state.pg_error = None
    try:
        await agent.connect()
        logger.info("✅ Behavior Agent ready (3 models preloaded, asyncpg pool open)")
    except ModelMissingError as exc:
        app.state.model_error = str(exc)
        logger.error("Behavior Agent: model artifacts missing: %s", exc)
    except PostgresUnavailableError as exc:
        app.state.pg_error = str(exc)
        logger.error("Behavior Agent: postgres unavailable at startup: %s", exc)
    yield
    await agent.close()


app = FastAPI(title="Behavior Agent", version="1.0.0", lifespan=lifespan)


class BehaviorRequest(BaseModel):
    account_id: str = Field(..., min_length=1)
    txn_id: str = Field(..., min_length=1)


class BehaviorResponse(BaseModel):
    txn_id: str
    account_id: str
    agent_name: str = "behavior-agent"
    risk_score: float = Field(..., ge=0.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    weights_profile: str
    history_count: int
    model_breakdown: dict
    latency_ms: float = Field(..., ge=0.0)


def _check_ready() -> None:
    if app.state.model_error is not None:
        raise HTTPException(
            status_code=503,
            detail=f"behavior agent: model artifacts missing ({app.state.model_error})")
    if agent.pg_pool is None:
        raise HTTPException(
            status_code=503, detail="behavior agent: postgres unavailable")


@app.post("/agents/behavior/evaluate", response_model=BehaviorResponse)
async def evaluate(body: BehaviorRequest) -> BehaviorResponse:
    _check_ready()
    try:
        verdict, latency_ms = await agent.evaluate_timed(body.account_id, body.txn_id)
    except TxnNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except AllModelsFailedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except PostgresUnavailableError as exc:
        logger.error("Behavior Postgres unavailable for %s: %s", body.txn_id, exc)
        raise HTTPException(
            status_code=503, detail="behavior agent: postgres unavailable") from None
    return BehaviorResponse(
        txn_id=body.txn_id,
        account_id=body.account_id,
        risk_score=verdict.risk_score,
        confidence=verdict.confidence,
        weights_profile=verdict.weights_profile,
        history_count=verdict.history_count,
        model_breakdown=verdict.model_breakdown,
        latency_ms=round(latency_ms, 3),
    )


@app.get("/agents/behavior/health")
async def health() -> dict[str, object]:
    checks: dict[str, str] = {}
    checks["models"] = ("ok" if app.state.model_error is None
                        else f"missing: {app.state.model_error}")
    if agent.pg_pool is None:
        checks["postgres"] = "unavailable"
    else:
        try:
            async with agent.pg_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            checks["postgres"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["postgres"] = f"unavailable: {type(exc).__name__}"
    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"service": "behavior-agent", "status": status, "checks": checks}
