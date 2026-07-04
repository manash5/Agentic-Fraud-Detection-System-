"""The pipeline orchestrator — a Kafka consumer that coordinates the workflow.

It is the ONLY component that decides what runs when. Kafka just moves bytes.

For every ``transaction_received`` event on ``fraud-events`` it:

  1. runs velocity + geo + graph + behavior IN PARALLEL (asyncio.gather),
  2. publishes a ``<agent>_completed`` event as each finishes,
  3. fuses the verdicts via the pure ``synthesise()`` (Layer1/Layer2 + Eq.2),
     publishes ``synthesis_completed``,
  4. applies the decision layer (PASS/OTP/BLOCK, already inside synthesise) and
     publishes ``final_decision`` back to the same topic,
  5. writes the synchronous Postgres audit record.

Agents never call each other; they are invoked only here and communicate purely
by the events this orchestrator emits. Run it as its own process::

    uv run python -m kafka_bus.orchestrator

It owns its own agent instances (its own Redis/Postgres/Neo4j connections), so
it is fully decoupled from the FastAPI app that publishes transactions.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

if "REDIS_HOST" in os.environ:
    os.environ.setdefault("FRAUD_REDIS_HOST", os.environ["REDIS_HOST"])

from aiokafka import AIOKafkaConsumer  # noqa: E402

from agents.behavior_agent import BehaviorAgent, ModelMissingError  # noqa: E402
from agents.behavior_agent import (  # noqa: E402
    PostgresUnavailableError as BehaviorPostgresUnavailableError,
)
from agents.geo_agent import GeoAgent  # noqa: E402
from agents.graph_agent import NEO4J_DATABASE, get_driver, graph_counts  # noqa: E402
from agents.velocity_agent import VelocityAgent  # noqa: E402
from kafka_bus import config  # noqa: E402
from kafka_bus.config import AGENT_COMPLETED_EVENT, EventType  # noqa: E402
from kafka_bus.events import Event  # noqa: E402
from kafka_bus.producer import EventProducer  # noqa: E402
from pipeline.agent_runner import (  # noqa: E402
    FUSION_AGENTS,
    AgentOutcome,
    PipelineTxn,
    fuse,
    run_behavior,
    run_geo,
    run_graph,
    run_velocity,
)
from pipeline.audit import write_pipeline_audit  # noqa: E402
from pipeline.explanations import primary_shap_summary  # noqa: E402
from synthesis_agent.api import store as synthesis_store  # noqa: E402
from synthesis_agent.txn_type_mapping import log_mapping_table  # noqa: E402

logger = logging.getLogger("kafka-bus.orchestrator")


class Orchestrator:
    """Owns the agents + the producer; coordinates one transaction end-to-end."""

    def __init__(self) -> None:
        self.velocity = VelocityAgent()
        self.geo = GeoAgent()
        self.behavior = BehaviorAgent()
        self.graph_driver = None
        self.behavior_model_error: str | None = None
        self.producer = EventProducer()

    async def connect(self) -> None:
        """Wire up every backing store; a store being down degrades one agent,
        it does not stop the orchestrator (that agent just abstains)."""
        try:
            await self.geo.connect()
            logger.info("✅ Geo agent connected")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Geo agent could not connect: %s", exc)
        try:
            self.graph_driver = get_driver()
            with self.graph_driver.session(database=NEO4J_DATABASE) as s:
                graph_counts(s)
            logger.info("✅ Graph agent connected")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Graph agent could not connect: %s", exc)
        try:
            await self.behavior.connect()
            logger.info("✅ Behavior agent ready (models preloaded)")
        except ModelMissingError as exc:
            self.behavior_model_error = str(exc)
            logger.warning("Behavior agent: model artifacts missing: %s", exc)
        except BehaviorPostgresUnavailableError as exc:
            logger.warning("Behavior agent: postgres unavailable: %s", exc)
        try:
            await synthesis_store.connect()
            logger.info("✅ Synthesis audit store ready")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Synthesis audit store unavailable: %s", exc)
        await self.producer.start()

    async def close(self) -> None:
        await self.producer.stop()
        await self.geo.close()
        await self.behavior.close()
        await synthesis_store.close()
        if self.graph_driver is not None:
            self.graph_driver.close()

    async def _run_agent(self, name: str, txn: PipelineTxn) -> tuple[str, AgentOutcome]:
        """Run one fusion agent and publish its ``*_completed`` event."""
        if name == "velocity":
            outcome = await run_velocity(self.velocity, txn)
        elif name == "geo":
            outcome = await run_geo(self.geo, txn)
        elif name == "graph":
            outcome = await run_graph(self.graph_driver, NEO4J_DATABASE, txn)
        else:
            outcome = await run_behavior(self.behavior, self.behavior_model_error, txn)
        await self.producer.publish(Event.make(
            AGENT_COMPLETED_EVENT[name], txn.txn_id,
            {"agent": name, **outcome.model_dump()}))
        return name, outcome

    async def handle_transaction(self, txn: PipelineTxn) -> None:
        """The whole workflow for one transaction (steps 1–5 in the module doc)."""
        # 1–2. fan out in parallel; each task publishes its own completion event.
        results = await asyncio.gather(
            *(self._run_agent(name, txn) for name in FUSION_AGENTS))
        outcomes = dict(results)

        # 3. fuse (pure) — omit agents that abstained; renormalize over the rest.
        try:
            result, mapped, verdicts = fuse(outcomes, txn.txn_type)
        except ValueError as exc:
            logger.error("Fusion produced nothing for %s: %s", txn.txn_id, exc)
            await self.producer.publish(Event.make(
                EventType.FINAL_DECISION, txn.txn_id,
                {"decision": "ERROR", "detail": str(exc)}))
            return
        await self.producer.publish(Event.make(
            EventType.SYNTHESIS_COMPLETED, txn.txn_id,
            {"final_score": result.final_score,
             "fraud_pattern": result.fraud_pattern.value,
             "disagreement_score": result.disagreement_score,
             "agents_used": result.agents_used,
             "weights_applied": result.weights_applied.model_dump()}))

        # 4. decision (already inside synthesise) -> publish final_decision.
        shap = primary_shap_summary(outcomes)
        await self.producer.publish(Event.make(
            EventType.FINAL_DECISION, txn.txn_id,
            {"decision": result.decision.value,
             "final_score": result.final_score,
             "otp_forced_by_disagreement": result.otp_forced_by_disagreement,
             "fraud_pattern": result.fraud_pattern.value,
             "agents_used": result.agents_used,
             "txn_type_mapped": mapped.value,
             "shap": shap}))
        logger.info("%s -> %s (score %.3f, pattern %s)", txn.txn_id,
                    result.decision.value, result.final_score, result.fraud_pattern.value)

        # 5. synchronous audit write (best-effort; never blocks the decision).
        await write_pipeline_audit(
            txn_id=txn.txn_id, txn_type_raw=txn.txn_type, txn_type_mapped=mapped.value,
            verdicts=verdicts, result=result, outcomes=outcomes)

    async def run(self) -> None:
        """Consume ``transaction_received`` events forever and coordinate each."""
        consumer = AIOKafkaConsumer(
            config.TOPIC,
            bootstrap_servers=config.BOOTSTRAP_SERVERS,
            group_id=config.ORCHESTRATOR_GROUP,
            enable_auto_commit=True,
            auto_offset_reset="latest",
        )
        await consumer.start()
        logger.info("Orchestrator consuming %s @ %s (group %s)",
                    config.TOPIC, config.BOOTSTRAP_SERVERS, config.ORCHESTRATOR_GROUP)
        try:
            async for msg in consumer:
                try:
                    event = Event.from_bytes(msg.value)
                except Exception as exc:  # noqa: BLE001 — never die on one bad message
                    logger.warning("Skipping unparseable message: %s", exc)
                    continue
                # Only react to triggers; ignore our own *_completed / decision events.
                if event.event_type not in config.ORCHESTRATOR_TRIGGERS:
                    continue
                p = event.payload
                try:
                    txn = PipelineTxn(
                        txn_id=event.transaction_id,
                        account_id=p["account_id"],
                        txn_type=p["txn_type"],
                        amount=p.get("amount", 0.0),
                        currency=p.get("currency", "NPR"),
                        timestamp=None,
                        device_id=p.get("device_id"),
                        latitude=p.get("latitude"),
                        longitude=p.get("longitude"))
                except KeyError as exc:
                    logger.warning("transaction_received missing field %s for %s",
                                   exc, event.transaction_id)
                    continue
                await self.handle_transaction(txn)
        finally:
            await consumer.stop()


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    log_mapping_table()
    orch = Orchestrator()
    await orch.connect()
    try:
        await orch.run()
    finally:
        await orch.close()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
