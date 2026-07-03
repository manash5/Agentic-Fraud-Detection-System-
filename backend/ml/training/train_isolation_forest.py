"""Train the Isolation Forest anomaly detector (cold-start fallback model).

Trained unsupervised on all 400k rows; labels are used for reference-only
evaluation. Serves accounts with <50 transactions where sequence/supervised
signals are weak.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import joblib
from sklearn.ensemble import IsolationForest

from ml.training.common import (
    MODELS_DIR,
    log_mlflow_run,
    measure_latency_ms,
    print_box,
    save_metrics,
    summarize_scores,
)
from ml.training.prepare_features import DEFAULT_LABELED_TABLE, prepare_features

EXPERIMENT_NAME = "behavior_agent_isolation_forest"
MODEL_FILENAME = "isolation_forest_model.pkl"

CONTAMINATION = 0.0183  # empirical fraud rate
N_ESTIMATORS = 200
RANDOM_STATE = 42


def train_isolation_forest(feature_table_path: Path | None = None) -> dict:
    started = time.perf_counter()
    X, y, feature_cols = prepare_features(feature_table_path, save_columns=False, verbose=False)

    model = IsolationForest(
        n_estimators=N_ESTIMATORS,
        contamination=CONTAMINATION,
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    model.fit(X)  # unsupervised — labels not used for training

    anomaly_flags = (model.predict(X) == -1).astype(int)
    anomaly_scores = -model.decision_function(X)
    # Normalize scores to [0, 1] for threshold-style metrics.
    score_range = anomaly_scores.max() - anomaly_scores.min()
    proba_like = (anomaly_scores - anomaly_scores.min()) / (score_range or 1.0)
    scores = summarize_scores(y, proba_like)

    flagged = int(anomaly_flags.sum())
    caught = int(((anomaly_flags == 1) & (y == 1)).sum())
    total_fraud = int(y.sum())
    flag_precision = caught / flagged if flagged else 0.0
    flag_recall = caught / total_fraud if total_fraud else 0.0
    flag_f1 = (
        2 * flag_precision * flag_recall / (flag_precision + flag_recall)
        if (flag_precision + flag_recall)
        else 0.0
    )

    single_row = X.iloc[[0]]
    latency_single = measure_latency_ms(lambda: model.decision_function(single_row))

    train_seconds = time.perf_counter() - started
    model_path = MODELS_DIR / MODEL_FILENAME
    joblib.dump(model, model_path)

    print_box(
        "ISOLATION FOREST RESULTS (reference only)",
        [
            [
                f"Contamination:    {CONTAMINATION:.4f}",
                f"n_estimators:        {N_ESTIMATORS}",
                f"AUROC:             {scores['auroc']:.3f}  (unsupervised — expected lower)",
                f"Anomalies flagged: {flagged:,}  (matching contamination rate)",
                f"Of those, actual fraud:  {caught:,} ({flag_precision:.1%} precision)",
                f"Fraud cases caught:      {caught:,} / {total_fraud:,} ({flag_recall:.1%} recall)",
                "Role: Cold-start fallback when user has <50 transactions",
                f"Inference latency (single txn): {latency_single:.0f}ms",
                f"Model saved: ml/models/{MODEL_FILENAME}",
            ],
        ],
    )

    metrics = {
        "model": "isolation_forest",
        "auroc": scores["auroc"],
        "pr_auc": scores["pr_auc"],
        "precision": flag_precision,
        "recall": flag_recall,
        "f1": flag_f1,
        "fpr": float(((anomaly_flags == 1) & (y == 0)).sum() / max(int((y == 0).sum()), 1)),
        "flagged": flagged,
        "caught": caught,
        "latency_single_ms": latency_single,
        "train_seconds": train_seconds,
        "path": str(model_path),
    }
    save_metrics("isolation_forest", metrics)
    log_mlflow_run(
        EXPERIMENT_NAME,
        "isolation_forest",
        params={
            "contamination": CONTAMINATION,
            "n_estimators": N_ESTIMATORS,
            "n_samples": len(X),
            "n_features": len(feature_cols),
        },
        metrics={
            "auroc": scores["auroc"],
            "precision": flag_precision,
            "recall": flag_recall,
            "f1": flag_f1,
        },
        model=model,
    )
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Isolation Forest anomaly detector")
    parser.add_argument("--feature-table", type=Path, default=DEFAULT_LABELED_TABLE)
    args = parser.parse_args()
    train_isolation_forest(args.feature_table)


if __name__ == "__main__":
    main()
