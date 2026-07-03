"""Evaluate the 50/50 XGBoost + LightGBM probability ensemble on the shared test split."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import joblib
import numpy as np
from sklearn.model_selection import train_test_split

from ml.training.common import (
    MODELS_DIR,
    measure_latency_ms,
    print_box,
    save_metrics,
    summarize_scores,
)
from ml.training.prepare_features import DEFAULT_LABELED_TABLE, prepare_features

TEST_SIZE = 0.2
RANDOM_STATE = 42  # identical split to both base trainers

ENSEMBLE_WEIGHTS = {"xgb": 0.5, "lgb": 0.5}


def train_ensemble(feature_table_path: Path | None = None) -> dict:
    started = time.perf_counter()
    xgb_model = joblib.load(MODELS_DIR / "xgboost_model.pkl")
    lgb_model = joblib.load(MODELS_DIR / "lightgbm_model.pkl")

    X, y, _ = prepare_features(feature_table_path, save_columns=False, verbose=False)
    _, X_test, _, y_test = train_test_split(
        X, y, test_size=TEST_SIZE, stratify=y, random_state=RANDOM_STATE
    )

    xgb_proba = xgb_model.predict_proba(X_test)[:, 1]
    lgb_proba = lgb_model.predict_proba(X_test)[:, 1]
    ensemble_proba = ENSEMBLE_WEIGHTS["xgb"] * xgb_proba + ENSEMBLE_WEIGHTS["lgb"] * lgb_proba

    scores = summarize_scores(y_test, ensemble_proba)
    xgb_scores = summarize_scores(y_test, xgb_proba)
    lgb_scores = summarize_scores(y_test, lgb_proba)
    t05 = scores["thr_05"]

    single_row = X_test.iloc[[0]]
    latency_single = measure_latency_ms(
        lambda: ENSEMBLE_WEIGHTS["xgb"] * xgb_model.predict_proba(single_row)[:, 1]
        + ENSEMBLE_WEIGHTS["lgb"] * lgb_model.predict_proba(single_row)[:, 1]
    )

    np.save(MODELS_DIR / "ensemble_test_probas.npy", ensemble_proba)
    config_path = MODELS_DIR / "ensemble_config.json"
    config_path.write_text(
        json.dumps(
            {
                "weights": ENSEMBLE_WEIGHTS,
                "models": ["xgboost_model.pkl", "lightgbm_model.pkl"],
                "test_size": TEST_SIZE,
                "random_state": RANDOM_STATE,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    elapsed = time.perf_counter() - started
    print_box(
        "ENSEMBLE (XGBoost + LightGBM) RESULTS",
        [
            [
                f"AUROC:         {scores['auroc']:.3f}  "
                f"(XGB: {xgb_scores['auroc']:.3f}, LGB: {lgb_scores['auroc']:.3f})",
                f"Precision:     {t05['precision']:.3f}  (threshold=0.5)",
                f"Recall:        {t05['recall']:.3f}  (threshold=0.5)",
                f"F1 Score:      {t05['f1']:.3f}",
                f"FPR:           {t05['fpr']:.3f}",
                f"PR-AUC:        {scores['pr_auc']:.3f}",
            ],
            [
                "vs XGBoost alone:",
                f"AUROC:  {scores['auroc'] - xgb_scores['auroc']:+.3f}   "
                f"Recall: {t05['recall'] - xgb_scores['thr_05']['recall']:+.3f}   "
                f"F1: {t05['f1'] - xgb_scores['thr_05']['f1']:+.3f}",
            ],
            [
                f"Inference latency (single txn):  {latency_single:.0f}ms",
                "Saved: ml/models/ensemble_test_probas.npy",
                "Saved: ml/models/ensemble_config.json",
            ],
        ],
    )

    metrics = {
        "model": "ensemble",
        "auroc": scores["auroc"],
        "pr_auc": scores["pr_auc"],
        "precision": t05["precision"],
        "recall": t05["recall"],
        "f1": t05["f1"],
        "fpr": t05["fpr"],
        "latency_single_ms": latency_single,
        "train_seconds": elapsed,
        "path": str(config_path),
    }
    save_metrics("ensemble", metrics)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate XGB+LGB ensemble")
    parser.add_argument("--feature-table", type=Path, default=DEFAULT_LABELED_TABLE)
    args = parser.parse_args()
    train_ensemble(args.feature_table)


if __name__ == "__main__":
    main()
