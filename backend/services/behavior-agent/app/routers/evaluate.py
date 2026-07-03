from __future__ import annotations

import time

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.model_loader import (
    BEHAVIOR_FEATURE_NAMES,
    predict_risk,
    vector_from_row,
)
from shared.constants.service_names import BEHAVIOR_AGENT
from shared.explainability.shap_utils import compute_shap_for_xgboost, contributions_to_explanation
from shared.schemas.risk import AgentRiskResponse, SHAPExplanation

router = APIRouter(prefix="/evaluate", tags=["evaluate"])


class EvaluateRequest(BaseModel):
    transaction_id: str
    features: list[float] | None = Field(
        default=None,
        description="Legacy 10-dim heuristic vector. Ignored when a trained model + feature row exist.",
    )


@router.post("/risk", response_model=AgentRiskResponse)
async def evaluate_risk(body: EvaluateRequest, request: Request) -> AgentRiskResponse:
    models = request.app.state.models
    feature_index = request.app.state.feature_index
    started = time.perf_counter()

    shap_names: list[str]
    if models.feature_columns and body.transaction_id in feature_index:
        row = feature_index[body.transaction_id]
        feature_array = vector_from_row(row, models.feature_columns)
        shap_names = models.feature_columns
    elif body.features is not None:
        if len(body.features) != len(BEHAVIOR_FEATURE_NAMES):
            raise HTTPException(
                status_code=422,
                detail=f"features must have {len(BEHAVIOR_FEATURE_NAMES)} values for heuristic mode",
            )
        feature_array = np.asarray(body.features, dtype=float)
        shap_names = BEHAVIOR_FEATURE_NAMES
    else:
        raise HTTPException(
            status_code=404,
            detail=(
                f"transaction_id '{body.transaction_id}' not in feature table; "
                "provide features[] for heuristic mode"
            ),
        )

    risk_score, model_used = predict_risk(models, feature_array)
    shap = _compute_shap(models, feature_array, model_used, shap_names)

    confidence = 0.85 if model_used != "heuristic" else 0.60
    reasons = [f"model={model_used}"]
    if model_used == "heuristic":
        reasons.append("no trained model artifact — heuristic fallback")

    return AgentRiskResponse(
        transaction_id=body.transaction_id,
        agent_name=BEHAVIOR_AGENT,
        risk_score=risk_score,
        confidence_score=confidence,
        reasons=reasons,
        shap=shap,
    )


def _compute_shap(
    models,
    features: np.ndarray,
    model_used: str,
    feature_names: list[str],
) -> SHAPExplanation | None:
    matrix = np.asarray(features, dtype=float).reshape(1, -1)
    try:
        if model_used == "xgboost" and models.xgboost is not None:
            return compute_shap_for_xgboost(models.xgboost, matrix, feature_names)
    except Exception:
        pass

    weights = np.ones(min(len(feature_names), matrix.shape[1])) / max(matrix.shape[1], 1)
    contrib = matrix.reshape(-1)[: len(weights)] * weights
    return contributions_to_explanation(feature_names[: len(contrib)], contrib, base_value=0.0)
