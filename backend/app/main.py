"""Unified Fraud-Detection API — all agents in one FastAPI app.

One process, one command, four agents mounted under their own prefixes:

    POST /velocity/evaluate          Velocity Agent  (Redis sliding windows)
    POST /geo/evaluate               Geo Agent       (Redis + Postgres, travel + device)
    POST /graph/evaluate             Graph Agent     (Neo4j account network)
    POST /agents/behavior/evaluate   Behavior Agent  (XGBoost + IsoForest + LSTM ensemble)
    POST /agents/synthesis/evaluate  Synthesis Agent (fuses the verdicts above; Postgres audit only)
    GET  /health                     per-agent connectivity

The Synthesis endpoint is called AFTER the other agents have returned their
verdicts — the caller fans out to velocity/geo/(graph)/(behavior), then posts
the collected scores to synthesis for the fused decision.

Each agent owns its own backing store; a store being down yields a 503 from
that agent's endpoint only (never a fabricated score) — the others keep serving.

Run from ``backend/``::

    uv run uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
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
from pipeline.agent_runner import (  # noqa: E402
    AgentOutcome,
    PipelineTxn,
    fuse,
    run_behavior,
    run_geo,
    run_graph,
    run_velocity,
)
from pipeline.audit import write_pipeline_audit  # noqa: E402
from pipeline.explanations import collect_explanations, primary_shap_summary  # noqa: E402
from kafka_bus.config import EventType  # noqa: E402
from kafka_bus.events import Event  # noqa: E402
from kafka_bus.producer import EventProducer  # noqa: E402
from shared.schemas.transaction import TransactionEvent  # noqa: E402
from synthesis_agent.api import router as synthesis_router  # noqa: E402
from synthesis_agent.api import store as synthesis_store  # noqa: E402
from synthesis_agent.txn_type_mapping import log_mapping_table  # noqa: E402

logger = logging.getLogger("fraud-api")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Agentic Fraud Detection API",
    version="1.0.0",
    description="Velocity + Geo + Graph + Behavior + Synthesis fraud agents behind one FastAPI app.",
)
app.include_router(synthesis_router)

# Agents are created eagerly (cheap) and connected on startup.
velocity_agent = VelocityAgent()
geo_agent = GeoAgent()
behavior_agent = BehaviorAgent()
event_producer = EventProducer()
app.state.graph_driver = None
app.state.behavior_model_error = None
app.state.kafka_ready = False


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
    log_mapping_table()
    try:
        await synthesis_store.connect()
        logger.info("✅ Synthesis Agent ready (asyncpg pool open, audit table ensured)")
    except Exception as exc:  # noqa: BLE001 — endpoint reports 503 instead
        logger.warning("Synthesis Agent could not connect at startup: %s", exc)
    try:
        await event_producer.start()
        app.state.kafka_ready = True
        logger.info("✅ Kafka producer ready (topic 'fraud-events')")
    except Exception as exc:  # noqa: BLE001 — /pipeline/submit reports 503 instead
        logger.warning("Kafka producer could not connect at startup: %s", exc)
    logger.info("✅ Velocity Agent ready (Redis, lazy connect)")


@app.on_event("shutdown")
async def shutdown() -> None:
    await geo_agent.close()
    await behavior_agent.close()
    await synthesis_store.close()
    if app.state.kafka_ready:
        await event_producer.stop()
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
    if synthesis_store.pool is None:
        agents["synthesis"] = "unavailable: postgres not connected"
    else:
        try:
            async with synthesis_store.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            agents["synthesis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            agents["synthesis"] = f"unavailable: {type(exc).__name__}"
    agents["kafka"] = "ok" if app.state.kafka_ready else "unavailable"
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
    shap: dict | None = None
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
        shap=(verdict.model_breakdown.get("xgboost") or {}).get("shap"),
        latency_ms=round(latency_ms, 3),
    )


# -- pipeline orchestrator -----------------------------------------------------
# Synchronous path: all four fusion agents (velocity, geo, graph, behavior) run
# in PARALLEL via asyncio.gather, fused in-process by synthesise(), decision
# layer applied, audit written, response returned in one HTTP round-trip.


class PipelineRequest(BaseModel):
    txn_id: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    txn_type: str = Field(..., min_length=1, description="RAW dataset value, e.g. ESEWA_P2P")
    amount: float = Field(0.0, ge=0.0)
    currency: str = "NPR"
    timestamp: datetime | None = Field(default=None, description="Event time; defaults to now (UTC).")
    device_id: str | None = None
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)

    def to_txn(self) -> PipelineTxn:
        return PipelineTxn(
            txn_id=self.txn_id, account_id=self.account_id, txn_type=self.txn_type,
            amount=self.amount, currency=self.currency, timestamp=self.timestamp,
            device_id=self.device_id, latitude=self.latitude, longitude=self.longitude)


class PipelineResponse(BaseModel):
    txn_id: str
    decision: str
    final_score: float
    fraud_pattern: str
    disagreement_score: float
    otp_forced_by_disagreement: bool
    agents_used: list[str]
    txn_type_mapped: str
    weights_applied: dict[str, float]
    agent_outcomes: dict[str, AgentOutcome]
    explanations: dict[str, object] = Field(default_factory=dict)
    shap: dict[str, object] | None = None
    total_latency_ms: float = Field(..., ge=0.0)
    parallel_agents_ms: float = Field(..., ge=0.0, description="Wall time of the fan-out step.")


@app.post("/evaluate", response_model=PipelineResponse)
async def evaluate_pipeline(body: PipelineRequest) -> PipelineResponse:
    """Fan out to the fusion agents in parallel, synthesise, decide, audit."""
    started = time.monotonic()
    txn = body.to_txn()

    fan_started = time.monotonic()
    tasks = {
        "velocity": run_velocity(velocity_agent, txn),
        "geo": run_geo(geo_agent, txn),
        "graph": run_graph(app.state.graph_driver, NEO4J_DATABASE, txn),
        "behavior": run_behavior(behavior_agent, app.state.behavior_model_error, txn),
    }
    gathered = await asyncio.gather(*tasks.values())
    outcomes = dict(zip(tasks.keys(), gathered))
    parallel_ms = round((time.monotonic() - fan_started) * 1000, 3)

    try:
        result, mapped, verdicts = fuse(outcomes, body.txn_type)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None

    try:
        await write_pipeline_audit(
            txn_id=body.txn_id, txn_type_raw=body.txn_type, txn_type_mapped=mapped.value,
            verdicts=verdicts, result=result, outcomes=outcomes)
    except Exception as exc:  # noqa: BLE001 — audit is best-effort in the pipeline
        logger.error("Pipeline audit write failed for %s: %s", body.txn_id, exc)

    total_ms = round((time.monotonic() - started) * 1000, 3)
    return PipelineResponse(
        txn_id=body.txn_id,
        decision=result.decision.value,
        final_score=result.final_score,
        fraud_pattern=result.fraud_pattern.value,
        disagreement_score=result.disagreement_score,
        otp_forced_by_disagreement=result.otp_forced_by_disagreement,
        agents_used=result.agents_used,
        txn_type_mapped=mapped.value,
        weights_applied=result.weights_applied.model_dump(),
        agent_outcomes=outcomes,
        explanations=collect_explanations(outcomes),
        shap=primary_shap_summary(outcomes),
        total_latency_ms=total_ms,
        parallel_agents_ms=parallel_ms,
    )


# -- kafka event-bus entrypoint ------------------------------------------------
# Fire-and-forget: publish the transaction to the "fraud-events" topic and
# return immediately. The standalone orchestrator (kafka_bus.orchestrator)
# consumes it, runs the agents in parallel, and publishes the *_completed +
# final_decision events back to the same topic. Kafka only transports; the
# orchestrator coordinates. In-process /evaluate above remains available for a
# synchronous request/response result.


class SubmitResponse(BaseModel):
    transaction_id: str
    event_type: str = EventType.TRANSACTION_RECEIVED
    status: str = "accepted"
    topic: str = "fraud-events"


@app.post("/pipeline/submit", response_model=SubmitResponse, status_code=202)
async def submit_transaction(body: PipelineRequest) -> SubmitResponse:
    """Publish ``transaction_received`` to Kafka; the orchestrator does the rest."""
    if not app.state.kafka_ready:
        raise HTTPException(status_code=503, detail="kafka producer unavailable")
    event = Event.make(
        EventType.TRANSACTION_RECEIVED, body.txn_id,
        {"account_id": body.account_id, "txn_type": body.txn_type,
         "amount": body.amount, "currency": body.currency,
         "device_id": body.device_id, "latitude": body.latitude,
         "longitude": body.longitude})
    try:
        await event_producer.publish(event)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to publish transaction %s: %s", body.txn_id, exc)
        raise HTTPException(status_code=503, detail="kafka publish failed") from None
    return SubmitResponse(transaction_id=body.txn_id)
