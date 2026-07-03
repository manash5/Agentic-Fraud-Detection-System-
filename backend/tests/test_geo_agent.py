"""Tests for the paper §IV-C-2 Phase 1 Geo Agent (agents.geo_agent).

Unit tests run against in-memory async fakes for Redis and the asyncpg
pool; the concurrent load test needs live Redis + Postgres (skipped
otherwise, test-scoped key prefixes, cleaned up after).
"""

from __future__ import annotations

import asyncio
import copy
import math
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import redis as redis_sync

from agents.geo_agent import (
    GeoAgent,
    PostgresUnavailableError,
    confidence_score,
    device_novelty_signal,
    get_last_location,
    get_observation_count,
    travel_feasibility_signal,
)
from agents.velocity_agent import RedisUnavailableError
from feature_engineering.config import load_config
from feature_engineering.geo_features import haversine_km

CFG = load_config()
ACFG = CFG["geo_agent"]

BASE_TS = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

KATHMANDU = (27.7172, 85.3240)
POKHARA = (28.2096, 83.9856)     # ~145 km from Kathmandu
MOSCOW = (55.7558, 37.6173)      # ~4,300 km from Kathmandu


# -- async in-memory fakes -----------------------------------------------------


class FakeAsyncPipeline:
    def __init__(self, fake):
        self.fake = fake
        self.ops = []

    def __getattr__(self, name):
        def queue(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self

        return queue

    async def execute(self):
        return [
            await getattr(self.fake, name)(*args, **kwargs)
            for name, args, kwargs in self.ops
        ]


class FakeAsyncRedis:
    """Just the commands the geo agent uses."""

    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.strings: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def pipeline(self, transaction=True):
        return FakeAsyncPipeline(self)

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def get(self, key):
        return self.strings.get(key)

    async def set(self, key, value, ex=None):
        self.strings[key] = str(value)
        if ex is not None:
            self.ttls[key] = int(ex)
        return True

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hset(self, key, mapping=None):
        h = self.hashes.setdefault(key, {})
        new = sum(1 for k in mapping if k not in h)
        h.update({str(k): str(v) for k, v in mapping.items()})
        return new

    async def sismember(self, key, member):
        return member in self.sets.get(key, set())

    async def sadd(self, key, *members):
        s = self.sets.setdefault(key, set())
        added = len(set(members) - s)
        s.update(members)
        return added

    async def scard(self, key):
        return len(self.sets.get(key, set()))

    async def expire(self, key, ttl):
        self.ttls[key] = int(ttl)
        return True

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            removed += int(self.hashes.pop(key, None) is not None)
            removed += int(self.sets.pop(key, None) is not None)
        return removed


class DeadAsyncRedis:
    """Every command fails the way a downed Redis does."""

    def pipeline(self, transaction=True):
        raise redis_sync.ConnectionError("connection refused")

    def __getattr__(self, name):
        async def boom(*args, **kwargs):
            raise redis_sync.ConnectionError("connection refused")

        return boom


class FakePgPool:
    """Routes the agent's three queries to canned rows; records calls."""

    def __init__(self, last_location=None, geo_count=0, devices=None):
        self.last_location = last_location  # dict row or None
        self.geo_count = geo_count
        self.devices = devices or {}        # device_id -> dict row
        self.queries: list[str] = []

    async def fetchrow(self, query, *args):
        self.queries.append(query)
        if "count(*)" in query:
            return {"count": self.geo_count}
        if "FROM geo_events" in query:
            return self.last_location
        if "FROM device_fingerprints" in query:
            return self.devices.get(args[0])
        raise AssertionError(f"unexpected query: {query}")


class DeadPgPool:
    async def fetchrow(self, query, *args):
        raise ConnectionError("postgres down")


def last_loc_key(account_id):
    return ACFG["key_prefixes"]["last_location"] + account_id


def devices_key(account_id):
    return ACFG["key_prefixes"]["known_devices"] + account_id


def cache_location(fake, account_id, lat, lon, ts):
    fake.hashes[last_loc_key(account_id)] = {
        "lat": str(lat),
        "lon": str(lon),
        "ts_epoch_ms": str(int(ts.timestamp() * 1000)),
        "device_id": "DEV-PREV",
    }


def fingerprint(rooted=False, vpn=False, accounts=1, shared=False, signals=0):
    return {
        "is_rooted_or_jailbroken": rooted,
        "vpn_detected": vpn,
        "num_accounts_seen_on_device": accounts,
        "is_shared_device": shared,
        "risk_signal_count": signals,
    }


# -- signal 1: travel feasibility -----------------------------------------------


class TestTravelFeasibility:
    async def test_first_ever_transaction_scores_zero(self):
        score = await travel_feasibility_signal(
            "ACC-NEW", *KATHMANDU, BASE_TS, FakeAsyncRedis(), FakePgPool(), cfg=CFG
        )
        assert score == 0.0

    async def test_impossible_travel_saturates(self):
        fake = FakeAsyncRedis()
        cache_location(fake, "ACC-A", *KATHMANDU, ts=BASE_TS - timedelta(hours=1))
        score = await travel_feasibility_signal(
            "ACC-A", *MOSCOW, BASE_TS, fake, FakePgPool(), cfg=CFG
        )
        assert score == 1.0  # ~4300 km/h vs 900 max

    async def test_short_hop_is_jitter_not_signal(self):
        fake = FakeAsyncRedis()
        cache_location(fake, "ACC-A", *KATHMANDU, ts=BASE_TS - timedelta(minutes=1))
        nearby = (KATHMANDU[0] + 0.05, KATHMANDU[1])  # ~5.5 km
        score = await travel_feasibility_signal(
            "ACC-A", *nearby, BASE_TS, fake, FakePgPool(), cfg=CFG
        )
        assert score == 0.0

    async def test_plausible_travel_scores_zero(self):
        fake = FakeAsyncRedis()
        cache_location(fake, "ACC-A", *KATHMANDU, ts=BASE_TS - timedelta(hours=3))
        score = await travel_feasibility_signal(
            "ACC-A", *POKHARA, BASE_TS, fake, FakePgPool(), cfg=CFG
        )
        assert score == 0.0  # ~145 km in 3h ≈ 48 km/h

    async def test_gradient_boundary_at_max_plausible_kmh(self):
        """Exactly AT the 900 km/h threshold the gradient reads 0.5."""
        travel = ACFG["travel"]
        km = float(haversine_km(*KATHMANDU, *MOSCOW))

        async def score_at(speed_kmh):
            fake = FakeAsyncRedis()
            dt = timedelta(hours=km / speed_kmh)
            cache_location(fake, "ACC-A", *KATHMANDU, ts=BASE_TS - dt)
            return await travel_feasibility_signal(
                "ACC-A", *MOSCOW, BASE_TS, fake, FakePgPool(), cfg=CFG
            )

        max_kmh = travel["max_plausible_kmh"]
        at = await score_at(max_kmh)
        below = await score_at(max_kmh * 0.99)
        above = await score_at(max_kmh * 1.01)
        assert at == pytest.approx(0.5, abs=1e-6)
        assert below < at < above  # gradient, not a hard boolean
        assert await score_at(max_kmh * travel["gradient_start_ratio"]) == pytest.approx(0.0, abs=1e-6)
        assert await score_at(max_kmh * travel["gradient_full_ratio"]) == pytest.approx(1.0, abs=1e-6)

    async def test_teleport_and_out_of_order_timestamps(self):
        fake = FakeAsyncRedis()
        cache_location(fake, "ACC-A", *KATHMANDU, ts=BASE_TS - timedelta(seconds=5))
        assert await travel_feasibility_signal(
            "ACC-A", *MOSCOW, BASE_TS, fake, FakePgPool(), cfg=CFG
        ) == 1.0
        cache_location(fake, "ACC-A", *KATHMANDU, ts=BASE_TS + timedelta(minutes=10))
        assert await travel_feasibility_signal(
            "ACC-A", *MOSCOW, BASE_TS, fake, FakePgPool(), cfg=CFG
        ) == 1.0

    async def test_redis_miss_falls_back_to_postgres(self):
        pool = FakePgPool(
            last_location={
                "latitude": KATHMANDU[0],
                "longitude": KATHMANDU[1],
                # naive, as asyncpg returns TIMESTAMP WITHOUT TIME ZONE
                "timestamp": (BASE_TS - timedelta(hours=1)).replace(tzinfo=None),
            }
        )
        score = await travel_feasibility_signal(
            "ACC-A", *MOSCOW, BASE_TS, FakeAsyncRedis(), pool, cfg=CFG
        )
        assert score == 1.0
        assert any("FROM geo_events" in q for q in pool.queries)

    async def test_redis_hit_never_touches_postgres(self):
        fake = FakeAsyncRedis()
        cache_location(fake, "ACC-A", *KATHMANDU, ts=BASE_TS - timedelta(hours=1))
        pool = FakePgPool()
        await travel_feasibility_signal("ACC-A", *MOSCOW, BASE_TS, fake, pool, cfg=CFG)
        assert pool.queries == []

    async def test_redis_down_raises(self):
        with pytest.raises(RedisUnavailableError):
            await travel_feasibility_signal(
                "ACC-A", *KATHMANDU, BASE_TS, DeadAsyncRedis(), FakePgPool(), cfg=CFG
            )

    async def test_postgres_down_on_cache_miss_raises(self):
        with pytest.raises(PostgresUnavailableError):
            await travel_feasibility_signal(
                "ACC-A", *KATHMANDU, BASE_TS, FakeAsyncRedis(), DeadPgPool(), cfg=CFG
            )


# -- signal 2: device novelty -----------------------------------------------------


class TestDeviceNovelty:
    async def test_known_device_scores_zero(self):
        fake = FakeAsyncRedis()
        fake.sets[devices_key("ACC-A")] = {"DEV-1"}
        score = await device_novelty_signal("ACC-A", "DEV-1", fake, FakePgPool(), cfg=CFG)
        assert score == 0.0

    async def test_first_ever_device_is_neutral_not_risky(self):
        score = await device_novelty_signal(
            "ACC-NEW", "DEV-1", FakeAsyncRedis(), FakePgPool(), cfg=CFG
        )
        assert score == ACFG["device"]["first_device_score"]
        assert score < 0.5

    async def test_unknown_device_elevated_base(self):
        fake = FakeAsyncRedis()
        fake.sets[devices_key("ACC-A")] = {"DEV-OLD"}
        score = await device_novelty_signal("ACC-A", "DEV-NEW", fake, FakePgPool(), cfg=CFG)
        assert score == ACFG["device"]["unknown_device_score"]

    async def test_fingerprint_enrichment_pushes_higher(self):
        d = ACFG["device"]
        fake = FakeAsyncRedis()
        fake.sets[devices_key("ACC-A")] = {"DEV-OLD"}
        pool = FakePgPool(devices={
            "DEV-ROOTED": fingerprint(rooted=True),
            "DEV-EVIL": fingerprint(rooted=True, shared=True, accounts=5),
        })
        rooted = await device_novelty_signal("ACC-A", "DEV-ROOTED", fake, pool, cfg=CFG)
        assert rooted == pytest.approx(d["unknown_device_score"] + d["rooted_bonus"])
        fake.sets[devices_key("ACC-B")] = {"DEV-OLD"}
        evil = await device_novelty_signal("ACC-B", "DEV-EVIL", fake, pool, cfg=CFG)
        assert evil == pytest.approx(min(
            1.0,
            d["unknown_device_score"] + d["rooted_bonus"] + d["shared_bonus"] + d["multi_account_bonus"],
        ))

    async def test_device_missing_from_fingerprints_is_neutral_metadata(self):
        fake = FakeAsyncRedis()
        fake.sets[devices_key("ACC-A")] = {"DEV-OLD"}
        score = await device_novelty_signal(
            "ACC-A", "DEV-UNKNOWN-EVERYWHERE", fake, FakePgPool(devices={}), cfg=CFG
        )
        assert score == ACFG["device"]["unknown_device_score"]  # base only, no crash

    async def test_device_is_learned_for_next_time(self):
        fake = FakeAsyncRedis()
        first = await device_novelty_signal("ACC-A", "DEV-1", fake, FakePgPool(), cfg=CFG)
        assert "DEV-1" in fake.sets[devices_key("ACC-A")]
        assert 0 < fake.ttls[devices_key("ACC-A")] <= ACFG["ttl"]["known_devices_s"]
        second = await device_novelty_signal("ACC-A", "DEV-1", fake, FakePgPool(), cfg=CFG)
        assert first == ACFG["device"]["first_device_score"] and second == 0.0

    async def test_redis_down_raises(self):
        with pytest.raises(RedisUnavailableError):
            await device_novelty_signal("ACC-A", "DEV-1", DeadAsyncRedis(), FakePgPool(), cfg=CFG)


# -- confidence and observation count ------------------------------------------------


class TestConfidence:
    def test_smooth_ramp(self):
        threshold = ACFG["confidence"]["observation_threshold"]
        assert confidence_score(0, threshold) == 0.0
        assert confidence_score(threshold // 2, threshold) == pytest.approx(0.5)
        assert confidence_score(threshold, threshold) == 1.0
        assert confidence_score(threshold * 3, threshold) == 1.0

    def test_independent_threshold_from_velocity(self):
        assert (
            ACFG["confidence"]["observation_threshold"]
            != CFG["velocity_agent"]["confidence"]["observation_threshold"]
        )

    async def test_observation_count_prefers_baseline_cache(self):
        fake = FakeAsyncRedis()
        fake.hashes[ACFG["key_prefixes"]["baseline"] + "ACC-A"] = {"n_geo_90d": "37"}
        pool = FakePgPool(geo_count=999)
        assert await get_observation_count("ACC-A", fake, pool, cfg=CFG) == 37
        assert pool.queries == []

    async def test_observation_count_falls_back_to_postgres_then_memoizes(self):
        fake = FakeAsyncRedis()
        pool = FakePgPool(geo_count=12)
        assert await get_observation_count("ACC-A", fake, pool, cfg=CFG) == 12
        n_pg = len(pool.queries)
        assert await get_observation_count("ACC-A", fake, pool, cfg=CFG) == 12
        assert len(pool.queries) == n_pg  # second read served from geo:obs cache
        obs_key = ACFG["key_prefixes"]["observation_count"] + "ACC-A"
        assert 0 < fake.ttls[obs_key] <= ACFG["ttl"]["observation_count_s"]


# -- the agent end to end -----------------------------------------------------------


class TestGeoAgentEvaluate:
    def _agent(self, fake=None, pool=None):
        return GeoAgent(
            redis_client=fake or FakeAsyncRedis(),
            pg_pool=pool or FakePgPool(),
            cfg=CFG,
        )

    async def test_cold_start_low_risk_low_confidence(self):
        agent = self._agent()
        risk, confidence, signals = await agent.evaluate(
            account_id="ACC-NEW", txn_id="T1", device_id="DEV-1",
            latitude=KATHMANDU[0], longitude=KATHMANDU[1], timestamp=BASE_TS,
        )
        assert confidence == 0.0
        assert risk <= 0.2  # first device neutral, no travel history
        assert set(signals) == {"travel_feasibility", "device_novelty"}

    async def test_risk_is_documented_5050_split(self):
        fake = FakeAsyncRedis()
        cache_location(fake, "ACC-A", *KATHMANDU, ts=BASE_TS - timedelta(hours=1))
        fake.sets[devices_key("ACC-A")] = {"DEV-OLD"}
        agent = self._agent(fake=fake)
        risk, _, signals = await agent.evaluate(
            account_id="ACC-A", txn_id="T1", device_id="DEV-NEW",
            latitude=MOSCOW[0], longitude=MOSCOW[1], timestamp=BASE_TS,
        )
        w = ACFG["weights"]
        assert risk == pytest.approx(
            w["travel_feasibility"] * signals["travel_feasibility"]
            + w["device_novelty"] * signals["device_novelty"]
        )
        assert signals["travel_feasibility"] == 1.0

    async def test_evaluate_records_new_last_location(self):
        fake = FakeAsyncRedis()
        agent = self._agent(fake=fake)
        await agent.evaluate(
            account_id="ACC-A", txn_id="T1", device_id="DEV-1",
            latitude=KATHMANDU[0], longitude=KATHMANDU[1], timestamp=BASE_TS,
        )
        cached = fake.hashes[last_loc_key("ACC-A")]
        assert float(cached["lat"]) == pytest.approx(KATHMANDU[0])
        assert cached["device_id"] == "DEV-1"
        assert 0 < fake.ttls[last_loc_key("ACC-A")] <= ACFG["ttl"]["last_location_s"]

    async def test_pg_fallback_then_next_txn_hits_redis(self):
        """Redis miss -> Postgres -> the evaluate() writes repopulate Redis."""
        pool = FakePgPool(last_location={
            "latitude": KATHMANDU[0], "longitude": KATHMANDU[1],
            "timestamp": (BASE_TS - timedelta(hours=1)).replace(tzinfo=None),
        })
        fake = FakeAsyncRedis()
        agent = self._agent(fake=fake, pool=pool)
        await agent.evaluate(
            account_id="ACC-A", txn_id="T1", device_id="DEV-1",
            latitude=POKHARA[0], longitude=POKHARA[1], timestamp=BASE_TS,
        )
        n_geo_queries = sum("FROM geo_events" in q and "count(*)" not in q for q in pool.queries)
        assert n_geo_queries == 1
        await agent.evaluate(
            account_id="ACC-A", txn_id="T2", device_id="DEV-1",
            latitude=POKHARA[0], longitude=POKHARA[1], timestamp=BASE_TS + timedelta(minutes=5),
        )
        n_geo_queries_after = sum("FROM geo_events" in q and "count(*)" not in q for q in pool.queries)
        assert n_geo_queries_after == n_geo_queries  # second txn served from Redis

    async def test_redis_down_raises_specific_error(self):
        agent = self._agent(fake=DeadAsyncRedis())
        with pytest.raises(RedisUnavailableError):
            await agent.evaluate(
                account_id="ACC-A", txn_id="T1", device_id="DEV-1",
                latitude=KATHMANDU[0], longitude=KATHMANDU[1], timestamp=BASE_TS,
            )


# -- live concurrent load test (Redis + Postgres) --------------------------------------


def _scoped_cfg() -> dict:
    cfg = copy.deepcopy(load_config())
    scope = f"test:{uuid.uuid4().hex[:8]}:"
    prefixes = cfg["geo_agent"]["key_prefixes"]
    for name in prefixes:
        prefixes[name] = scope + prefixes[name]
    return cfg


class TestConcurrentLoad:
    N_ACCOUNTS = 20
    N_REQUESTS = 200

    async def test_pools_survive_concurrency_within_budget(self):
        cfg = _scoped_cfg()
        rc = cfg["redis"]
        probe = redis_sync.Redis(host=rc["host"], port=rc["port"], socket_timeout=0.25)
        try:
            probe.ping()
        except redis_sync.RedisError:
            pytest.skip("Redis not reachable")
        agent = GeoAgent(cfg=cfg)
        try:
            await agent.connect()
        except Exception:
            pytest.skip("Postgres not reachable for asyncpg")
        try:
            async def one(i: int, latencies: list[float]):
                started = time.monotonic()
                risk, confidence, _ = await agent.evaluate(
                    account_id=f"ACC-LOAD-{i % self.N_ACCOUNTS}",
                    txn_id=f"T{i}",
                    device_id=f"DEV-LOAD-{i % 7}",
                    latitude=KATHMANDU[0] + (i % 10) * 0.001,
                    longitude=KATHMANDU[1],
                    timestamp=BASE_TS + timedelta(seconds=i),
                )
                latencies.append((time.monotonic() - started) * 1000)
                assert 0.0 <= risk <= 1.0 and 0.0 <= confidence <= 1.0

            # Phase 1 — pool survival: an all-cold simultaneous burst where
            # every request misses Redis and stampedes Postgres through the
            # 20-connection pool. Must complete without a single error
            # (blocking pools queue; they never raise "too many connections").
            cold: list[float] = []
            await asyncio.gather(*(one(i, cold) for i in range(self.N_REQUESTS)))

            # Phase 2 — latency budget in steady state: caches are now warm
            # (geo:last + known devices populated), which is the operating
            # regime the paper's 20-50ms Geo Agent budget describes.
            warm: list[float] = []
            await asyncio.gather(
                *(one(i + self.N_REQUESTS, warm) for i in range(self.N_REQUESTS))
            )
            warm.sort()
            p50 = warm[len(warm) // 2]
            p95 = warm[int(0.95 * len(warm)) - 1]
            print(f"\ngeo agent latency: cold-burst max={max(cold):.2f}ms | warm p50={p50:.2f}ms p95={p95:.2f}ms")
            assert p95 < 50.0
        finally:
            for prefix in cfg["geo_agent"]["key_prefixes"].values():
                keys = probe.keys(prefix + "*")
                if keys:
                    probe.delete(*keys)
            await agent.close()
