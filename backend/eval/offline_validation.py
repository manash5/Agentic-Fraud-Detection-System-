"""Offline validation against backend/datasets — precision, recall, F1, AUROC, PR-AUC."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


def load_eval_frame(data_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    labels = pd.read_csv(data_dir / "fraud_labels_eval_HIDDEN.csv")
    preds = pd.read_csv(data_dir / "rule_engine_baseline_predictions.csv")
    merged = preds.merge(labels, on="txn_id", how="inner")
    y_true = merged["is_fraud"].astype(int).values
    if "confidence" in merged.columns:
        y_score = merged["confidence"].astype(float).values
    else:
        y_score = (merged.get("baseline_decision", pd.Series(["ALLOW"] * len(merged))) != "ALLOW").astype(float).values
    return y_true, y_score


def evaluate(y_true: np.ndarray, y_score: np.ndarray, threshold: float = 0.5) -> dict[str, float]:
    y_pred = (y_score >= threshold).astype(int)
    return {
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "auroc": float(roc_auc_score(y_true, y_score)) if len(np.unique(y_true)) > 1 else 0.0,
        "pr_auc": float(average_precision_score(y_true, y_score)) if len(np.unique(y_true)) > 1 else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline fraud model validation")
    parser.add_argument("--data-dir", type=Path, default=Path("backend/datasets"))
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    y_true, y_score = load_eval_frame(args.data_dir)
    metrics = evaluate(y_true, y_score, threshold=args.threshold)

    print("Offline validation metrics")
    print("-" * 32)
    for name, value in metrics.items():
        print(f"  {name:12s}: {value:.4f}")


if __name__ == "__main__":
    main()
