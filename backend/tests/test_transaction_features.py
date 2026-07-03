"""Unit tests for TransactionFeatureEngineer: nulls, single-row, boundaries."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from feature_engineering.config import load_config
from feature_engineering.transaction_features import TransactionFeatureEngineer

CFG = load_config()


def make_df(**overrides) -> pd.DataFrame:
    """One well-formed transaction row; override any field."""
    row = {
        "txn_id": "TXN-TEST-1",
        "account_id": "ACC-TEST",
        "timestamp": pd.Timestamp("2026-03-04 14:00:00"),  # Wednesday, daytime
        "amount_npr": 10_000.0,
        "currency": "NPR",
        "channel": "MOBILE_APP",
        "auth_method": "MPIN",
        "response_code": "00",
        "is_international": False,
        "notes": None,
    }
    row.update(overrides)
    return pd.DataFrame([row])


@pytest.fixture()
def eng() -> TransactionFeatureEngineer:
    e = TransactionFeatureEngineer()
    e.categories_ = {
        "channel": ["ATM", "BRANCH", "MOBILE_APP", "WEB"],
        "auth_method": ["BIOMETRIC", "CARD_PIN", "MPIN", "OTP_EMAIL", "OTP_SMS"],
    }
    return e


class TestDerive:
    def test_single_row_inference(self, eng):
        out = eng.derive(make_df())
        assert len(out) == 1
        assert out["txn_hour"].iloc[0] == 14
        assert out["txn_day_of_week"].iloc[0] == 2  # Wednesday
        assert out["txn_is_weekend"].iloc[0] == 0
        assert out["amount_log"].iloc[0] == pytest.approx(np.log1p(10_000.0))
        assert out["response_code_is_success"].iloc[0] == 1
        assert out["has_notes"].iloc[0] == 0
        assert out["is_international"].iloc[0] == 0
        assert out["currency_is_foreign"].iloc[0] == 0
        assert out["channel_MOBILE_APP"].iloc[0] == 1
        assert out["auth_method_MPIN"].iloc[0] == 1

    def test_unfitted_raises(self):
        with pytest.raises(RuntimeError, match="not fitted"):
            TransactionFeatureEngineer().derive(make_df())

    def test_missing_columns_raises(self, eng):
        with pytest.raises(ValueError, match="missing columns"):
            eng.derive(make_df().drop(columns=["currency"]))

    def test_null_amount_is_safe(self, eng):
        out = eng.derive(make_df(amount_npr=np.nan))
        # log1p(0) == 0
        assert out["amount_log"].iloc[0] == pytest.approx(0.0)
        assert np.isfinite(out["amount_log"].iloc[0])

    def test_has_notes_flag(self, eng):
        assert eng.derive(make_df(notes=None))["has_notes"].iloc[0] == 0
        assert eng.derive(make_df(notes="chargeback dispute"))["has_notes"].iloc[0] == 1

    def test_response_success_only_for_success_codes(self, eng):
        assert eng.derive(make_df(response_code="00"))["response_code_is_success"].iloc[0] == 1
        for code in ("05", "51", "57"):
            assert eng.derive(make_df(response_code=code))["response_code_is_success"].iloc[0] == 0

    def test_currency_foreign_and_international(self, eng):
        out = eng.derive(make_df(currency="USD", is_international=True))
        assert out["currency_is_foreign"].iloc[0] == 1
        assert out["is_international"].iloc[0] == 1
        out = eng.derive(make_df(currency="NPR", is_international=False))
        assert out["currency_is_foreign"].iloc[0] == 0

    def test_weekend_boundary(self, eng):
        # 2026-03-07 is a Saturday, 2026-03-08 Sunday, 2026-03-06 Friday
        assert eng.derive(make_df(timestamp=pd.Timestamp("2026-03-07 10:00")))["txn_is_weekend"].iloc[0] == 1
        assert eng.derive(make_df(timestamp=pd.Timestamp("2026-03-08 10:00")))["txn_is_weekend"].iloc[0] == 1
        assert eng.derive(make_df(timestamp=pd.Timestamp("2026-03-06 10:00")))["txn_is_weekend"].iloc[0] == 0

    def test_hour_boundary(self, eng):
        assert eng.derive(make_df(timestamp=pd.Timestamp("2026-03-04 00:00")))["txn_hour"].iloc[0] == 0
        assert eng.derive(make_df(timestamp=pd.Timestamp("2026-03-04 23:59")))["txn_hour"].iloc[0] == 23

    def test_one_hot_known_and_unseen_category(self, eng):
        out = eng.derive(make_df(channel="MOBILE_APP"))
        assert out["channel_MOBILE_APP"].iloc[0] == 1
        assert out["channel_ATM"].iloc[0] == 0
        out = eng.derive(make_df(channel="TELEPATHY"))  # unseen -> all zeros
        assert sum(out[f"channel_{c}"].iloc[0] for c in eng.categories_["channel"]) == 0


class TestCrosscheck:
    def test_agreeing_flags_no_warning(self, eng):
        # Wednesday 14:00 -> weekend_flag False, night_flag False
        df = make_df()
        df["weekend_flag"] = False
        df["night_flag"] = False
        assert eng.crosscheck_flags(df) == []

    def test_disagreeing_flags_warn(self, eng):
        # night_flag stored True but 14:00 is not night -> 100% disagreement
        df = pd.concat([make_df(txn_id=f"T{i}") for i in range(50)], ignore_index=True)
        df["weekend_flag"] = False
        df["night_flag"] = True
        warnings = eng.crosscheck_flags(df)
        assert any("night_flag" in w for w in warnings)

    def test_missing_flags_skipped(self, eng):
        assert eng.crosscheck_flags(make_df()) == []


class TestFit:
    def test_fit_learns_categories(self):
        df = pd.concat(
            [make_df(txn_id=f"T{i}", channel="WEB" if i % 2 else "ATM") for i in range(50)],
            ignore_index=True,
        )
        eng = TransactionFeatureEngineer().fit(df, save=False)
        assert eng.categories_["channel"] == ["ATM", "WEB"]
        assert eng.fit_stats_ is not None
        assert "amount_log" in eng.fit_stats_

    def test_fit_empty_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            TransactionFeatureEngineer().fit(pd.DataFrame(), save=False)

    def test_artifacts_roundtrip(self, tmp_path):
        df = make_df()
        eng = TransactionFeatureEngineer().fit(df, save=False)
        path = eng.save_artifacts(tmp_path / "txn.json")
        loaded = TransactionFeatureEngineer.load_artifacts(path)
        assert loaded.categories_ == eng.categories_
        pd.testing.assert_frame_equal(loaded.derive(df), eng.derive(df))
