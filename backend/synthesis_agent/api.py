"""Synthesis Agent FastAPI endpoint — paper §IV-E behind HTTP.

    POST /agents/synthesis/evaluate   {txn_id, txn_type, velocity, geo, graph?, behavior?}
    GET  /agents/synthesis/health

Run standalone from ``backend/``::

    uv run uvicorn synthesis_agent.api:app --port 8002

or mount ``router`` into the unified app (app/main.py does this).

The endpoint is called AFTER the other agents have produced their verdicts —
it never calls them itself. The fusion is a pure in-process function call
(``agents.synthesis_agent.synthesise``); the ONLY database call here is the
synchronous Postgres audit write, which must land before the response is
returned (paper §IV-E audit requirement). Synthesis is Redis-free end to end.

Latency budget: the paper's Table V allows ~3ms for synthesis + weight
blending. The pure math should sit far under that; if total latency blows the
budget the bottleneck is almost certainly the audit write, so both phases are
timed separately and the warning says which one overran.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager

import asyncpg
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from agents.synthesis_agent import synthesise
from behavior_agent.config import pg_connect_kwargs
from shared.schemas.risk import AgentVerdict
from synthesis_agent.txn_type_mapping import log_mapping_table, map_txn_type

logger = logging.getLogger("synthesis-agent")

SYNTHESIS_BUDGET_MS = 3.0  # paper Table V: synthesis + weight blending

_DDL = """
CREATE TABLE IF NOT EXISTS synthesis_audit (
    id                          BIGSERIAL PRIMARY KEY,
    txn_id                      TEXT NOT NULL,
    txn_type_raw                TEXT NOT NULL,
    txn_type_mapped             TEXT NOT NULL,
    input_verdicts              JSONB NOT NULL,
    layer1_weights              JSONB NOT NULL,
    layer2_weights              JSONB NOT NULL,
    blended_weights             JSONB NOT NULL,
    fraud_pattern               TEXT NOT NULL,
    disagreement_score          DOUBLE PRECISION NOT NULL,
    final_score                 DOUBLE PRECISION NOT NULL,
    decision                    TEXT NOT NULL,
    agents_used                 TEXT[] NOT NULL,
    otp_forced_by_disagreement  BOOLEAN NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_synthesis_audit_txn ON synthesis_audit (txn_id);
ALTER TABLE synthesis_audit ADD COLUMN IF NOT EXISTS agent_explanations JSONB;
ALTER TABLE synthesis_audit ADD COLUMN IF NOT EXISTS shap_explanation JSONB;
"""


class AuditStore:
    """asyncpg pool + the one INSERT this endpoint is allowed to make."""

    def __init__(self, pool: asyncpg.Pool | None = None) -> None:
        self.pool = pool

    async def connect(self) -> None:
        if self.pool is None:
            dsn = os.environ.get("FRAUD_DB_DSN", "dbname=fraud_detection_global")
            self.pool = await asyncpg.create_pool(
                min_size=1, max_size=4, **pg_connect_kwargs(dsn))
        async with self.pool.acquire() as conn:
            await conn.execute(_DDL)

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def write(
        self,
        *,
        txn_id: str,
        txn_type_raw: str,
        txn_type_mapped: str,
        verdicts: dict[str, AgentVerdict],
        result,
        agent_explanations: dict | None = None,
        shap_explanation: dict | None = None,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO synthesis_audit (
                    txn_id, txn_type_raw, txn_type_mapped, input_verdicts,
                    layer1_weights, layer2_weights, blended_weights,
                    fraud_pattern, disagreement_score, final_score, decision,
                    agents_used, otp_forced_by_disagreement,
                    agent_explanations, shap_explanation
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                """,
                txn_id,
                txn_type_raw,
                txn_type_mapped,
                json.dumps({a: v.model_dump() for a, v in verdicts.items()}),
                json.dumps(result.layer1_weights.model_dump()),
                json.dumps(result.layer2_weights.model_dump()),
                json.dumps(result.weights_applied.model_dump()),
                result.fraud_pattern.value,
                result.disagreement_score,
                result.final_score,
                result.decision.value,
                result.agents_used,
                result.otp_forced_by_disagreement,
                json.dumps(agent_explanations) if agent_explanations else None,
                json.dumps(shap_explanation) if shap_explanation else None,
            )


store = AuditStore()
router = APIRouter()


class AgentScore(BaseModel):
    risk_score: float = Field(..., ge=0.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)


