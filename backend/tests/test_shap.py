"""SHAP helper tests — no live models required."""

from __future__ import annotations

import numpy as np

from shared.explainability.shap_utils import contributions_to_explanation, shap_to_compact


def test_top_k_shap_truncates_features() -> None:
    names = [f"f{i}" for i in range(20)]
    values = np.arange(20, dtype=float)
    expl = contributions_to_explanation(names, values, base_value=0.5, top_k=3)
    assert len(expl.feature_names) == 3
    assert expl.feature_names[0] == "f19"  # largest |value|
    compact = shap_to_compact(expl)
    assert compact["base_value"] == 0.5
    assert len(compact["top_features"]) == 3
