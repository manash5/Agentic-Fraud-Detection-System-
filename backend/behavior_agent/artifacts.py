"""Load every trained model + preprocessor ONCE (paper: "all models preloaded
in memory" for the 100ms budget). Nothing here touches the database.

Raises :class:`ModelMissingError` naming the exact missing file so the API can
return a distinct 503 for a missing model vs. Postgres being down.

IMPORTANT (macOS OpenMP clash): calling ``xgboost.load_model`` after torch has
been imported segfaults with duplicate OpenMP runtimes. ``load_bundle``
therefore loads the XGBoost model FIRST and imports torch (via lstm_arch)
only afterwards. Never add a top-level ``import torch`` to this package's
import chain ahead of the XGBoost load.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import xgboost as xgb

from behavior_agent.config import load_config, model_path


class ModelMissingError(Exception):
    """A required model/preprocessor artifact is absent on disk."""


@dataclass
class ModelBundle:
    # XGBoost (supervised branch)
    xgb_model: xgb.XGBClassifier
    xgb_features: list[str]
    xgb_threshold: float
    # Isolation Forest (cold-start / complementary branch)
    iso_model: Any
    iso_scaler: Any                 # fitted StandardScaler over iso_scaled_cols
    iso_features: list[str]
    iso_scaled_cols: list[str]
    # LSTM (sequence branch) — a lstm_arch.TwoBranchLSTM in eval mode
    lstm_model: Any
    lstm_manifest: dict[str, Any]
    lstm_seq_len: int
    lstm_preproc: dict[str, Any]    # seq_scaler, static_scaler, ohe, district_freq
    # Percentile calibration grids: name -> sorted np.ndarray of reference scores
    calibration: dict[str, np.ndarray]
    # Pre-built SHAP explainers (TreeExplainer) — created once at load time.
    xgb_explainer: Any = None
    iso_explainer: Any = None


def _require(path: Path, what: str) -> Path:
    if not path.exists():
        raise ModelMissingError(f"{what} not found at {path}")
    return path


def load_bundle(cfg: dict[str, Any] | None = None,
                require_calibration: bool = True) -> ModelBundle:
    """``require_calibration=False`` is only for behavior_agent.build_calibration,
    which needs the models loaded before the calibration file exists."""
    cfg = cfg or load_config()

    # ---- XGBoost ----
    xgb_model = xgb.XGBClassifier()
    xgb_model.load_model(_require(model_path(cfg, "xgboost", "model"), "XGBoost model"))
    with open(_require(model_path(cfg, "xgboost", "manifest"), "XGBoost manifest")) as f:
        xgb_manifest = json.load(f)
    xgb_features = xgb_manifest["feature_columns"]
    xgb_threshold = float(xgb_manifest["recommended_threshold"])

    # ---- Isolation Forest ----
    iso_model = joblib.load(
        _require(model_path(cfg, "isolation_forest", "model"), "Isolation Forest model"))
    iso_scaler = joblib.load(
        _require(model_path(cfg, "isolation_forest", "scaler"), "Isolation Forest scaler"))
    with open(_require(model_path(cfg, "isolation_forest", "manifest"),
                       "Isolation Forest manifest")) as f:
        iso_manifest = json.load(f)
    iso_features = iso_manifest["feature_columns"]
    iso_scaled_cols = iso_manifest["scaled_columns"]
    if list(getattr(iso_scaler, "feature_names_in_", iso_scaled_cols)) != iso_scaled_cols:
        raise ModelMissingError(
            "Isolation Forest scaler was fitted on different columns than the manifest lists")

    # ---- LSTM (torch imported only now — see module docstring) ----
    import torch

    from behavior_agent.lstm_arch import TwoBranchLSTM

    ckpt = torch.load(_require(model_path(cfg, "lstm", "model"), "LSTM checkpoint"),
                      map_location="cpu", weights_only=False)
    arch = ckpt["arch"]
    lstm_model = TwoBranchLSTM(
        arch["n_seq_feat"], arch["n_static_feat"], hidden=arch["hidden"],
        layers=arch["layers"], static_emb=arch["static_emb"],
        fusion_hidden=arch["fusion_hidden"], dropout=arch["dropout"])
    lstm_model.load_state_dict(ckpt["state_dict"])
    lstm_model.eval()
    with open(_require(model_path(cfg, "lstm", "manifest"), "LSTM manifest")) as f:
        lstm_manifest = json.load(f)
    lstm_preproc = joblib.load(
        _require(model_path(cfg, "lstm", "preprocessors"), "LSTM preprocessors"))
    n_seq = len(lstm_manifest["seq_features"])
    if arch["n_seq_feat"] != n_seq:
        raise ModelMissingError(
            f"LSTM checkpoint expects {arch['n_seq_feat']} seq features but the "
            f"manifest lists {n_seq}")

    # ---- Percentile calibration grids (built by behavior_agent.build_calibration) ----
    calibration: dict[str, np.ndarray] = {}
    if require_calibration:
        with open(_require(model_path(cfg, "calibration"), "score calibration file")) as f:
            raw = json.load(f)
        calibration = {name: np.asarray(grid["quantiles"], dtype=np.float64)
                       for name, grid in raw["models"].items()}
        for name in ("xgboost", "isolation_forest", "lstm"):
            if name not in calibration:
                raise ModelMissingError(
                    f"calibration file has no grid for '{name}' — rerun "
                    f"behavior_agent.build_calibration")

    xgb_explainer = iso_explainer = None
    try:
        import shap
        xgb_explainer = shap.TreeExplainer(xgb_model)
        iso_explainer = shap.TreeExplainer(iso_model)
    except Exception as exc:  # noqa: BLE001 — SHAP optional at load; scorers skip if None
        import logging
        logging.getLogger("behavior-agent").warning(
            "SHAP explainers not preloaded: %s", exc)

    return ModelBundle(
        xgb_model=xgb_model, xgb_features=xgb_features, xgb_threshold=xgb_threshold,
        iso_model=iso_model, iso_scaler=iso_scaler, iso_features=iso_features,
        iso_scaled_cols=iso_scaled_cols,
        lstm_model=lstm_model, lstm_manifest=lstm_manifest,
        lstm_seq_len=int(lstm_manifest["seq_len_N"]), lstm_preproc=lstm_preproc,
        calibration=calibration,
        xgb_explainer=xgb_explainer, iso_explainer=iso_explainer,
    )