class SynthesisRequest(BaseModel):
    txn_id: str = Field(..., min_length=1)
    txn_type: str = Field(..., min_length=1, description="RAW dataset value, e.g. ESEWA_P2P")
    velocity: AgentScore
    geo: AgentScore
    graph: AgentScore | None = None
    behavior: AgentScore | None = None


class SynthesisResponse(BaseModel):
    txn_id: str
    agent_name: str = "synthesis-agent"
    final_score: float = Field(..., ge=0.0, le=1.0)
    decision: str
    otp_forced_by_disagreement: bool
    fraud_pattern: str
    disagreement_score: float = Field(..., ge=0.0)
    agents_used: list[str]
    txn_type_mapped: str
    layer1_weights: dict[str, float]
    layer2_weights: dict[str, float]
    weights_applied: dict[str, float]
    latency_ms: float = Field(..., ge=0.0)


@router.post("/agents/synthesis/evaluate", response_model=SynthesisResponse)
async def evaluate_synthesis(body: SynthesisRequest) -> SynthesisResponse:
    if store.pool is None:
        raise HTTPException(status_code=503, detail="synthesis agent: postgres unavailable")

    started = time.perf_counter()
    scores = {"velocity": body.velocity, "geo": body.geo,
              "graph": body.graph, "behavior": body.behavior}
    verdicts = {
        agent: AgentVerdict(risk_score=s.risk_score, confidence=s.confidence, latency_ms=0)
        for agent, s in scores.items() if s is not None
    }
    mapped = map_txn_type(body.txn_type)
    try:
        result = synthesise(verdicts, mapped)
    except ValueError as exc:  # zero-weight guard / degenerate input
        raise HTTPException(status_code=422, detail=str(exc)) from None
    fuse_ms = (time.perf_counter() - started) * 1000

    audit_started = time.perf_counter()
    try:
        await store.write(
            txn_id=body.txn_id,
            txn_type_raw=body.txn_type,
            txn_type_mapped=mapped.value,
            verdicts=verdicts,
            result=result,
        )
    except (asyncpg.PostgresError, OSError) as exc:
        logger.error("Synthesis audit write failed for %s: %s", body.txn_id, exc)
        raise HTTPException(status_code=503, detail="synthesis agent: postgres unavailable") from None
    audit_ms = (time.perf_counter() - audit_started) * 1000

    latency_ms = fuse_ms + audit_ms
    if fuse_ms > SYNTHESIS_BUDGET_MS:
        logger.warning(
            "Synthesis math for %s took %.2fms (> %.1fms Table V budget) — "
            "the pure fusion itself overran, not the audit write",
            body.txn_id, fuse_ms, SYNTHESIS_BUDGET_MS)
    elif latency_ms > SYNTHESIS_BUDGET_MS:
        logger.warning(
            "Synthesis endpoint for %s took %.2fms (> %.1fms Table V budget): "
            "math %.2fms, postgres audit write %.2fms — bottleneck is the audit write",
            body.txn_id, latency_ms, SYNTHESIS_BUDGET_MS, fuse_ms, audit_ms)

    return SynthesisResponse(
        txn_id=body.txn_id,
        final_score=result.final_score,
        decision=result.decision.value,
        otp_forced_by_disagreement=result.otp_forced_by_disagreement,
        fraud_pattern=result.fraud_pattern.value,
        disagreement_score=result.disagreement_score,
        agents_used=result.agents_used,
        txn_type_mapped=mapped.value,
        layer1_weights=result.layer1_weights.model_dump(),
        layer2_weights=result.layer2_weights.model_dump(),
        weights_applied=result.weights_applied.model_dump(),
        latency_ms=round(latency_ms, 3),
    )


@router.get("/agents/synthesis/health")
async def health() -> dict[str, object]:
    checks: dict[str, str] = {}
    if store.pool is None:
        checks["postgres"] = "unavailable"
    else:
        try:
            async with store.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            checks["postgres"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["postgres"] = f"unavailable: {type(exc).__name__}"
    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"service": "synthesis-agent", "status": status, "checks": checks}


# -- standalone app (same dual pattern as behavior_agent/api.py) ---------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_mapping_table()
    try:
        await store.connect()
        logger.info("✅ Synthesis Agent ready (asyncpg pool open, audit table ensured)")
    except (asyncpg.PostgresError, OSError) as exc:
        logger.error("Synthesis Agent: postgres unavailable at startup: %s", exc)
    yield
    await store.close()


app = FastAPI(title="Synthesis Agent", version="1.0.0", lifespan=lifespan)
app.include_router(router)
