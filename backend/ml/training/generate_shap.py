"""Compute per-transaction SHAP explanations for all 400k labeled rows.

Uses XGBoost's native ``pred_contribs`` (TreeSHAP) which is orders of magnitude
faster than a generic explainer. Writes the top-5 features per transaction to
``shap_values_train.csv`` for the hackathon explainability bonus.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb

from ml.training.common import DEFAULT_LABELED_TABLE, MODELS_DIR
from ml.training.prepare_features import prepare_features

OUTPUT_FILENAME = "shap_values_train.csv"
TOP_K = 5


def generate_shap(feature_table_path: Path | None = None) -> Path:
    model = joblib.load(MODELS_DIR / "xgboost_model.pkl")
    X, _, feature_cols = prepare_features(feature_table_path, save_columns=False, verbose=False)

    table_path = Path(feature_table_path) if feature_table_path else DEFAULT_LABELED_TABLE
    txn_ids = pd.read_csv(table_path, usecols=["txn_id"])["txn_id"].to_numpy()

    booster = model.get_booster()
    dmatrix = xgb.DMatrix(X, feature_names=feature_cols)
    contribs = booster.predict(dmatrix, pred_contribs=True).astype(np.float32)
    shap_matrix = contribs[:, :-1]  # last column is the bias term

    abs_shap = np.abs(shap_matrix)
    top_idx = np.argsort(-abs_shap, axis=1)[:, :TOP_K]
    rows = np.arange(len(shap_matrix))[:, None]
    top_values = shap_matrix[rows, top_idx]
    feature_names = np.asarray(feature_cols)

    data: dict[str, np.ndarray] = {"txn_id": txn_ids}
    for k in range(TOP_K):
        data[f"feature_{k + 1}"] = feature_names[top_idx[:, k]]
        data[f"shap_{k + 1}"] = np.round(top_values[:, k], 6)

    output_path = MODELS_DIR / OUTPUT_FILENAME
    pd.DataFrame(data).to_csv(output_path, index=False)

    mean_abs = abs_shap.mean(axis=0)
    global_top = sorted(zip(feature_cols, mean_abs), key=lambda kv: kv[1], reverse=True)[:TOP_K]

    print(f"✓ SHAP values computed for {len(shap_matrix):,} transactions")
    print("✓ Top global features:")
    for i, (name, value) in enumerate(global_top, start=1):
        print(f"{i}. {name:<28} (mean |SHAP| = {value:.3f})")
    print(f"✓ Saved: ml/models/{OUTPUT_FILENAME} (for hackathon bonus submission)")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate SHAP values for all labeled rows")
    parser.add_argument("--feature-table", type=Path, default=DEFAULT_LABELED_TABLE)
    args = parser.parse_args()
    generate_shap(args.feature_table)


if __name__ == "__main__":
    main()
