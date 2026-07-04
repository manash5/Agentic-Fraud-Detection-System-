"""Unit tests for eval metrics (no MLflow / datasets required)."""

from __future__ import annotations

import numpy as np

from eval.metrics import binary_metrics


def test_binary_metrics_perfect_separation() -> None:
    y = np.array([0, 0, 0, 1, 1])
    s = np.array([0.1, 0.2, 0.3, 0.8, 0.9])
    m = binary_metrics(y, s)
    assert m["pr_auc"] == 1.0
    assert m["auroc"] == 1.0


def test_binary_metrics_handles_empty_class_gracefully() -> None:
    m = binary_metrics(np.zeros(5), np.linspace(0, 1, 5))
    assert "error" in m
