"""Train the Synthesis Agent Random Forest meta-learner on 4-agent score tuples.

Behavior scores come from the real trained XGBoost model. Velocity, Geo, and
Graph agent scores are MOCKED from correlated domain features until those
services produce held-out evaluation outputs — replace post-deployment.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

from ml.training.common import (
    MODELS_DIR,
    log_mlflow_run,
    measure_latency_ms,
    print_box,
    save_metrics,
    summarize_scores,
)
from ml.training.prepare_features import (
    DEFAULT_LABELED_TABLE,
    load_labeled_table,
    prepare_features,
)

EXPERIMENT_NAME = "synthesis_meta_learner"
MODEL_FILENAME = "meta_learner_model.pkl"

META_FEATURE_COLS = [
    "r_velocity",
    "c_velocity",
    "r_geo",
    "c_geo",
    "r_behavior",
    "c_behavior",
    "r_graph",
    "c_graph",
    "txn_type_encoded",
]

TEST_SIZE = 0.2
RANDOM_STATE = 42


def _bool_float(series: pd.Series) -> np.ndarray:
    return (
        series.map({True: 1.0, False: 0.0, "True": 1.0, "False": 0.0})
        .fillna(0.0)
        .to_numpy(dtype=float)
    )


def generate_agent_scores(
    df: pd.DataFrame, behavior_proba: np.ndarray, *, random_state: int = RANDOM_STATE
) -> pd.DataFrame:
    """Build (risk, confidence) tuples per agent; behavior is REAL, rest mocked."""
    rng = np.random.default_rng(random_state)
    n = len(df)

    z_score = pd.to_numeric(df["vel_z_score_amount"], errors="coerce").fillna(0)
    count_1m = pd.to_numeric(df["vel_txn_count_1m"], errors="coerce").fillna(0)
    r_velocity = (
        0.4 * (z_score / 10).clip(0, 1)
        + 0.3 * (count_1m / 5).clip(0, 1)
        + 0.2 * _bool_float(df["vel_dormancy_break"])
        + 0.1 * _bool_float(df["vel_new_counterparty_flag"])
        + rng.normal(0, 0.05, n)
    ).clip(0, 1)

    km_home = pd.to_numeric(df["geo_km_from_home_district"], errors="coerce").fillna(0)
    r_geo = (
        0.5 * _bool_float(df["geo_impossible_travel"])
        + 0.2 * _bool_float(df["geo_is_vpn"])
        + 0.2 * (km_home / 1000).clip(0, 1)
        + 0.1 * _bool_float(df["geo_is_tor"])
        + rng.normal(0, 0.05, n)
    ).clip(0, 1)

    r_behavior = np.clip(behavior_proba, 0, 1)

    r_graph = (
        0.5 * _bool_float(df["is_fraud_merchant"])
        + 0.3 * _bool_float(df["is_structuring_amount"])
        + 0.2 * _bool_float(df["dev_is_rooted"])
        + rng.normal(0, 0.05, n)
    ).clip(0, 1)

    monthly_count = pd.to_numeric(df["cust_avg_monthly_txn_count"], errors="coerce")
    c_velocity = np.where(monthly_count.fillna(0) >= 50, 0.95, 0.70)
    c_geo = np.where(df["geo_impossible_travel"].notna(), 0.92, 0.70)
    c_behavior = np.where(monthly_count.fillna(0) >= 50, 0.88, 0.75)
    c_graph = np.full(n, 0.85)

    return pd.DataFrame(
        {
            "r_velocity": r_velocity,
            "c_velocity": c_velocity,
            "r_geo": r_geo,
            "c_geo": c_geo,
            "r_behavior": r_behavior,
            "c_behavior": c_behavior,
            "r_graph": r_graph,
            "c_graph": c_graph,
            "txn_type_encoded": pd.to_numeric(df["type_encoded"], errors="coerce")
            .fillna(0)
            .astype(int),
        }
    )


def train_meta_learner(feature_table_path: Path | None = None) -> dict:
    started = time.perf_counter()

    df = load_labeled_table(feature_table_path)
    X_full, y, _ = prepare_features(df=df, save_columns=False, verbose=False)

    xgb_model = joblib.load(MODELS_DIR / "xgboost_model.pkl")
    behavior_proba = xgb_model.predict_proba(X_full)[:, 1]

    agent_scores = generate_agent_scores(df, behavior_proba)
    X = agent_scores[META_FEATURE_COLS]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=TEST_SIZE, stratify=y, random_state=RANDOM_STATE
    )

    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=8,
        class_weight="balanced",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    proba = model.predict_proba(X_test)[:, 1]
    scores = summarize_scores(y_test, proba)
    t05 = scores["thr_05"]

    single_row = X_test.iloc[[0]]
    latency_single = measure_latency_ms(lambda: model.predict_proba(single_row))

    model_path = MODELS_DIR / MODEL_FILENAME
    joblib.dump({"model": model, "feature_columns": META_FEATURE_COLS}, model_path)

    train_seconds = time.perf_counter() - started
    print_box(
        "META-LEARNER (Random Forest) RESULTS",
        [
            [
                "Input features:   9 (4 risk + 4 confidence + txn_type)",
                f"AUROC:            {scores['auroc']:.3f}",
                f"Precision:        {t05['precision']:.3f}",
                f"Recall:           {t05['recall']:.3f}",
                f"F1:               {t05['f1']:.3f}",
                f"FPR:              {t05['fpr']:.3f}",
                "Note: Uses MOCKED geo/velocity/graph scores",
                "      Replace with real agent scores post-deployment",
                f"Inference latency: {latency_single:.0f}ms",
                f"Model saved: ml/models/{MODEL_FILENAME}",
            ],
        ],
    )

    metrics = {
        "model": "meta_learner",
        "auroc": scores["auroc"],
        "pr_auc": scores["pr_auc"],
        "precision": t05["precision"],
        "recall": t05["recall"],
        "f1": t05["f1"],
        "fpr": t05["fpr"],
        "latency_single_ms": latency_single,
        "train_seconds": train_seconds,
        "mock_agent_scores": True,
        "path": str(model_path),
    }
    save_metrics("meta_learner", metrics)
    log_mlflow_run(
        EXPERIMENT_NAME,
        "meta_learner",
        params={
            "mock_agent_scores": True,
            "n_train": len(X_train),
            "n_test": len(X_test),
            "n_features": len(META_FEATURE_COLS),
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
    parser = argparse.ArgumentParser(description="Train synthesis meta-learner")
    parser.add_argument("--feature-table", type=Path, default=DEFAULT_LABELED_TABLE)
    args = parser.parse_args()
    train_meta_learner(args.feature_table)


if __name__ == "__main__":
    main()
