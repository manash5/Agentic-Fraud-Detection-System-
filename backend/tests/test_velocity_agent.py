"""Tests for the paper §IV-C-1 Velocity Agent (agents.velocity_agent).

Signal unit tests run against a minimal in-memory fake Redis; the
Redis-unavailable path uses both a mock that raises and a real client on a
dead port; the p99 latency load test needs a live Redis (skipped otherwise,
test-scoped key prefixes, cleaned up after).
"""

from __future__ import annotations

import copy
import math
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import redis

from agents.velocity_agent import (
    RedisUnavailableError,
    VelocityAgent,
    aggregate_risk,
    amount_spike_signal,
    amount_vs_baseline_signal,
    balance_integrity_signal,
    confidence_score,
    get_baseline,
    record_transaction,
    txn_count_signal,
    txn_type_mismatch_signal,
    write_baseline,
    write_type_dist,
)
from feature_engineering.config import load_config
from shared.schemas.transaction import TransactionEvent

CFG = load_config()
ACFG = CFG["velocity_agent"]

BASE_TS = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
BASE_TS_MS = int(BASE_TS.timestamp() * 1000)

WARM_BASELINE = {
    "hist_txn_count_2min_mean": 0.1,
    "hist_txn_count_1hr_mean": 2.0,
    "hist_amount_avg": 1000.0,
    "hist_amount_std": 250.0,
    "observation_count": 200,
}

TYPE_DIST = {"p2p": 0.6, "qr": 0.3, "pos": 0.1}


# -- minimal in-memory Redis fake --------------------------------------------


def _bound(value, default):
    if value is None:
        return default, False
    s = str(value)
    exclusive = s.startswith("(")
    if exclusive:
        s = s[1:]
    return float(s), exclusive


