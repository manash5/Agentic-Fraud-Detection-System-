"""Offline classification metrics for imbalanced fraud detection."""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)


def binary_metrics(y_true: np.ndarray, y_score: np.ndarray,
                   *, threshold: float | None = None) -> dict[str, Any]:
    """Compute standard metrics; PR-AUC is primary for imbalanced fraud."""
    y_true = np.asarray(y_true, dtype=int)
    y_score = np.asarray(y_score, dtype=float)
    mask = np.isfinite(y_score)
    y_true, y_score = y_true[mask], y_score[mask]
    if len(y_true) == 0 or len(np.unique(y_true)) < 2:
        return {"n": int(len(y_true)), "error": "need labeled rows with both classes"}

    out: dict[str, Any] = {
        "n": int(len(y_true)),
        "fraud_rate": float(y_true.mean()),
        "pr_auc": float(average_precision_score(y_true, y_score)),
        "auroc": float(roc_auc_score(y_true, y_score)),
    }
    precisions, recalls, thresholds = precision_recall_curve(y_true, y_score)
    f1s = 2 * precisions * recalls / np.maximum(precisions + recalls, 1e-12)
    best_idx = int(np.argmax(f1s))
    out["best_f1"] = float(f1s[best_idx])
    out["best_f1_threshold"] = float(thresholds[min(best_idx, len(thresholds) - 1)])

    # Recall at 20% precision (operational constraint from paper eval section)
    p20_idx = np.where(precisions >= 0.20)[0]
    out["recall_at_p20"] = float(recalls[p20_idx[0]]) if len(p20_idx) else 0.0

    thr = threshold if threshold is not None else out["best_f1_threshold"]
    y_pred = (y_score >= thr).astype(int)
    out["threshold_used"] = float(thr)
    out["precision"] = float(precision_score(y_true, y_pred, zero_division=0))
    out["recall"] = float(recall_score(y_true, y_pred, zero_division=0))
    out["f1"] = float(f1_score(y_true, y_pred, zero_division=0))
    return out
