"""Resolve backend-relative paths for offline validation."""

from __future__ import annotations

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATASETS_DIR = BACKEND_DIR / "datasets"
PROCESSED_DIR = BACKEND_DIR / "datasets_processed"
MODELS_DIR = BACKEND_DIR / "models"
MLRUNS_DIR = BACKEND_DIR / "mlruns"

XGB_VAL_SCORED = PROCESSED_DIR / "val_scored_xgboost.csv"
ISO_SCORED = PROCESSED_DIR / "transactions_scored_isoforest.csv"
LSTM_METRICS = MODELS_DIR / "lstm" / "metrics.json"
LABELS_TRAIN = DATASETS_DIR / "fraud_labels_train.csv"
LABELS_HIDDEN = DATASETS_DIR / "fraud_labels_eval_HIDDEN.csv"
RULE_BASELINE = DATASETS_DIR / "rule_engine_baseline_predictions.csv"