class FakePipeline:
    def __init__(self, fake):
        self.fake = fake
        self.ops = []

    def __getattr__(self, name):
        def queue(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self

        return queue

    def execute(self):
        return [getattr(self.fake, name)(*args, **kwargs) for name, args, kwargs in self.ops]


class FakeRedis:
    """Just the commands the agent uses: zadd/zremrangebyscore/zcard,
    hset/hgetall/delete, expire, pipeline."""

    def __init__(self):
        self.zsets: dict[str, dict[str, float]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.ttls: dict[str, int] = {}

    def pipeline(self, transaction=True):
        return FakePipeline(self)

    def zadd(self, key, mapping):
        z = self.zsets.setdefault(key, {})
        added = sum(1 for m in mapping if m not in z)
        z.update({str(m): float(s) for m, s in mapping.items()})
        return added

    def zremrangebyscore(self, key, min_score, max_score):
        z = self.zsets.get(key, {})
        lo, lo_ex = _bound(min_score, -math.inf)
        hi, hi_ex = _bound(max_score, math.inf)
        doomed = [
            m
            for m, s in z.items()
            if (s > lo or (not lo_ex and s == lo)) and (s < hi or (not hi_ex and s == hi))
        ]
        for m in doomed:
            del z[m]
        return len(doomed)

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def hset(self, key, mapping=None):
        h = self.hashes.setdefault(key, {})
        new = sum(1 for k in mapping if k not in h)
        h.update({str(k): str(v) for k, v in mapping.items()})
        return new

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def delete(self, *keys):
        removed = 0
        for key in keys:
            removed += int(self.zsets.pop(key, None) is not None)
            removed += int(self.hashes.pop(key, None) is not None)
        return removed

    def expire(self, key, ttl):
        self.ttls[key] = int(ttl)
        return True


class DeadRedis:
    """Every command fails the way a downed Redis does."""

    def __getattr__(self, name):
        def boom(*args, **kwargs):
            raise redis.ConnectionError("connection refused")

        return boom


def warm_fake() -> FakeRedis:
    fake = FakeRedis()
    write_baseline("ACC-A", WARM_BASELINE, fake, cfg=CFG)
    write_type_dist("ACC-A", TYPE_DIST, fake, cfg=CFG)
    return fake


def make_event(txn_id="T1", account="ACC-A", amount=1000.0, ts=BASE_TS, txn_type="p2p"):
    return TransactionEvent(
        transaction_id=txn_id,
        user_id=account,
        amount=amount,
        timestamp=ts,
        txn_type=txn_type,
    )


# -- signal 1: transaction counts vs baseline ---------------------------------


class TestTxnCountSignal:
    def test_normal_rate_scores_low(self):
        fake = warm_fake()
        record_transaction("ACC-A", "T1", BASE_TS_MS, fake, cfg=CFG)
        score = txn_count_signal("ACC-A", fake, cfg=CFG, baseline=WARM_BASELINE)
        saturation = ACFG["txn_count"]["saturation_ratio"]
        expected = max(
            ((1 - 0.1) / 1.1) / saturation,  # 2min window
            0.0,  # 1hr window: 1 txn is below the mean of 2
        )
        assert score == pytest.approx(expected)
        assert score < 0.15

    def test_burst_saturates_to_one(self):
        fake = warm_fake()
        for i in range(15):
            record_transaction("ACC-A", f"T{i}", BASE_TS_MS + i * 1000, fake, cfg=CFG)
        assert txn_count_signal("ACC-A", fake, cfg=CFG, baseline=WARM_BASELINE) == 1.0

    def test_missing_baseline_falls_back_to_zero_mean(self):
        fake = FakeRedis()
        record_transaction("ACC-NEW", "T1", BASE_TS_MS, fake, cfg=CFG)
        score = txn_count_signal("ACC-NEW", fake, cfg=CFG, baseline={})
        assert score == pytest.approx(1.0 / ACFG["txn_count"]["saturation_ratio"])

    def test_fetches_baseline_from_redis_when_not_passed(self):
        fake = warm_fake()
        record_transaction("ACC-A", "T1", BASE_TS_MS, fake, cfg=CFG)
        fetched = txn_count_signal("ACC-A", fake, cfg=CFG)
        passed = txn_count_signal("ACC-A", fake, cfg=CFG, baseline=WARM_BASELINE)
        assert fetched == pytest.approx(passed)

    def test_redis_down_raises(self):
        with pytest.raises(RedisUnavailableError):
            txn_count_signal("ACC-A", DeadRedis(), cfg=CFG)


# -- signal 2: amount vs baseline average -------------------------------------


class TestAmountVsBaselineSignal:
    def test_at_baseline_average_scores_zero(self):
        assert amount_vs_baseline_signal(1000.0, "ACC-A", None, cfg=CFG, baseline=WARM_BASELINE) == 0.0

    def test_below_average_clips_to_zero(self):
        assert amount_vs_baseline_signal(100.0, "ACC-A", None, cfg=CFG, baseline=WARM_BASELINE) == 0.0

    def test_midpoint_is_monotonic(self):
        saturation = ACFG["amount"]["ratio_saturation"]
        mid = amount_vs_baseline_signal(5500.0, "ACC-A", None, cfg=CFG, baseline=WARM_BASELINE)
        assert mid == pytest.approx((5.5 - 1) / (saturation - 1))
        higher = amount_vs_baseline_signal(8000.0, "ACC-A", None, cfg=CFG, baseline=WARM_BASELINE)
        assert 0.0 < mid < higher < 1.0

    def test_saturation_reaches_one(self):
        saturation = ACFG["amount"]["ratio_saturation"]
        score = amount_vs_baseline_signal(
            1000.0 * saturation, "ACC-A", None, cfg=CFG, baseline=WARM_BASELINE
        )
        assert score == 1.0

    def test_cold_start_returns_neutral_not_crash(self):
        neutral = ACFG["amount"]["cold_start_score"]
        assert amount_vs_baseline_signal(5000.0, "ACC-NEW", None, cfg=CFG, baseline={}) == neutral
        near_zero = {"hist_amount_avg": 0.0}
        assert amount_vs_baseline_signal(5000.0, "ACC-NEW", None, cfg=CFG, baseline=near_zero) == neutral

    def test_cached_nan_reads_as_cold_start(self):
        # An account with geo events but no txns in the window used to cache
        # "nan"; get_baseline must drop it so the signal sees a cold start.
        fake = FakeRedis()
        write_baseline("ACC-GEO-ONLY", {"hist_amount_avg": float("nan")}, fake, cfg=CFG)
        assert get_baseline("ACC-GEO-ONLY", fake, cfg=CFG) == {}
        score = amount_vs_baseline_signal(5000.0, "ACC-GEO-ONLY", fake, cfg=CFG)
        assert score == ACFG["amount"]["cold_start_score"]


# -- signal 3: >=5x spike boundary --------------------------------------------


class TestAmountSpikeSignal:
    def _score(self, amount):
        return amount_spike_signal(amount, "ACC-A", None, cfg=CFG, baseline=WARM_BASELINE)

    def test_just_below_multiplier_is_zero(self):
        assert self._score(4990.0) == 0.0  # 4.99x

    def test_exactly_at_multiplier_jumps_to_base(self):
        assert self._score(5000.0) == pytest.approx(ACFG["spike"]["base_score"])  # 5.00x

    def test_just_above_multiplier_exceeds_base(self):
        base = ACFG["spike"]["base_score"]
        score = self._score(5010.0)  # 5.01x
        assert base < score < base + 0.01

    def test_full_multiplier_saturates(self):
        full = ACFG["spike"]["full_score_multiplier"]
        assert self._score(1000.0 * full) == 1.0
        assert self._score(1000.0 * full * 2) == 1.0

    def test_cold_start_returns_zero(self):
        assert amount_spike_signal(1_000_000.0, "ACC-NEW", None, cfg=CFG, baseline={}) == 0.0


# -- signal 4: balance integrity (stubbed) ------------------------------------


class TestBalanceIntegritySignal:
    def test_missing_balances_return_none(self):
        assert balance_integrity_signal(None, None, 6000.0, cfg=CFG) is None
        assert balance_integrity_signal(10_000.0, None, 6000.0, cfg=CFG) is None

    def test_consistent_balances_score_zero_when_wired(self):
        assert balance_integrity_signal(10_000.0, 4000.0, 6000.0, cfg=CFG) == 0.0

    def test_mismatch_saturates_when_wired(self):
        # delta 7000 vs declared 6000: 16.7% relative mismatch >= 10% cap
        assert balance_integrity_signal(10_000.0, 3000.0, 6000.0, cfg=CFG) == 1.0

    def test_small_mismatch_scales_when_wired(self):
        # delta 6300 vs declared 6000: 5% relative mismatch, half the cap
        score = balance_integrity_signal(10_000.0, 3700.0, 6000.0, cfg=CFG)
        assert score == pytest.approx(0.05 / ACFG["balance_integrity"]["mismatch_full_score_ratio"])


# -- signal 5: txn_type rarity -------------------------------------------------


class TestTxnTypeMismatchSignal:
    def test_common_type_scores_zero(self):
        assert txn_type_mismatch_signal("p2p", "ACC-A", None, cfg=CFG, type_dist=TYPE_DIST) == 0.0

    def test_rare_type_scores_high(self):
        common = ACFG["type_mismatch"]["common_share"]
        score = txn_type_mismatch_signal("pos", "ACC-A", None, cfg=CFG, type_dist=TYPE_DIST)
        assert score == pytest.approx(1.0 - 0.1 / common)

    def test_unseen_type_scores_one(self):
        assert txn_type_mismatch_signal("wallet", "ACC-A", None, cfg=CFG, type_dist=TYPE_DIST) == 1.0

    def test_cold_start_is_neutral_mid_range(self):
        neutral = ACFG["type_mismatch"]["cold_start_score"]
        assert txn_type_mismatch_signal("p2p", "ACC-NEW", None, cfg=CFG, type_dist={}) == neutral
        assert txn_type_mismatch_signal(None, "ACC-A", None, cfg=CFG, type_dist=TYPE_DIST) == neutral
        assert 0.0 < neutral < 1.0

    def test_fetches_dist_from_redis_when_not_passed(self):
        fake = warm_fake()
        assert txn_type_mismatch_signal("wallet", "ACC-A", fake, cfg=CFG) == 1.0

    def test_redis_down_raises(self):
        with pytest.raises(RedisUnavailableError):
            txn_type_mismatch_signal("p2p", "ACC-A", DeadRedis(), cfg=CFG)


# -- confidence and aggregation ------------------------------------------------


class TestConfidenceScore:
    def test_smooth_ramp(self):
        threshold = ACFG["confidence"]["observation_threshold"]
        assert confidence_score(0, threshold) == 0.0
        assert confidence_score(threshold // 2, threshold) == pytest.approx(0.5)
        assert confidence_score(threshold, threshold) == 1.0
        assert confidence_score(threshold * 4, threshold) == 1.0

    def test_gradient_not_step(self):
        threshold = ACFG["confidence"]["observation_threshold"]
        values = [confidence_score(n, threshold) for n in range(threshold + 1)]
        assert values == sorted(values)
        assert len(set(values)) > 2  # a ramp, not a cutoff


class TestAggregateRisk:
    def test_none_signal_excluded_and_weights_renormalized(self):
        signals = {
            "amount_spike": 1.0,
            "txn_count": 0.0,
            "amount_vs_baseline": 0.5,
            "txn_type_mismatch": 0.0,
            "balance_integrity": None,
        }
        w = ACFG["weights"]
        active = w["amount_spike"] + w["txn_count"] + w["amount_vs_baseline"] + w["txn_type_mismatch"]
        expected = (w["amount_spike"] * 1.0 + w["amount_vs_baseline"] * 0.5) / active
        assert aggregate_risk(signals, cfg=CFG) == pytest.approx(expected)

    def test_all_quiet_scores_zero(self):
        signals = dict.fromkeys(
            ("amount_spike", "txn_count", "amount_vs_baseline", "txn_type_mismatch"), 0.0
        )
        assert aggregate_risk(signals, cfg=CFG) == 0.0

    def test_all_firing_scores_one(self):
        signals = dict.fromkeys(
            ("amount_spike", "txn_count", "amount_vs_baseline", "txn_type_mismatch"), 1.0
        )
        assert aggregate_risk(signals, cfg=CFG) == pytest.approx(1.0)


# -- Redis window maintenance ----------------------------------------------------


class TestRecordTransaction:
    def test_old_members_evicted_per_window(self):
        fake = FakeRedis()
        record_transaction("ACC-A", "T1", BASE_TS_MS, fake, cfg=CFG)
        three_hours = 3 * 3600 * 1000
        record_transaction("ACC-A", "T2", BASE_TS_MS + three_hours, fake, cfg=CFG)
        prefix = ACFG["key_prefixes"]["user"]
        assert fake.zcard(f"{prefix}ACC-A:count_2min") == 1
        assert fake.zcard(f"{prefix}ACC-A:count_1hr") == 1

    def test_keys_carry_window_plus_slack_ttl(self):
        fake = FakeRedis()
        record_transaction("ACC-A", "T1", BASE_TS_MS, fake, cfg=CFG)
        prefix = ACFG["key_prefixes"]["user"]
        slack = ACFG["key_ttl_slack_s"]
        for window, w_s in ACFG["windows_s"].items():
            assert fake.ttls[f"{prefix}ACC-A:{window}"] == w_s + slack

    def test_redis_down_raises(self):
        with pytest.raises(RedisUnavailableError):
            record_transaction("ACC-A", "T1", BASE_TS_MS, DeadRedis(), cfg=CFG)


# -- the agent end to end ---------------------------------------------------------


class TestVelocityAgentEvaluate:
    async def test_warm_account_large_amount(self):
        agent = VelocityAgent(client=warm_fake(), cfg=CFG)
        risk, confidence = await agent.evaluate(make_event(amount=12_000.0))
        assert 0.0 <= risk <= 1.0
        assert risk > 0.4  # 12x average trips both amount signals
        assert confidence == 1.0  # observation_count 200 >= threshold

    async def test_warm_account_normal_txn_scores_low(self):
        agent = VelocityAgent(client=warm_fake(), cfg=CFG)
        risk, confidence = await agent.evaluate(make_event(amount=900.0))
        assert risk < 0.2
        assert confidence == 1.0

    async def test_cold_start_zero_observations(self):
        agent = VelocityAgent(client=FakeRedis(), cfg=CFG)
        risk, confidence = await agent.evaluate(make_event(account="ACC-NEW"))
        assert confidence == 0.0
        assert 0.0 <= risk <= 1.0

    async def test_burst_raises_risk(self):
        agent = VelocityAgent(client=warm_fake(), cfg=CFG)
        first_risk, _ = await agent.evaluate(make_event(txn_id="T0"))
        last_risk = first_risk
        for i in range(1, 12):
            event = make_event(txn_id=f"T{i}", ts=BASE_TS + timedelta(seconds=i))
            last_risk, _ = await agent.evaluate(event)
        assert last_risk > first_risk

    async def test_redis_unavailable_raises_specific_error(self):
        agent = VelocityAgent(client=DeadRedis(), cfg=CFG)
        with pytest.raises(RedisUnavailableError):
            await agent.evaluate(make_event())

    async def test_redis_unavailable_real_client_fails_fast(self):
        cfg = copy.deepcopy(CFG)
        cfg["redis"]["port"] = 6399  # nothing listens here
        cfg["redis"]["socket_timeout_s"] = 0.05
        agent = VelocityAgent(cfg=cfg)
        started = time.perf_counter()
        with pytest.raises(RedisUnavailableError):
            await agent.evaluate(make_event())
        assert time.perf_counter() - started < 1.0  # fails fast, no hang


# -- latency (live Redis) ----------------------------------------------------------


def _scoped_cfg() -> dict:
    cfg = copy.deepcopy(load_config())
    scope = f"test:{uuid.uuid4().hex[:8]}:"
    prefixes = cfg["velocity_agent"]["key_prefixes"]
    for name in prefixes:
        prefixes[name] = scope + prefixes[name]
    return cfg


@pytest.fixture()
def live_agent():
    cfg = _scoped_cfg()
    rc = cfg["redis"]
    client = redis.Redis(
        host=rc["host"],
        port=rc["port"],
        socket_timeout=rc["socket_timeout_s"],
        socket_connect_timeout=rc["socket_timeout_s"],
        decode_responses=True,
    )
    try:
        client.ping()
    except redis.RedisError:
        pytest.skip("Redis not reachable on the configured host/port")
    yield VelocityAgent(client=client, cfg=cfg)
    for prefix in cfg["velocity_agent"]["key_prefixes"].values():
        keys = client.keys(prefix + "*")
        if keys:
            client.delete(*keys)


class TestLatency:
    N_ACCOUNTS = 20
    N_EVENTS = 1000

    async def test_p99_under_2ms(self, live_agent):
        """The paper's architectural claim: 1-2 ms per evaluation on Redis."""
        for i in range(self.N_ACCOUNTS):
            write_baseline(f"ACC-{i}", WARM_BASELINE, live_agent.client, cfg=live_agent.cfg)
            write_type_dist(f"ACC-{i}", TYPE_DIST, live_agent.client, cfg=live_agent.cfg)

        for i in range(50):  # warm up connections and code paths
            await live_agent.evaluate(
                make_event(txn_id=f"WARM-{i}", account=f"ACC-{i % self.N_ACCOUNTS}")
            )

        latencies_ms = []
        for i in range(self.N_EVENTS):
            event = make_event(
                txn_id=f"T{i}",
                account=f"ACC-{i % self.N_ACCOUNTS}",
                amount=500.0 + i,
                ts=BASE_TS + timedelta(milliseconds=500 * i),
            )
            started = time.perf_counter()
            risk, confidence = await live_agent.evaluate(event)
            latencies_ms.append((time.perf_counter() - started) * 1000)
            assert 0.0 <= risk <= 1.0 and 0.0 <= confidence <= 1.0

        latencies_ms.sort()
        p50 = latencies_ms[len(latencies_ms) // 2]
        p99 = latencies_ms[int(0.99 * len(latencies_ms)) - 1]
        print(f"\nvelocity agent latency: p50={p50:.3f}ms p99={p99:.3f}ms")
        assert p99 < 2.0, f"p99 {p99:.3f}ms breaches the paper's 2ms budget"
