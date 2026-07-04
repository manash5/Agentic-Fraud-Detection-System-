"""The one message envelope every pipeline event uses.

Contract (identical for every stage):

    {
        "event_type":     "transaction_received" | ... | "final_decision",
        "transaction_id": "TXN-...",
        "timestamp":      ISO-8601 UTC,
        "payload":        {stage-specific body}
    }

Keeping a single envelope means a consumer only ever branches on
``event_type`` and can treat ``payload`` opaquely until it cares.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Event(BaseModel):
    event_type: str
    transaction_id: str
    timestamp: str = Field(default_factory=_utcnow_iso)
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def make(cls, event_type: str, transaction_id: str,
             payload: dict[str, Any] | None = None) -> "Event":
        return cls(event_type=event_type, transaction_id=transaction_id,
                   payload=payload or {})

    def to_bytes(self) -> bytes:
        """Serialize for Kafka. ``default=str`` handles datetimes/enums safely."""
        return json.dumps(self.model_dump(), default=str).encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Event":
        return cls(**json.loads(raw.decode("utf-8")))
