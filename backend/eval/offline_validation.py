"""Offline validation for deployed behavior models + rule baseline.

Scores already materialized in ``datasets_processed/`` are reused so validation
is fast and matches the training notebooks' held-out splits. MLflow logging is
optional and lives entirely off the real-time request path.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from eval.metrics import binary_metrics
from eval.mlflow_tracking import (
    champion_challenger_decision,
    load_champion_metrics,
    log_json_artifact,
    log_metrics,
    start_validation_run,
)
from eval.paths import (
    ISO_SCORED,
    LABELS_TRAIN,
    LSTM_METRICS,
    RULE_BASELINE,
    XGB_VAL_SCORED,
)


def validate_xgboost(*, log_mlflow: bool = False) -> dict[str, Any]:
    """Validate XGBoost on the notebook's time-split validation holdout."""
    df = pd.read_csv(XGB_VAL_SCORED)
    metrics = binary_metrics(
        df["is_fraud"].values, df["fraud_proba"].values,
        threshold=float(df["fraud_proba"].quantile(0.99)),  # operational high-recall cut
    )
    result = {"model": "xgboost", "metrics": metrics, "source": str(XGB_VAL_SCORED.name)}
    if log_mlflow:
        champion = load_champion_metrics("xgboost")
        with start_validation_run("xgboost", tags={"model_type": "supervised"}):
            log_metrics(metrics)
            gate = champion_challenger_decision(metrics, champion)
            log_json_artifact({"validation": result, "promotion_gate": gate}, "xgboost.json")
            if gate.get("delta") is not None:
                log_metrics({"promotion_delta": gate["delta"]}, prefix="gate_")
            result["promotion_gate"] = gate
    return result


def validate_isolation_forest(*, log_mlflow: bool = False,
                              sample_n: int | None = 200_000) -> dict[str, Any]:
    """Validate Isolation Forest anomaly scores on labeled transactions."""
    labels = pd.read_csv(LABELS_TRAIN, usecols=["txn_id", "is_fraud"])
    iso = pd.read_csv(ISO_SCORED, usecols=["txn_id", "anomaly_score"])
    if sample_n and len(iso) > sample_n:
        iso = iso.sample(sample_n, random_state=42)
    df = iso.merge(labels, on="txn_id", how="inner")
    metrics = binary_metrics(df["is_fraud"].values, df["anomaly_score"].values)
    result = {
        "model": "isolation_forest",
        "metrics": metrics,
        "source": str(ISO_SCORED.name),
        "labeled_rows": len(df),
    }
    if log_mlflow:
        champion = load_champion_metrics("isolation_forest")
        with start_validation_run("isolation_forest", tags={"model_type": "unsupervised"}):
            log_metrics(metrics)
            gate = champion_challenger_decision(metrics, champion)
            log_json_artifact({"validation": result, "promotion_gate": gate}, "isoforest.json")
            result["promotion_gate"] = gate
    return result


def validate_lstm(*, log_mlflow: bool = False) -> dict[str, Any]:
    """Surface LSTM test metrics exported at train time (sequence eval is costly)."""
    with open(LSTM_METRICS) as f:
        saved = json.load(f)
    metrics = {
        "n": int(saved.get("n_test", 0)),
        "pr_auc": float(saved["test_pr_auc"]),
        "auroc": float(saved["test_auroc"]),
        "recall_at_p20": float(saved["test_recall_at_p20"]),
        "best_f1": float(saved["test_best_f1"]),
        "fraud_rate": np.nan,
    }
    result = {"model": "lstm", "metrics": metrics, "source": LSTM_METRICS.name,
              "training_history_epochs": len(saved.get("history", []))}
    if log_mlflow:
        with start_validation_run("lstm", tags={"model_type": "sequence"}):
            log_metrics(metrics)
            log_json_artifact(saved, "lstm_training_metrics.json")
    return result


def validate_rule_baseline(*, log_mlflow: bool = False) -> dict[str, Any]:
    """Legacy rule engine baseline vs confirmed labels (for uplift reporting)."""
    labels = pd.read_csv(LABELS_TRAIN, usecols=["txn_id", "is_fraud"])
    base = pd.read_csv(RULE_BASELINE)
    df = base.merge(labels, on="txn_id", how="inner")
    # Treat FLAG/BLOCK as positive prediction
    y_pred = df["baseline_decision"].isin(["FLAG", "BLOCK"]).astype(int).values
    y_true = df["is_fraud"].values
    metrics = {
        "n": int(len(df)),
        "fraud_rate": float(y_true.mean()),
        "precision": float((y_pred & y_true).sum() / max(y_pred.sum(), 1)),
        "recall": float((y_pred & y_true).sum() / max(y_true.sum(), 1)),
        "flag_rate": float(y_pred.mean()),
    }
    result = {"model": "rule_engine_baseline", "metrics": metrics,
              "source": RULE_BASELINE.name}
    if log_mlflow:
        with start_validation_run("rule_baseline", tags={"model_type": "baseline"}):
            log_metrics(metrics)
    return result


def run_all(*, log_mlflow: bool = False) -> dict[str, Any]:
    """Validate all behavior models + baseline; optionally log to MLflow."""
    results = {
        "xgboost": validate_xgboost(log_mlflow=log_mlflow),
        "isolation_forest": validate_isolation_forest(log_mlflow=log_mlflow),
        "lstm": validate_lstm(log_mlflow=log_mlflow),
        "rule_baseline": validate_rule_baseline(log_mlflow=log_mlflow),
    }
    summary = {
        name: r["metrics"].get("pr_auc", r["metrics"].get("recall"))
        for name, r in results.items()
    }
    return {"results": results, "summary": summary}
