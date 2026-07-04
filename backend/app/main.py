"""Unified Fraud-Detection API — all three agents in one FastAPI app.

One process, one command, three agents mounted under their own prefixes:

    POST /velocity/evaluate   Velocity Agent   (Redis sliding windows)
    POST /geo/evaluate        Geo Agent        (Redis + Postgres, travel + device)
    POST /graph/evaluate      Graph Agent      (Neo4j account network)
    POST /synthesis/evaluate  Synthesis Agent  (2-layer fusion of agent verdicts)
    GET  /health              per-agent connectivity

The Synthesis Agent is pure orchestration math (paper §IV-E): it takes the
risk/confidence verdicts the other agents produced and fuses them into one
score + fraud pattern + PASS/OTP/BLOCK decision. It reaches no datastore, so
it is always available even when an agent's backing store is down.

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
from agents.synthesis_agent import synthesise  # noqa: E402
from agents.velocity_agent import RedisUnavailableError, VelocityAgent  # noqa: E402
from shared.schemas.risk import (  # noqa: E402
    AgentVerdict,
    SynthesisResult,
    TransactionType,
)
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
app.state.graph_driver = None


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
    logger.info("✅ Velocity Agent ready (Redis, lazy connect)")


@app.on_event("shutdown")
async def shutdown() -> None:
    await geo_agent.close()
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


# -- synthesis -----------------------------------------------------------------


class AgentVerdictIn(BaseModel):
    """One agent's output as consumed by the Synthesis Agent.

    ``confidence_score`` is accepted as an alias for ``confidence`` so the
    running agents' response shape (which uses ``confidence_score``) can be
    forwarded verbatim.
    """

    risk_score: float = Field(..., ge=0.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0, alias="confidence_score")
    latency_ms: float = Field(default=0.0, ge=0.0)

    model_config = {"populate_by_name": True}

    def to_verdict(self) -> AgentVerdict:
        return AgentVerdict(
            risk_score=self.risk_score,
            confidence=self.confidence,
            latency_ms=int(self.latency_ms),
        )


class SynthesisRequest(BaseModel):
    """The three (soon four) agent verdicts to fuse, plus the transaction type.

    Every agent is optional: pass whichever ones ran. ``behavior`` has no
    running agent yet — omit it today and the synthesizer fuses the rest;
    include it once the Behavior Agent exists and it is folded in automatically.
    At least one agent must be present.
    """

    transaction_id: str = Field(..., min_length=1)
    transaction_type: TransactionType = TransactionType.P2P_TRANSFER
    velocity: AgentVerdictIn | None = None
    geo: AgentVerdictIn | None = None
    graph: AgentVerdictIn | None = None
    behavior: AgentVerdictIn | None = None


class SynthesisResponse(BaseModel):
    transaction_id: str
    agent_name: str = "synthesis-agent"
    result: SynthesisResult
    latency_ms: float = Field(..., ge=0.0)


@app.post("/synthesis/evaluate", response_model=SynthesisResponse)
async def evaluate_synthesis(body: SynthesisRequest) -> SynthesisResponse:
    started = time.monotonic()
    verdicts = {
        name: getattr(body, name).to_verdict()
        for name in ("velocity", "geo", "graph", "behavior")
        if getattr(body, name) is not None
    }
    try:
        result = synthesise(verdicts, body.transaction_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return SynthesisResponse(
        transaction_id=body.transaction_id,
        result=result,
        latency_ms=round((time.monotonic() - started) * 1000, 3),
    )
