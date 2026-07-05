"""Run each agent behind a uniform ``AgentOutcome`` and fuse the results.

Every agent takes different inputs and raises different errors; this module
normalizes all of that into one shape (``status`` + ``risk_score`` +
``confidence`` + ``explanation`` + ``latency_ms``) so callers never special-case
an agent. An agent that is down / missing inputs / unknown to the DB returns a
non-``ok`` status and is simply omitted from the fusion — synthesise()
renormalizes the two-layer weights over whoever actually reported.

Graph (Neo4j network signals) participates in the weighted fusion alongside
velocity, geo, and behavior — each has a Table I/II column in
``shared/schemas/risk.py``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi.concurrency import run_in_threadpool
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from pydantic import BaseModel, Field

from agents.behavior_agent import (
    AllModelsFailedError,
    BehaviorAgent,
)
from agents.behavior_agent import (
    PostgresUnavailableError as BehaviorPostgresUnavailableError,
)
from agents.behavior_agent import (
    TxnNotFoundError as BehaviorTxnNotFoundError,
)
from agents.geo_agent import GeoAgent, PostgresUnavailableError
from agents.graph_agent import evaluate as graph_evaluate
from agents.synthesis_agent import synthesise
from agents.velocity_agent import RedisUnavailableError, VelocityAgent
from shared.schemas.risk import AgentVerdict, SynthesisResult, TransactionType
from shared.schemas.transaction import TransactionEvent
from synthesis_agent.txn_type_mapping import map_txn_type

# Agents that participate in the weighted fusion (paper §IV-E + graph network).
FUSION_AGENTS: tuple[str, ...] = ("velocity", "geo", "graph", "behavior")


@dataclass
class PipelineTxn:
    """The normalized transaction every agent slices what it needs from."""

    txn_id: str
    account_id: str
    txn_type: str  # RAW dataset value, e.g. ESEWA_P2P
    amount: float = 0.0
    currency: str = "NPR"
    timestamp: datetime | None = None
    device_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    def when(self) -> datetime:
        return self.timestamp or datetime.now(timezone.utc)

    def to_velocity_event(self) -> TransactionEvent:
        return TransactionEvent(
            transaction_id=self.txn_id, user_id=self.account_id, amount=self.amount,
            currency=self.currency, timestamp=self.when(), txn_type=self.txn_type,
            device_id=self.device_id, latitude=self.latitude, longitude=self.longitude)


class AgentOutcome(BaseModel):
    status: str  # "ok" | "unavailable" | "not_found" | "skipped" | "error"
    risk_score: float | None = None
    confidence: float | None = None
    explanation: str | dict | None = None
    latency_ms: float | None = None
    detail: str | None = None


def _ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 3)


async def run_velocity(agent: VelocityAgent, txn: PipelineTxn) -> AgentOutcome:
    started = time.monotonic()
    try:
        risk, confidence = await agent.evaluate(txn.to_velocity_event())
        return AgentOutcome(
            status="ok", risk_score=risk, confidence=confidence, latency_ms=_ms(started),
            explanation=f"velocity risk {risk:.2f} at confidence {confidence:.2f} "
                        "from Redis sliding-window counters")
    except RedisUnavailableError:
        return AgentOutcome(status="unavailable", detail="redis unavailable",
                            latency_ms=_ms(started))


async def run_geo(agent: GeoAgent, txn: PipelineTxn) -> AgentOutcome:
    if agent.pg_pool is None:
        return AgentOutcome(status="unavailable", detail="postgres not connected")
    if txn.device_id is None or txn.latitude is None or txn.longitude is None:
        return AgentOutcome(status="skipped",
                            detail="device_id/latitude/longitude required")
    started = time.monotonic()
    try:
        risk, confidence, signals = await agent.evaluate(
            account_id=txn.account_id, txn_id=txn.txn_id, device_id=txn.device_id,
            latitude=txn.latitude, longitude=txn.longitude, timestamp=txn.when())
        return AgentOutcome(status="ok", risk_score=risk, confidence=confidence,
                            explanation=signals, latency_ms=_ms(started))
    except (RedisUnavailableError, PostgresUnavailableError) as exc:
        return AgentOutcome(status="unavailable", detail=type(exc).__name__,
                            latency_ms=_ms(started))


async def run_behavior(agent: BehaviorAgent, model_error: str | None,
                       txn: PipelineTxn) -> AgentOutcome:
    if model_error is not None:
        return AgentOutcome(status="unavailable", detail="model artifacts missing")
    if agent.pg_pool is None:
        return AgentOutcome(status="unavailable", detail="postgres not connected")
    started = time.monotonic()
    try:
        verdict, latency_ms = await agent.evaluate_timed(txn.account_id, txn.txn_id)
        return AgentOutcome(
            status="ok", risk_score=verdict.risk_score, confidence=verdict.confidence,
            explanation={"weights_profile": verdict.weights_profile,
                         "history_count": verdict.history_count,
                         "model_breakdown": verdict.model_breakdown},
            latency_ms=round(latency_ms, 3))
    except BehaviorTxnNotFoundError:
        return AgentOutcome(status="not_found",
                            detail="txn/account unknown to behavior agent",
                            latency_ms=_ms(started))
    except AllModelsFailedError as exc:
        return AgentOutcome(status="error", detail=str(exc), latency_ms=_ms(started))
    except BehaviorPostgresUnavailableError:
        return AgentOutcome(status="unavailable", detail="postgres unavailable",
                            latency_ms=_ms(started))


async def run_graph(driver, database: str, txn: PipelineTxn) -> AgentOutcome:
    """Neo4j account-network score — fused when status is ``ok``."""
    if driver is None:
        return AgentOutcome(status="unavailable", detail="neo4j unavailable")
    started = time.monotonic()

    def _run() -> dict:
        with driver.session(database=database) as session:
            return graph_evaluate(txn.account_id, session)

    try:
        result = await run_in_threadpool(_run)
    except (ServiceUnavailable, Neo4jError) as exc:
        return AgentOutcome(status="unavailable", detail=type(exc).__name__,
                            latency_ms=_ms(started))
    if "error" in result:
        return AgentOutcome(status="not_found", detail=result["error"],
                            latency_ms=_ms(started))
    return AgentOutcome(status="ok", risk_score=result["graph_score"], confidence=1.0,
                        explanation={"flag": result["flag"], "decision": result["decision"],
                                     "reasons": result["reasons"]},
                        latency_ms=_ms(started))


def build_verdicts(outcomes: dict[str, AgentOutcome]) -> dict[str, AgentVerdict]:
    """Keep only agents that produced a usable score; the rest are omitted."""
    return {
        name: AgentVerdict(risk_score=o.risk_score, confidence=o.confidence, latency_ms=0)
        for name, o in outcomes.items()
        if name in FUSION_AGENTS and o.status == "ok"
        and o.risk_score is not None and o.confidence is not None
    }


def fuse(outcomes: dict[str, AgentOutcome],
         txn_type_raw: str) -> tuple[SynthesisResult, TransactionType, dict[str, AgentVerdict]]:
    """Map the txn_type, build verdicts, and run the pure synthesis + decision.

    Thresholds come from pipeline.decision_settings (admin-tunable via Redis,
    defaults to the paper's constants), so both the API process and the
    orchestrator pick up Settings changes within seconds.
    """
    from pipeline.decision_settings import current_config

    verdicts = build_verdicts(outcomes)
    mapped = map_txn_type(txn_type_raw)
    if not verdicts:
        raise ValueError(
            "no fusion agent produced a verdict — "
            + "; ".join(f"{n}: {o.status}" for n, o in outcomes.items()
                        if n in FUSION_AGENTS))
    return synthesise(verdicts, mapped, cfg=current_config()), mapped, verdicts
