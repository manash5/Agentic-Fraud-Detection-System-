"""Shared audit write for /evaluate and the Kafka orchestrator."""

from __future__ import annotations

import logging
from typing import Any

from pipeline.agent_runner import AgentOutcome
from pipeline.explanations import collect_explanations, primary_shap_summary
from synthesis_agent.api import store as synthesis_store

logger = logging.getLogger("pipeline-audit")


async def write_pipeline_audit(
    *,
    txn_id: str,
    txn_type_raw: str,
    txn_type_mapped: str,
    verdicts: dict,
    result: Any,
    outcomes: dict[str, AgentOutcome],
) -> None:
    """Best-effort synchronous audit — never blocks the decision on failure."""
    try:
        await synthesis_store.write(
            txn_id=txn_id,
            txn_type_raw=txn_type_raw,
            txn_type_mapped=txn_type_mapped,
            verdicts=verdicts,
            result=result,
            agent_explanations=collect_explanations(outcomes),
            shap_explanation=primary_shap_summary(outcomes),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Pipeline audit write failed for %s: %s", txn_id, exc)
