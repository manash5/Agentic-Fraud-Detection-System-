"""Tests for fit/transform stats and drift warnings."""

from __future__ import annotations

import numpy as np
import pandas as pd

from feature_engineering.monitoring import (
    compute_feature_stats,
    warn_on_drift,
)


def test_compute_feature_stats_handles_nulls():
    df = pd.DataFrame({"a": [1.0, 2.0, np.nan, 3.0], "b": [np.nan] * 4})
    stats = compute_feature_stats(df, ["a", "b"])
    assert stats["a"]["mean"] == 2.0
    assert stats["a"]["null_rate"] == 0.25
    assert stats["b"]["null_rate"] == 1.0


def test_no_drift_for_identical_stats():
    df = pd.DataFrame({"a": np.random.default_rng(0).normal(0, 1, 1000)})
    stats = compute_feature_stats(df, ["a"])
    assert warn_on_drift(stats, stats) == []


def test_mean_shift_triggers_warning():
    rng = np.random.default_rng(0)
    fit_stats = compute_feature_stats(
        pd.DataFrame({"a": rng.normal(0, 1, 1000)}), ["a"]
    )
    cur_stats = compute_feature_stats(
        pd.DataFrame({"a": rng.normal(10, 1, 1000)}), ["a"]
    )
    warnings = warn_on_drift(fit_stats, cur_stats)
    assert len(warnings) == 1
    assert "mean shifted" in warnings[0]


def test_null_rate_change_triggers_warning():
    fit_stats = compute_feature_stats(pd.DataFrame({"a": [1.0] * 100}), ["a"])
    cur_stats = compute_feature_stats(
        pd.DataFrame({"a": [1.0] * 80 + [np.nan] * 20}), ["a"]
    )
    warnings = warn_on_drift(fit_stats, cur_stats)
    assert any("null rate" in w for w in warnings)
