"""Shared helpers for the offline training pipeline.

Real-data pipeline: 400k labeled rows, 1.83% fraud. Every trainer evaluates
against the rule-engine baseline and persists a ``metrics_<model>.json`` so
``run_all_training`` can assemble the final summary table from real numbers.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LABELED_TABLE = BACKEND_ROOT / "datasets_processed" / "feature_table_labeled.csv"
MODELS_DIR = BACKEND_ROOT / "ml" / "models"

# Rule-engine baseline from the hackathon data dictionary.
BASELINE: dict[str, float] = {"auroc": 0.71, "recall": 0.62, "f1": 0.54, "fpr": 0.14}

# Hackathon targets.
TARGETS: dict[str, float] = {"auroc": 0.93, "recall": 0.88, "f1": 0.80, "fpr": 0.005}

BOX_INNER_WIDTH = 62


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def evaluate_at_threshold(y_true, proba, threshold: float) -> dict[str, float]:
    y_pred = (np.asarray(proba) >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {
        "threshold": threshold,
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "fpr": float(fp / (fp + tn)) if (fp + tn) else 0.0,
        "tn": int(tn),
        "fp": int(fp),
        "fn": int(fn),
        "tp": int(tp),
    }


def summarize_scores(y_true, proba) -> dict[str, Any]:
    y_arr = np.asarray(y_true)
    return {
        "auroc": float(roc_auc_score(y_arr, proba)) if len(np.unique(y_arr)) > 1 else 0.0,
        "pr_auc": float(average_precision_score(y_arr, proba)),
        "thr_05": evaluate_at_threshold(y_arr, proba, 0.5),
        "thr_08": evaluate_at_threshold(y_arr, proba, 0.8),
    }


def baseline_comparison_rows(auroc: float, recall: float, fpr: float, f1: float) -> list[str]:
    def pct(new: float, old: float) -> str:
        delta = (new - old) / old * 100
        arrow = "↑" if delta >= 0 else "↓"
        return f"({delta:+.1f}% {arrow})"

    return [
        "vs BASELINE (Rule Engine):",
        f"AUROC:     {auroc:.3f} vs {BASELINE['auroc']:.3f}  {pct(auroc, BASELINE['auroc'])}",
        f"Recall:    {recall:.3f} vs {BASELINE['recall']:.3f}  {pct(recall, BASELINE['recall'])}",
        f"FPR:       {fpr:.3f} vs {BASELINE['fpr']:.3f}  {pct(fpr, BASELINE['fpr'])}",
        f"F1:        {f1:.3f} vs {BASELINE['f1']:.3f}  {pct(f1, BASELINE['f1'])}",
    ]


# ---------------------------------------------------------------------------
# Latency measurement
# ---------------------------------------------------------------------------
def measure_latency_ms(fn: Callable[[], Any], repeats: int = 20) -> float:
    """Median wall-clock latency of ``fn`` in milliseconds (after one warmup)."""
    fn()
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return float(np.median(samples))


# ---------------------------------------------------------------------------
# Metrics persistence (consumed by run_all_training)
# ---------------------------------------------------------------------------
def save_metrics(model_name: str, payload: dict[str, Any]) -> Path:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    path = MODELS_DIR / f"metrics_{model_name}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_metrics(model_name: str) -> dict[str, Any] | None:
    path = MODELS_DIR / f"metrics_{model_name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Box printing
# ---------------------------------------------------------------------------
def _display_width(text: str) -> int:
    # Box-drawing output only uses single-width glyphs; arrows/✓ count as 1.
    return len(text)


def print_box(title: str, sections: list[list[str]], width: int = BOX_INNER_WIDTH) -> None:
    """Print a ╔═╗-style box. ``sections`` are separated by ╠═╣ rules."""
    print("╔" + "═" * width + "╗")
    pad_title = title.center(width)
    print("║" + pad_title + "║")
    for section in sections:
        print("╠" + "═" * width + "╣")
        for line in section:
            content = "  " + line
            padding = width - _display_width(content)
            print("║" + content + " " * max(padding, 0) + "║")
    print("╚" + "═" * width + "╝")


def log_mlflow_run(
    experiment: str,
    run_name: str,
    params: dict[str, Any],
    metrics: dict[str, float],
    model: Any | None = None,
) -> None:
    """Best-effort MLflow logging — never fails the training run."""
    try:
        import mlflow

        from ml.mlflow.config import MLFLOW_TRACKING_URI

        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        mlflow.set_experiment(experiment)
        with mlflow.start_run(run_name=run_name):
            for key, value in params.items():
                mlflow.log_param(key, value)
            for key, value in metrics.items():
                mlflow.log_metric(key, value)
            if model is not None:
                try:
                    mlflow.sklearn.log_model(model, artifact_path="model")
                except Exception:
                    pass
    except Exception as exc:  # pragma: no cover
        print(f"WARNING: MLflow logging skipped ({exc!r})")
