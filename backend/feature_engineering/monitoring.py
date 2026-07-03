"""Fit-time / transform-time feature statistics and drift warnings (§8)."""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

from feature_engineering.config import load_config

logger = logging.getLogger(__name__)

StatsDict = dict[str, dict[str, float]]


def compute_feature_stats(df: pd.DataFrame, columns: list[str]) -> StatsDict:
    """Per-feature mean/std/null_rate/min/max for the given numeric columns."""
    stats: StatsDict = {}
    for col in columns:
        s = pd.to_numeric(df[col], errors="coerce")
        stats[col] = {
            "mean": float(s.mean()) if s.notna().any() else math.nan,
            "std": float(s.std()) if s.notna().any() else math.nan,
            "null_rate": float(s.isna().mean()),
            "min": float(s.min()) if s.notna().any() else math.nan,
            "max": float(s.max()) if s.notna().any() else math.nan,
        }
    return stats


def log_stats(stats: StatsDict, label: str) -> None:
    """Emit one log line per feature: mean/std/null-rate/min/max."""
    for col, st in stats.items():
        logger.info(
            "%s stats %s: mean=%.4g std=%.4g null=%.3f min=%.4g max=%.4g",
            label, col, st["mean"], st["std"], st["null_rate"], st["min"], st["max"],
        )


def warn_on_drift(
    fit_stats: StatsDict,
    transform_stats: StatsDict,
    cfg: dict[str, Any] | None = None,
) -> list[str]:
    """Compare transform-time stats to fit-time stats; log + return warnings.

    A feature drifts when its mean moved more than ``drift.mean_shift_stds``
    fit-time standard deviations, or its null rate changed by more than
    ``drift.null_rate_delta``.
    """
    cfg = cfg or load_config()
    k = cfg["drift"]["mean_shift_stds"]
    max_null_delta = cfg["drift"]["null_rate_delta"]
    warnings: list[str] = []
    for col, fit_st in fit_stats.items():
        cur = transform_stats.get(col)
        if cur is None:
            continue
        fit_std = fit_st["std"]
        if fit_std and not math.isnan(fit_std) and fit_std > 0:
            shift = abs(cur["mean"] - fit_st["mean"]) / fit_std
            if shift > k:
                warnings.append(
                    f"{col}: mean shifted {shift:.1f} fit-stds "
                    f"({fit_st['mean']:.4g} -> {cur['mean']:.4g})"
                )
        null_delta = abs(cur["null_rate"] - fit_st["null_rate"])
        if null_delta > max_null_delta:
            warnings.append(
                f"{col}: null rate changed {fit_st['null_rate']:.3f} -> "
                f"{cur['null_rate']:.3f}"
            )
    for w in warnings:
        logger.warning("feature drift: %s", w)
    return warnings
