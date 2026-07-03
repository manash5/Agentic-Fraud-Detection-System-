"""Unit tests for VelocityFeatureEngineer: nulls, single-row, boundaries."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from feature_engineering.config import load_config
from feature_engineering.velocity_features import VelocityFeatureEngineer

CFG = load_config()


def make_df(**overrides) -> pd.DataFrame:
    """One well-formed row with raw + history columns; override any field."""
    row = {
        "txn_id": "TXN-TEST-1",
        "account_id": "ACC-TEST",
        "timestamp": pd.Timestamp("2026-03-01 14:00:00"),
        "amount_npr": 10_000.0,
        "channel": "MOBILE_APP",
        "auth_method": "MPIN",
        "txn_count_1m": 1,
        "txn_count_5m": 1,
        "txn_count_15m": 1,
        "txn_count_1h": 2,
        "txn_count_24h": 24,
        "txn_count_7d": 30,
        "total_amount_1h_npr": 12_000.0,
        "total_amount_24h_npr": 40_000.0,
        "avg_txn_amount_30d_npr": 8_000.0,
        "std_txn_amount_30d_npr": 2_000.0,
        "n_txn_30d_prior": 20,
    }
    row.update(overrides)
    return pd.DataFrame([row])


@pytest.fixture()
def eng() -> VelocityFeatureEngineer:
    e = VelocityFeatureEngineer()
    e.amount_cap_ = 4_500_000.0
    return e


class TestDerive:
    def test_single_row_inference(self, eng):
        out = eng.derive(make_df())
        assert len(out) == 1
        # z = (10000 - 8000) / 2000 = 1.0
        assert out["z_score_amount"].iloc[0] == pytest.approx(1.0)
        # acceleration = 2 / max(24/24, eps) = 2.0
        assert out["velocity_acceleration"].iloc[0] == pytest.approx(2.0)
        assert out["amount_deviation_ratio"].iloc[0] == pytest.approx(1.25)
        assert out["is_cold_start"].iloc[0] == 0

    def test_unfitted_raises(self):
        with pytest.raises(RuntimeError, match="not fitted"):
            VelocityFeatureEngineer().derive(make_df())

    def test_missing_columns_raises(self, eng):
        with pytest.raises(ValueError, match="missing columns"):
            eng.derive(make_df().drop(columns=["txn_count_1h"]))

    def test_null_amount_is_safe(self, eng):
        out = eng.derive(make_df(amount_npr=np.nan))
        assert np.isfinite(out["z_score_amount"].iloc[0])
        assert np.isfinite(out["structuring_proximity"].iloc[0])
        assert np.isfinite(out["amount_deviation_ratio"].iloc[0])

    def test_null_history_forces_cold_start(self, eng):
        out = eng.derive(
            make_df(
                avg_txn_amount_30d_npr=np.nan,
                std_txn_amount_30d_npr=np.nan,
                n_txn_30d_prior=0,
            )
        )
        assert out["is_cold_start"].iloc[0] == 1
        assert out["z_score_amount"].iloc[0] == 0.0
        assert out["amount_deviation_ratio"].iloc[0] == 1.0

    def test_zero_std_no_division_blowup(self, eng):
        out = eng.derive(make_df(std_txn_amount_30d_npr=0.0))
        assert out["is_cold_start"].iloc[0] == 1
        assert out["z_score_amount"].iloc[0] == 0.0

    def test_z_score_clipped_at_boundary(self, eng):
        clip = CFG["velocity"]["z_score_clip"]
        # amount far above baseline -> clipped exactly at the boundary
        out = eng.derive(make_df(amount_npr=1_000_000.0))
        assert out["z_score_amount"].iloc[0] == pytest.approx(clip)
        # exactly at the clip: z = (8000 + 10*2000 - 8000)/2000 = 10.0
        exact = 8_000.0 + clip * 2_000.0
        out = eng.derive(make_df(amount_npr=exact))
        assert out["z_score_amount"].iloc[0] == pytest.approx(clip)

    def test_cold_start_boundary_min_txns(self, eng):
        min_n = CFG["velocity"]["cold_start"]["min_prior_txns_30d"]
        assert eng.derive(make_df(n_txn_30d_prior=min_n))["is_cold_start"].iloc[0] == 0
        assert (
            eng.derive(make_df(n_txn_30d_prior=min_n - 1))["is_cold_start"].iloc[0] == 1
        )

    def test_structuring_proximity_exact_threshold(self, eng):
        out = eng.derive(make_df(amount_npr=49_999.0))
        assert out["structuring_proximity"].iloc[0] == 0.0
        out = eng.derive(make_df(amount_npr=50_010.0))
        assert out["structuring_proximity"].iloc[0] == pytest.approx(11.0)
        # far away from every threshold: capped
        out = eng.derive(make_df(amount_npr=4_000_000.0))
        assert (
            out["structuring_proximity"].iloc[0]
            == CFG["velocity"]["structuring_proximity_cap_npr"]
        )

    def test_night_flag_boundaries(self, eng):
        start = CFG["velocity"]["night_hours"]["start"]  # 22
        end = CFG["velocity"]["night_hours"]["end"]  # 6
        at_start = make_df(timestamp=pd.Timestamp(f"2026-03-01 {start}:00:00"))
        assert eng.derive(at_start)["night_flag"].iloc[0] == 1
        before_start = make_df(timestamp=pd.Timestamp(f"2026-03-01 {start - 1}:59:00"))
        assert eng.derive(before_start)["night_flag"].iloc[0] == 0
        at_end = make_df(timestamp=pd.Timestamp(f"2026-03-01 0{end}:00:00"))
        assert eng.derive(at_end)["night_flag"].iloc[0] == 0
        just_before_end = make_df(timestamp=pd.Timestamp(f"2026-03-01 0{end - 1}:59:59"))
        assert eng.derive(just_before_end)["night_flag"].iloc[0] == 1

    def test_night_burst_is_multiplicative(self, eng):
        out = eng.derive(
            make_df(timestamp=pd.Timestamp("2026-03-01 23:00:00"), txn_count_1m=7)
        )
        assert out["night_burst_interaction"].iloc[0] == 7
        out = eng.derive(
            make_df(timestamp=pd.Timestamp("2026-03-01 12:00:00"), txn_count_1m=7)
        )
        assert out["night_burst_interaction"].iloc[0] == 0

    def test_acceleration_zero_24h_uses_epsilon(self, eng):
        out = eng.derive(make_df(txn_count_1h=3, txn_count_24h=0))
        expected = 3 / CFG["velocity"]["epsilon"]
        assert out["velocity_acceleration"].iloc[0] == pytest.approx(expected)

    def test_amount_deviation_clipped(self, eng):
        out = eng.derive(make_df(amount_npr=4_000_000.0, avg_txn_amount_30d_npr=100.0))
        assert (
            out["amount_deviation_ratio"].iloc[0]
            == CFG["velocity"]["amount_deviation_clip"]
        )

    def test_winsorization_caps_amount_in_ratio(self, eng):
        # amount above the fitted cap behaves like the cap for z/ratio purposes
        big = eng.derive(make_df(amount_npr=10_000_000.0))
        at_cap = eng.derive(make_df(amount_npr=eng.amount_cap_))
        assert big["z_score_amount"].iloc[0] == at_cap["z_score_amount"].iloc[0]
        # but structuring proximity uses the RAW amount
        assert big["structuring_proximity"].iloc[0] == at_cap["structuring_proximity"].iloc[0]


class TestFit:
    def test_fit_learns_cap(self):
        df = pd.concat(
            [make_df(txn_id=f"T{i}", amount_npr=100.0 * (i + 1)) for i in range(200)],
            ignore_index=True,
        )
        eng = VelocityFeatureEngineer().fit(df, save=False)
        assert eng.amount_cap_ == pytest.approx(
            df["amount_npr"].quantile(CFG["outliers"]["amount_winsor_pct"])
        )
        assert eng.fit_stats_ is not None
        assert "z_score_amount" in eng.fit_stats_

    def test_fit_empty_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            VelocityFeatureEngineer().fit(pd.DataFrame(), save=False)

    def test_fit_missing_history_raises(self):
        raw_only = make_df()[
            ["txn_id", "account_id", "timestamp", "amount_npr", "channel", "auth_method"]
        ]
        with pytest.raises(ValueError, match="attach_history_batch"):
            VelocityFeatureEngineer().fit(raw_only, save=False)

    def test_artifacts_roundtrip(self, tmp_path):
        df = make_df()
        eng = VelocityFeatureEngineer().fit(df, save=False)
        path = eng.save_artifacts(tmp_path / "vel.json")
        loaded = VelocityFeatureEngineer.load_artifacts(path)
        assert loaded.amount_cap_ == eng.amount_cap_
        pd.testing.assert_frame_equal(loaded.derive(df), eng.derive(df))
