"""SHAP explainability helpers for tree-based fraud models."""

from __future__ import annotations

from typing import Any

import numpy as np
from shared.schemas.risk import SHAPContribution, SHAPExplanation


def _direction(value: float) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "neutral"


def contributions_to_explanation(
    feature_names: list[str],
    shap_values: np.ndarray,
    *,
    base_value: float = 0.0,
) -> SHAPExplanation:
    """Build a standard SHAPExplanation from a 1-D contribution vector."""
    flat = np.asarray(shap_values, dtype=float).reshape(-1)
    contributions = [
        SHAPContribution(
            feature=name,
            value=float(val),
            direction=_direction(float(val)),
        )
        for name, val in zip(feature_names, flat, strict=True)
    ]
    return SHAPExplanation.from_contributions(contributions, base_value=base_value)


def compute_shap_for_xgboost(
    model: Any,
    features: np.ndarray,
    feature_names: list[str],
) -> SHAPExplanation:
    """Compute SHAP values for an XGBoost classifier/regressor."""
    import shap

    matrix = np.asarray(features, dtype=float)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)

    explainer = shap.TreeExplainer(model)
    shap_output = explainer.shap_values(matrix)
    if isinstance(shap_output, list):
        shap_output = shap_output[1] if len(shap_output) > 1 else shap_output[0]

    values = np.asarray(shap_output, dtype=float).reshape(-1)
    base = float(np.asarray(explainer.expected_value).reshape(-1)[0])
    return contributions_to_explanation(feature_names, values, base_value=base)


def compute_shap_values(
    feature_vector: np.ndarray,
    model: Any,
    feature_names: list[str] | None = None,
) -> np.ndarray:
    """Return raw SHAP values for callers that need custom top-k formatting."""
    import shap

    matrix = np.asarray(feature_vector, dtype=float)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)

    explainer = shap.TreeExplainer(model)
    shap_output = explainer.shap_values(matrix)
    if isinstance(shap_output, list):
        shap_output = shap_output[1] if len(shap_output) > 1 else shap_output[0]
    return np.asarray(shap_output, dtype=float).reshape(-1)


def compute_shap_for_random_forest(
    model: Any,
    features: np.ndarray,
    feature_names: list[str],
) -> SHAPExplanation:
    """Compute SHAP values for a scikit-learn RandomForest model."""
    import shap

    matrix = np.asarray(features, dtype=float)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)

    explainer = shap.TreeExplainer(model)
    shap_output = explainer.shap_values(matrix)
    if isinstance(shap_output, list):
        shap_output = shap_output[1] if len(shap_output) > 1 else shap_output[0]

    values = np.asarray(shap_output, dtype=float).reshape(-1)
    base_arr = np.asarray(explainer.expected_value).reshape(-1)
    base = float(base_arr[1] if base_arr.size > 1 else base_arr[0])
    return contributions_to_explanation(feature_names, values, base_value=base)
