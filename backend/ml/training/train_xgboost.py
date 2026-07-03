"""Train the Behavior Agent XGBoost classifier on the real 400k labeled rows."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import joblib
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

from ml.training.common import (
    MODELS_DIR,
    baseline_comparison_rows,
    log_mlflow_run,
    measure_latency_ms,
    print_box,
    save_metrics,
    summarize_scores,
)
from ml.training.prepare_features import DEFAULT_LABELED_TABLE, prepare_features

EXPERIMENT_NAME = "behavior_agent_xgboost"
MODEL_FILENAME = "xgboost_model.pkl"

TEST_SIZE = 0.2
RANDOM_STATE = 42


def train_xgboost(feature_table_path: Path | None = None) -> dict:
    started = time.perf_counter()
    X, y, feature_cols = prepare_features(feature_table_path)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=TEST_SIZE, stratify=y, random_state=RANDOM_STATE
    )
    n_pos = int(y_train.sum())
    n_neg = int(len(y_train) - n_pos)
    scale_pos_weight = n_neg / max(n_pos, 1)

    model = XGBClassifier(
        n_estimators=500,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos_weight,
        eval_metric="auc",
        early_stopping_rounds=30,
        tree_method="hist",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
    best_iteration = int(getattr(model, "best_iteration", model.n_estimators) or model.n_estimators)

    proba = model.predict_proba(X_test)[:, 1]
    scores = summarize_scores(y_test, proba)
    t05, t08 = scores["thr_05"], scores["thr_08"]

    single_row = X_test.iloc[[0]]
    batch_1000 = X_test.iloc[:1000]
    latency_single = measure_latency_ms(lambda: model.predict_proba(single_row))
    latency_batch = measure_latency_ms(lambda: model.predict_proba(batch_1000), repeats=10)

    importances = sorted(
        zip(feature_cols, model.feature_importances_), key=lambda kv: kv[1], reverse=True
    )[:10]

    train_seconds = time.perf_counter() - started
    model_path = MODELS_DIR / MODEL_FILENAME
    joblib.dump(model, model_path)

    print_box(
        "XGBOOST TRAINING RESULTS",
        [
            [
                f"Training rows:        {len(X_train):,}",
                f"Test rows:             {len(X_test):,}",
                f"Fraud in test:          {int(y_test.sum()):,} ({y_test.mean():.2%})",
                f"Best iteration:           {best_iteration}",
            ],
            [
                "METRIC              THRESHOLD=0.5    THRESHOLD=0.8",
                f"AUROC               {scores['auroc']:.3f}            {scores['auroc']:.3f}",
                f"PR-AUC              {scores['pr_auc']:.3f}            {scores['pr_auc']:.3f}",
                f"Precision           {t05['precision']:.3f}            {t08['precision']:.3f}",
                f"Recall              {t05['recall']:.3f}            {t08['recall']:.3f}",
                f"F1 Score            {t05['f1']:.3f}            {t08['f1']:.3f}",
                f"False Positive Rate {t05['fpr']:.3f}            {t08['fpr']:.3f}",
            ],
            [
                "Confusion Matrix (threshold=0.5)",
                f"True Negatives:    {t05['tn']:,}",
                f"False Positives:      {t05['fp']:,}",
                f"False Negatives:      {t05['fn']:,}",
                f"True Positives:     {t05['tp']:,}",
            ],
            baseline_comparison_rows(scores["auroc"], t05["recall"], t05["fpr"], t05["f1"]),
            [
                "Top 10 Features (by importance):",
                *[
                    f"{i}. {name:<28} {imp:.3f}"
                    for i, (name, imp) in enumerate(importances, start=1)
                ],
            ],
            [
                f"Inference latency (single txn):  {latency_single:.0f}ms",
                f"Inference latency (batch 1000):  {latency_batch:.0f}ms",
                f"Model saved: ml/models/{MODEL_FILENAME}",
            ],
        ],
    )

    metrics = {
        "model": "xgboost",
        "auroc": scores["auroc"],
        "pr_auc": scores["pr_auc"],
        "precision": t05["precision"],
        "recall": t05["recall"],
        "f1": t05["f1"],
        "fpr": t05["fpr"],
        "thr_08": t08,
        "best_iteration": best_iteration,
        "latency_single_ms": latency_single,
        "latency_batch1000_ms": latency_batch,
        "train_seconds": train_seconds,
        "top_features": [{"feature": n, "importance": float(v)} for n, v in importances],
        "path": str(model_path),
    }
    save_metrics("xgboost", metrics)
    log_mlflow_run(
        EXPERIMENT_NAME,
        "xgboost",
        params={
            "n_train": len(X_train),
            "n_test": len(X_test),
            "n_features": len(feature_cols),
            "scale_pos_weight": round(scale_pos_weight, 2),
            "best_iteration": best_iteration,
        },
        metrics={
            "auroc": scores["auroc"],
            "pr_auc": scores["pr_auc"],
            "precision": t05["precision"],
            "recall": t05["recall"],
            "f1": t05["f1"],
            "fpr": t05["fpr"],
        },
        model=model,
    )
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Train XGBoost fraud classifier")
    parser.add_argument("--feature-table", type=Path, default=DEFAULT_LABELED_TABLE)
    args = parser.parse_args()
    train_xgboost(args.feature_table)


if __name__ == "__main__":
    main()
