"""Unified Fraud-Detection API — all agents in one FastAPI app.

One process, one command, four agents mounted under their own prefixes:

    POST /velocity/evaluate          Velocity Agent  (Redis sliding windows)
    POST /geo/evaluate               Geo Agent       (Redis + Postgres, travel + device)
    POST /graph/evaluate             Graph Agent     (Neo4j account network)
    POST /agents/behavior/evaluate   Behavior Agent  (XGBoost + IsoForest + LSTM ensemble)
    GET  /health                     per-agent connectivity

Each agent owns its own backing store; a store being down yields a 503 from
that agent's endpoint only (never a fabricated score) — the others keep serving.

Run from ``backend/``::

    uv run uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# docker/compose may export REDIS_HOST; feature_config reads FRAUD_REDIS_HOST.
if "REDIS_HOST" in os.environ:
    os.environ.setdefault("FRAUD_REDIS_HOST", os.environ["REDIS_HOST"])

from neo4j.exceptions import Neo4jError, ServiceUnavailable  # noqa: E402

from agents.geo_agent import GeoAgent, PostgresUnavailableError  # noqa: E402
from agents.graph_agent import (  # noqa: E402
    NEO4J_DATABASE,
    evaluate as graph_evaluate,
    get_driver,
    graph_counts,
)
from agents.behavior_agent import (  # noqa: E402
    AllModelsFailedError,
    BehaviorAgent,
    ModelMissingError,
)
from agents.behavior_agent import (  # noqa: E402
    PostgresUnavailableError as BehaviorPostgresUnavailableError,
)
from agents.behavior_agent import (  # noqa: E402
    TxnNotFoundError as BehaviorTxnNotFoundError,
)
from agents.velocity_agent import RedisUnavailableError, VelocityAgent  # noqa: E402
from shared.schemas.transaction import TransactionEvent  # noqa: E402

logger = logging.getLogger("fraud-api")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Agentic Fraud Detection API",
    version="1.0.0",
    description="Velocity + Geo + Graph fraud agents behind one FastAPI app.",
)

# Agents are created eagerly (cheap) and connected on startup.
velocity_agent = VelocityAgent()
geo_agent = GeoAgent()
behavior_agent = BehaviorAgent()
app.state.graph_driver = None
app.state.behavior_model_error = None


@app.on_event("startup")
async def startup() -> None:
    try:
        await geo_agent.connect()
        logger.info("✅ Geo Agent connected (Redis + asyncpg pool)")
    except Exception as exc:  # startup must not crash; endpoint reports 503
        logger.warning("Geo Agent could not connect at startup: %s", exc)
    try:
        app.state.graph_driver = get_driver()
        with app.state.graph_driver.session(database=NEO4J_DATABASE) as session:
            nodes, rels = graph_counts(session)
        logger.info("✅ Graph Agent connected to Neo4j '%s' (%d nodes / %d edges)",
                    NEO4J_DATABASE, nodes, rels)
    except Exception as exc:
        logger.warning("Graph Agent could not connect at startup: %s", exc)
    try:
        await behavior_agent.connect()
        logger.info("✅ Behavior Agent ready (3 models preloaded, asyncpg pool open)")
    except ModelMissingError as exc:
        app.state.behavior_model_error = str(exc)
        logger.warning("Behavior Agent: model artifacts missing: %s", exc)
    except BehaviorPostgresUnavailableError as exc:
        logger.warning("Behavior Agent: postgres unavailable at startup: %s", exc)
    logger.info("✅ Velocity Agent ready (Redis, lazy connect)")


@app.on_event("shutdown")
async def shutdown() -> None:
    await geo_agent.close()
    await behavior_agent.close()
    if app.state.graph_driver is not None:
        app.state.graph_driver.close()


# -- health --------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, object]:
    """Liveness plus a per-agent backing-store probe."""
    agents: dict[str, str] = {}
    try:
        velocity_agent.client.ping()
        agents["velocity"] = "ok"
    except Exception as exc:  # noqa: BLE001
        agents["velocity"] = f"unavailable: {type(exc).__name__}"
    try:
        await geo_agent.redis.ping()
        agents["geo"] = "ok"
    except Exception as exc:  # noqa: BLE001
        agents["geo"] = f"unavailable: {type(exc).__name__}"
    try:
        with app.state.graph_driver.session(database=NEO4J_DATABASE) as session:
            session.run("RETURN 1").consume()
        agents["graph"] = "ok"
    except Exception as exc:  # noqa: BLE001
        agents["graph"] = f"unavailable: {type(exc).__name__}"
    if app.state.behavior_model_error is not None:
        agents["behavior"] = f"models missing: {app.state.behavior_model_error}"
    elif behavior_agent.pg_pool is None:
        agents["behavior"] = "unavailable: postgres not connected"
    else:
        try:
            async with behavior_agent.pg_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            agents["behavior"] = "ok"
        except Exception as exc:  # noqa: BLE001
            agents["behavior"] = f"unavailable: {type(exc).__name__}"
    status = "ok" if all(v == "ok" for v in agents.values()) else "degraded"
    return {"service": "fraud-detection-api", "status": status, "agents": agents}


# -- velocity ------------------------------------------------------------------


class VelocityResponse(BaseModel):
    transaction_id: str
    agent_name: str = "velocity-agent"
    risk_score: float = Field(..., ge=0.0, le=1.0)
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    latency_ms: float = Field(..., ge=0.0)


@app.post("/velocity/evaluate", response_model=VelocityResponse)
async def evaluate_velocity(event: TransactionEvent) -> VelocityResponse:
    started = time.monotonic()
    try:
        risk, confidence = await velocity_agent.evaluate(event)
    except RedisUnavailableError as exc:
        logger.error("Velocity Redis unavailable for %s: %s", event.transaction_id, exc)
        raise HTTPException(status_code=503, detail="velocity agent: redis unavailable") from None
    return VelocityResponse(
        transaction_id=event.transaction_id,
        risk_score=risk,
        confidence_score=confidence,
        latency_ms=round((time.monotonic() - started) * 1000, 3),
    )


# -- geo -----------------------------------------------------------------------


class GeoRequest(BaseModel):
    txn_id: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    device_id: str = Field(..., min_length=1)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    timestamp: datetime | None = Field(default=None, description="Event time; defaults to now (UTC).")


class GeoResponse(BaseModel):
    txn_id: str
    agent_name: str = "geo-agent"
    risk_score: float = Field(..., ge=0.0, le=1.0)
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    signals: dict[str, float]
    latency_ms: float = Field(..., ge=0.0)


@app.post("/geo/evaluate", response_model=GeoResponse)
async def evaluate_geo(body: GeoRequest) -> GeoResponse:
    if geo_agent.pg_pool is None:
        raise HTTPException(status_code=503, detail="geo agent: postgres unavailable")
    started = time.monotonic()
    try:
        risk, confidence, signals = await geo_agent.evaluate(
            account_id=body.account_id,
            txn_id=body.txn_id,
            device_id=body.device_id,
            latitude=body.latitude,
            longitude=body.longitude,
            timestamp=body.timestamp or datetime.now(timezone.utc),
        )
    except RedisUnavailableError as exc:
        logger.error("Geo Redis unavailable for %s: %s", body.txn_id, exc)
        raise HTTPException(status_code=503, detail="geo agent: redis unavailable") from None
    except PostgresUnavailableError as exc:
        logger.error("Geo Postgres unavailable for %s: %s", body.txn_id, exc)
        raise HTTPException(status_code=503, detail="geo agent: postgres unavailable") from None
    return GeoResponse(
        txn_id=body.txn_id,
        risk_score=risk,
        confidence_score=confidence,
        signals=signals,
        latency_ms=round((time.monotonic() - started) * 1000, 3),
    )


# -- graph ---------------------------------------------------------------------


class GraphRequest(BaseModel):
    account_id: str = Field(..., min_length=1, description="ACC-* account id to score")


class GraphResponse(BaseModel):
    account_id: str
    agent_name: str = "graph-agent"
    graph_score: float = Field(..., ge=0.0, le=1.0)
    flag: str
    decision: str
    reasons: list[str]
    signals: dict
    latency_ms: float = Field(..., ge=0.0)


@app.post("/graph/evaluate", response_model=GraphResponse)
async def evaluate_graph(body: GraphRequest) -> GraphResponse:
    if app.state.graph_driver is None:
        raise HTTPException(status_code=503, detail="graph agent: neo4j unavailable")
    started = time.monotonic()

    def _run() -> dict:
        with app.state.graph_driver.session(database=NEO4J_DATABASE) as session:
            return graph_evaluate(body.account_id, session)

    try:
        result = await run_in_threadpool(_run)
    except (ServiceUnavailable, Neo4jError) as exc:
        logger.error("Graph Neo4j error for %s: %s", body.account_id, exc)
        raise HTTPException(status_code=503, detail="graph agent: neo4j unavailable") from None
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return GraphResponse(
        account_id=result["account_id"],
        graph_score=result["graph_score"],
        flag=result["flag"],
        decision=result["decision"],
        reasons=result["reasons"],
        signals=result["signals"],
        latency_ms=round((time.monotonic() - started) * 1000, 3),
    )


# -- behavior --------------------------------------------------------------------


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


@app.post("/agents/behavior/evaluate", response_model=BehaviorResponse)
async def evaluate_behavior(body: BehaviorRequest) -> BehaviorResponse:
    if app.state.behavior_model_error is not None:
        raise HTTPException(
            status_code=503,
            detail=f"behavior agent: model artifacts missing "
                   f"({app.state.behavior_model_error})")
    if behavior_agent.pg_pool is None:
        raise HTTPException(status_code=503, detail="behavior agent: postgres unavailable")
    try:
        verdict, latency_ms = await behavior_agent.evaluate_timed(
            body.account_id, body.txn_id)
    except BehaviorTxnNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except AllModelsFailedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except BehaviorPostgresUnavailableError as exc:
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
