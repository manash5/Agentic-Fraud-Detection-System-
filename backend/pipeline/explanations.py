"""Collect per-agent explanations for the pipeline response and audit log."""

from __future__ import annotations

from typing import Any

from pipeline.agent_runner import AgentOutcome


def extract_behavior_shap(outcome: AgentOutcome) -> dict[str, Any] | None:
    """Primary SHAP block from behavior's XGBoost sub-model (top-k features)."""
    if outcome.status != "ok" or not isinstance(outcome.explanation, dict):
        return None
    breakdown = outcome.explanation.get("model_breakdown") or {}
    xgb = breakdown.get("xgboost") or {}
    shap = xgb.get("shap")
    return shap if isinstance(shap, dict) else None


def collect_explanations(outcomes: dict[str, AgentOutcome]) -> dict[str, Any]:
    """One explanation object per agent for the audit record."""
    out: dict[str, Any] = {}
    for name, o in outcomes.items():
        if o.status != "ok":
            out[name] = {"status": o.status, "detail": o.detail}
            continue
        out[name] = {
            "status": "ok",
            "risk_score": o.risk_score,
            "confidence": o.confidence,
            "explanation": o.explanation,
        }
    return out


def primary_shap_summary(outcomes: dict[str, AgentOutcome]) -> dict[str, Any] | None:
    """Top-level SHAP for the pipeline response (behavior XGBoost)."""
    behavior = outcomes.get("behavior")
    if behavior is None:
        return None
    shap = extract_behavior_shap(behavior)
    if shap is None:
        return None
    return {"model": "xgboost", **shap}
