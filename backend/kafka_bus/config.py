"""Kafka connection + topic settings, all overridable by environment variable.

Nothing here is hard-coded into the producer/consumer code — a deployment can
point at a different broker or topic purely through env vars.
"""

from __future__ import annotations

import os

# Comma-separated list, e.g. "localhost:9092,localhost:9093".
BOOTSTRAP_SERVERS: str = os.environ.get("FRAUD_KAFKA_BOOTSTRAP", "localhost:9092")

# The single topic every pipeline event flows through (paper: one event bus).
TOPIC: str = os.environ.get("FRAUD_KAFKA_TOPIC", "fraud-events")

# Consumer group for the orchestrator; a second replica in the same group would
# share the partitions (horizontal scale-out) rather than double-processing.
ORCHESTRATOR_GROUP: str = os.environ.get(
    "FRAUD_KAFKA_ORCHESTRATOR_GROUP", "fraud-orchestrator")


# -- event types (the ``event_type`` field on every envelope) -----------------


class EventType:
    TRANSACTION_RECEIVED = "transaction_received"
    VELOCITY_COMPLETED = "velocity_completed"
    GEO_COMPLETED = "geo_completed"
    GRAPH_COMPLETED = "graph_completed"
    BEHAVIOR_COMPLETED = "behavior_completed"
    SYNTHESIS_COMPLETED = "synthesis_completed"
    FINAL_DECISION = "final_decision"


# Events the orchestrator ACTS on. It ignores everything else it emits, so the
# single shared topic never causes it to reprocess its own output.
ORCHESTRATOR_TRIGGERS: frozenset[str] = frozenset({EventType.TRANSACTION_RECEIVED})

# Per-agent completion event type, for the orchestrator's fan-out loop.
AGENT_COMPLETED_EVENT: dict[str, str] = {
    "velocity": EventType.VELOCITY_COMPLETED,
    "geo": EventType.GEO_COMPLETED,
    "graph": EventType.GRAPH_COMPLETED,
    "behavior": EventType.BEHAVIOR_COMPLETED,
}
