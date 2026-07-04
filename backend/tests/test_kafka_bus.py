"""Tests for the Kafka event bus (envelope, config, orchestrator workflow).

These are pure / mocked — no running Kafka broker required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kafka_bus.config import AGENT_COMPLETED_EVENT, EventType, ORCHESTRATOR_TRIGGERS
from kafka_bus.events import Event
from kafka_bus.orchestrator import Orchestrator
from pipeline.agent_runner import AgentOutcome, PipelineTxn


def test_event_roundtrip() -> None:
    event = Event.make(
        EventType.TRANSACTION_RECEIVED, "TXN-123",
        {"account_id": "ACC-1", "txn_type": "ESEWA_P2P"})
    restored = Event.from_bytes(event.to_bytes())
    assert restored.event_type == EventType.TRANSACTION_RECEIVED
    assert restored.transaction_id == "TXN-123"
    assert restored.payload["account_id"] == "ACC-1"


def test_orchestrator_only_triggers_on_transaction_received() -> None:
    assert EventType.TRANSACTION_RECEIVED in ORCHESTRATOR_TRIGGERS
    assert EventType.FINAL_DECISION not in ORCHESTRATOR_TRIGGERS
    assert EventType.VELOCITY_COMPLETED not in ORCHESTRATOR_TRIGGERS


def test_agent_completed_event_map_includes_graph() -> None:
    assert set(AGENT_COMPLETED_EVENT) == {"velocity", "geo", "graph", "behavior"}
    assert AGENT_COMPLETED_EVENT["graph"] == EventType.GRAPH_COMPLETED


@pytest.mark.asyncio
async def test_orchestrator_publishes_all_stage_events() -> None:
    """End-to-end workflow with mocked agents — no Kafka broker."""
    orch = Orchestrator()
    orch.producer = AsyncMock()
    orch.producer.publish = AsyncMock()

    ok = AgentOutcome(status="ok", risk_score=0.3, confidence=0.8, latency_ms=1.0)
    skipped = AgentOutcome(status="skipped", detail="no coords")

    with patch("kafka_bus.orchestrator.run_velocity", AsyncMock(return_value=ok)), \
         patch("kafka_bus.orchestrator.run_geo", AsyncMock(return_value=skipped)), \
         patch("kafka_bus.orchestrator.run_graph", AsyncMock(return_value=ok)), \
         patch("kafka_bus.orchestrator.run_behavior", AsyncMock(return_value=ok)), \
         patch("kafka_bus.orchestrator.synthesis_store.write", AsyncMock()):
        txn = PipelineTxn(
            txn_id="TXN-KAFKA-1", account_id="ACC-1002022", txn_type="ESEWA_P2P")
        await orch.handle_transaction(txn)

    published_types = [c.args[0].event_type for c in orch.producer.publish.call_args_list]
    assert EventType.VELOCITY_COMPLETED in published_types
    assert EventType.GEO_COMPLETED in published_types
    assert EventType.GRAPH_COMPLETED in published_types
    assert EventType.BEHAVIOR_COMPLETED in published_types
    assert EventType.SYNTHESIS_COMPLETED in published_types
    assert EventType.FINAL_DECISION in published_types
    assert published_types[-1] == EventType.FINAL_DECISION
