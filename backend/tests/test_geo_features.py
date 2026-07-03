"""Unit tests for GeoFeatureEngineer: nulls, single-row, boundary values."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from feature_engineering.config import load_config
from feature_engineering.geo_features import GeoFeatureEngineer, haversine_km

CFG = load_config()


def make_df(**overrides) -> pd.DataFrame:
    """One well-formed geo row with history columns; override any field."""
    row = {
        "txn_id": "TXN-TEST-1",
        "account_id": "ACC-TEST",
        "timestamp": pd.Timestamp("2026-03-01 14:00:00"),
        "latitude": 27.7172,  # Kathmandu
        "longitude": 85.3240,
        "ip_country": "Nepal",
        "ip_isp": "WorldLink",
        "is_vpn": False,
        "is_tor": False,
        "is_datacenter": False,
        "km_from_home_district": 10.0,
        "impossible_travel_reported": False,
        "prev_latitude": 27.7172,
        "prev_longitude": 85.3240,
        "prev_timestamp": pd.Timestamp("2026-03-01 12:00:00"),
        "km_home_prior_avg": 12.0,
        "km_home_prior_std": 5.0,
        "n_geo_prior": 30,
    }
    row.update(overrides)
    return pd.DataFrame([row])


@pytest.fixture()
def eng() -> GeoFeatureEngineer:
    e = GeoFeatureEngineer()
    e.isp_freq_ = {"WorldLink": 0.5, "Ncell": 0.3, "NTC": 0.2}
    e.global_km_home_mean_ = 40.0
    e.global_km_home_std_ = 30.0
    e.distance_cap_ = 150.0
    return e


class TestHaversine:
    def test_zero_distance(self):
        assert haversine_km(
            np.array([27.7]), np.array([85.3]), np.array([27.7]), np.array([85.3])
        )[0] == pytest.approx(0.0)

    def test_one_degree_latitude(self):
        # 1 degree of latitude ~= 111.19 km everywhere
        d = haversine_km(np.array([27.0]), np.array([85.0]), np.array([28.0]), np.array([85.0]))
        assert d[0] == pytest.approx(111.19, abs=0.5)


class TestDerive:
    def test_single_row_inference(self, eng):
        out = eng.derive(make_df())
        assert len(out) == 1
        assert out["prev_txn_km_recomputed"].iloc[0] == pytest.approx(0.0)
        assert out["implied_travel_speed_kmh"].iloc[0] == pytest.approx(0.0)
        assert out["impossible_travel_recomputed"].iloc[0] == 0
        # z = (10 - 12) / 5 = -0.4 against the account's own history
        assert out["distance_z_score"].iloc[0] == pytest.approx(-0.4)
        assert out["isp_risk_encoding"].iloc[0] == pytest.approx(0.5)
        assert out["is_foreign_ip"].iloc[0] == 0

    def test_unfitted_raises(self):
        with pytest.raises(RuntimeError, match="not fitted"):
            GeoFeatureEngineer().derive(make_df())

    def test_first_event_no_prev(self, eng):
        out = eng.derive(
            make_df(prev_latitude=None, prev_longitude=None, prev_timestamp=None)
        )
        assert out["prev_txn_km_recomputed"].iloc[0] == 0.0
        assert out["prev_txn_time_delta_min_recomputed"].iloc[0] == 0.0
        assert out["implied_travel_speed_kmh"].iloc[0] == 0.0
        assert out["impossible_travel_recomputed"].iloc[0] == 0

    def test_null_coordinates_are_safe(self, eng):
        out = eng.derive(make_df(latitude=np.nan, longitude=np.nan))
        assert np.isfinite(out["implied_travel_speed_kmh"].iloc[0])
        assert out["impossible_travel_recomputed"].iloc[0] == 0

    def test_impossible_travel_boundary(self, eng):
        speed_kmh = CFG["geo"]["impossible_speed_kmh"]  # 900
        min_km = CFG["geo"]["impossible_min_km"]  # 50
        # Kathmandu -> ~555 km hop (5 degrees longitude) in 30 minutes = ~1100 km/h
        fast = make_df(
            prev_latitude=27.7172,
            prev_longitude=80.3240,
            prev_timestamp=pd.Timestamp("2026-03-01 13:30:00"),
        )
        out = eng.derive(fast)
        assert out["prev_txn_km_recomputed"].iloc[0] > min_km
        assert out["implied_travel_speed_kmh"].iloc[0] > speed_kmh
        assert out["impossible_travel_recomputed"].iloc[0] == 1
        assert out["travel_flag_mismatch"].iloc[0] == 1  # reported flag said False
        # same speed but a tiny hop (jitter): below the km floor -> not flagged
        jitter = make_df(
            prev_latitude=27.7172,
            prev_longitude=85.3340,  # ~1 km
            prev_timestamp=pd.Timestamp("2026-03-01 13:59:59"),
        )
        assert eng.derive(jitter)["impossible_travel_recomputed"].iloc[0] == 0

    def test_speed_clip_but_flag_from_raw_speed(self, eng):
        clip = CFG["geo"]["implied_speed_clip_kmh"]
        out = eng.derive(
            make_df(
                prev_latitude=27.7172,
                prev_longitude=75.3240,  # ~1000 km
                prev_timestamp=pd.Timestamp("2026-03-01 13:59:00"),  # 1 minute
            )
        )
        assert out["implied_travel_speed_kmh"].iloc[0] == clip
        assert out["impossible_travel_recomputed"].iloc[0] == 1

    def test_tiny_time_delta_no_speed(self, eng):
        out = eng.derive(
            make_df(prev_timestamp=pd.Timestamp("2026-03-01 13:59:59.9"))
        )
        assert out["implied_travel_speed_kmh"].iloc[0] == 0.0

    def test_risk_composite_weights(self, eng):
        w = CFG["geo"]["risk_weights"]
        out = eng.derive(make_df(is_tor=True, is_vpn=True))
        assert out["geo_risk_composite"].iloc[0] == pytest.approx(
            w["is_tor"] + w["is_vpn"]
        )
        all_on = make_df(
            is_tor=True,
            is_vpn=True,
            is_datacenter=True,
            prev_latitude=27.7172,
            prev_longitude=80.3240,
            prev_timestamp=pd.Timestamp("2026-03-01 13:30:00"),
        )
        assert eng.derive(all_on)["geo_risk_composite"].iloc[0] == pytest.approx(1.0)

    def test_distance_z_thin_history_uses_global(self, eng):
        out = eng.derive(make_df(n_geo_prior=2, km_from_home_district=100.0))
        # (100 - 40) / 30 = 2.0 from the fitted GLOBAL distribution
        assert out["distance_z_score"].iloc[0] == pytest.approx(2.0)

    def test_distance_z_clipped(self, eng):
        out = eng.derive(make_df(km_from_home_district=150.0, km_home_prior_std=0.001))
        assert out["distance_z_score"].iloc[0] == CFG["geo"]["distance_z_clip"]

    def test_unseen_isp_and_foreign_ip(self, eng):
        out = eng.derive(make_df(ip_isp="EvilISP", ip_country="Russia"))
        assert out["isp_risk_encoding"].iloc[0] == CFG["geo"]["isp_encoding"]["unseen_value"]
        assert out["is_foreign_ip"].iloc[0] == 1


class TestFit:
    def test_fit_learns_freq_and_global_stats(self):
        rows = [
            make_df(txn_id=f"T{i}", ip_isp="WorldLink" if i % 2 else "Ncell",
                    km_from_home_district=float(i))
            for i in range(100)
        ]
        df = pd.concat(rows, ignore_index=True)
        eng = GeoFeatureEngineer().fit(df, save=False)
        assert eng.isp_freq_["WorldLink"] == pytest.approx(0.5)
        assert eng.isp_freq_["Ncell"] == pytest.approx(0.5)
        assert eng.global_km_home_std_ > 0
        assert eng.fit_stats_ is not None

    def test_fit_empty_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            GeoFeatureEngineer().fit(pd.DataFrame(), save=False)

    def test_artifacts_roundtrip(self, tmp_path):
        df = make_df()
        eng = GeoFeatureEngineer().fit(df, save=False)
        path = eng.save_artifacts(tmp_path / "geo.json")
        loaded = GeoFeatureEngineer.load_artifacts(path)
        pd.testing.assert_frame_equal(loaded.derive(df), eng.derive(df))
