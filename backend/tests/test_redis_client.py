"""Tests for VelocityStateStore (live Redis, test-scoped key prefixes) and
the RedisUnavailable fallback path."""

from __future__ import annotations

import copy
import uuid

import pandas as pd
import pytest

from feature_engineering.config import load_config
from feature_engineering.redis_client import RedisUnavailable, VelocityStateStore
from feature_engineering.velocity_features import VelocityFeatureEngineer

BASE_TS_MS = int(pd.Timestamp("2026-03-01 12:00:00").value // 1_000_000)


def _test_cfg() -> dict:
    cfg = copy.deepcopy(load_config())
    scope = f"test:{uuid.uuid4().hex[:8]}:"
    for name in cfg["redis"]["key_prefixes"]:
        cfg["redis"]["key_prefixes"][name] = scope + name + ":"
    return cfg


@pytest.fixture()
def store():
    cfg = _test_cfg()
    s = VelocityStateStore(cfg=cfg)
    if not s.ping():
        pytest.skip("Redis not reachable on the configured host/port")
    yield s
    for prefix in cfg["redis"]["key_prefixes"].values():
        keys = s.client.keys(prefix + "*")
        if keys:
            s.client.delete(*keys)


class TestRecordAndCount:
    def test_first_txn_counts_itself(self, store):
        counts = store.record_and_count("ACC-A", "T1", BASE_TS_MS, 500.0)
        assert counts["txn_count_1m"] == 1
        assert counts["txn_count_7d"] == 1
        assert counts["total_amount_1h_npr"] == pytest.approx(500.0)

    def test_windows_partition_correctly(self, store):
        store.record_and_count("ACC-A", "T1", BASE_TS_MS, 100.0)
        # 30 seconds later: inside 1m
        c = store.record_and_count("ACC-A", "T2", BASE_TS_MS + 30_000, 50.0)
        assert c["txn_count_1m"] == 2
        # 2 hours later: outside 1m/5m/15m/1h, inside 24h
        c = store.record_and_count("ACC-A", "T3", BASE_TS_MS + 2 * 3600_000, 25.0)
        assert c["txn_count_1m"] == 1
        assert c["txn_count_1h"] == 1
        assert c["txn_count_24h"] == 3
        assert c["total_amount_1h_npr"] == pytest.approx(25.0)
        assert c["total_amount_24h_npr"] == pytest.approx(175.0)

    def test_events_older_than_longest_window_are_trimmed(self, store):
        store.record_and_count("ACC-A", "T1", BASE_TS_MS, 100.0)
        eight_days = 8 * 86_400_000
        c = store.record_and_count("ACC-A", "T2", BASE_TS_MS + eight_days, 50.0)
        assert c["txn_count_7d"] == 1
        vel_key = store.prefixes["velocity"] + "ACC-A"
        assert store.client.zcard(vel_key) == 1  # old member physically removed

    def test_keys_carry_ttl(self, store):
        store.record_and_count("ACC-A", "T1", BASE_TS_MS, 100.0)
        for prefix in ("velocity", "velocity_amount"):
            ttl = store.client.ttl(store.prefixes[prefix] + "ACC-A")
            assert 0 < ttl <= store.velocity_ttl_s

    def test_duplicate_txn_id_not_double_counted(self, store):
        store.record_and_count("ACC-A", "T1", BASE_TS_MS, 100.0)
        c = store.record_and_count("ACC-A", "T1", BASE_TS_MS, 100.0)  # retry
        assert c["txn_count_1m"] == 1


class TestBaselineCache:
    def test_roundtrip_and_miss(self, store):
        assert store.get_baseline("ACC-NOPE") is None
        store.set_baseline(
            "ACC-A",
            {"avg_txn_amount_30d_npr": 8000.0, "std_txn_amount_30d_npr": 2000.0,
             "n_txn_30d": 20, "baseline_date": "2026-03-01"},
        )
        got = store.get_baseline("ACC-A")
        assert got["avg_txn_amount_30d_npr"] == pytest.approx(8000.0)
        assert "baseline_date" not in got  # non-numeric field filtered out
        ttl = store.client.ttl(store.prefixes["baseline"] + "ACC-A")
        assert 0 < ttl <= store.baseline_ttl_s


class TestUnavailableFallback:
    @pytest.fixture()
    def dead_store(self):
        cfg = copy.deepcopy(load_config())
        cfg["redis"]["port"] = 6399  # nothing listens here
        cfg["redis"]["socket_timeout_s"] = 0.05
        return VelocityStateStore(cfg=cfg)

    def test_record_raises_redis_unavailable(self, dead_store):
        with pytest.raises(RedisUnavailable):
            dead_store.record_and_count("ACC-A", "T1", BASE_TS_MS, 1.0)

    def test_transform_one_falls_back_to_postgres(self, dead_store):
        """Redis down -> velocity windows come from transactions_raw, txn still scored."""
        import psycopg2

        try:
            conn = psycopg2.connect(load_config()["database"]["dsn"])
        except psycopg2.OperationalError:
            pytest.skip("Postgres not reachable")
        try:
            eng = VelocityFeatureEngineer(state_store=dead_store)
            eng.amount_cap_ = 4_500_000.0
            feats = eng.transform_one(
                {
                    "txn_id": f"TXN-TEST-{uuid.uuid4().hex[:8]}",
                    "account_id": "ACC-TEST-NO-HISTORY",
                    "timestamp": "2026-03-01 12:00:00",
                    "amount_npr": 5000.0,
                    "channel": "MOBILE_APP",
                    "auth_method": "MPIN",
                },
                conn=conn,
                write_to_db=False,
            )
            assert feats["txn_count_1m"] == 1  # only itself
            assert feats["is_cold_start"] == 1
            assert feats["source"].startswith("realtime:pg_fallback")
        finally:
            conn.close()
