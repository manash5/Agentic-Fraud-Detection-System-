"""Geo Agent — paper §IV-C-2, Phase 1: travel feasibility + device novelty.

Two signals over a Redis-first hot path with a Postgres fallback, aggregated
50/50 (the paper fixes only the Synthesis Agent's Table I weights, not the
Geo Agent's internal split — see the rationale note in feature_config.yaml).
Budget: well under the paper's 20-50 ms Geo Agent envelope, since Phase 1
has no Neo4j.

Deliberately ABSENT (not stubbed): shared-IP, circular-flow and fraud-ring
checks. Those belong to a future Graph Agent built from the account_graph
CSVs; this agent's aggregation simply doesn't include them.

Redis layout (names/TTLs in feature_config.yaml ``geo_agent:``):

- ``geo:last:{account_id}``      HASH {lat, lon, ts_epoch_ms, device_id}, 24h TTL.
  Read BEFORE being overwritten with the incoming transaction.
- ``devices:known:{account_id}`` SET of device_ids, 90d TTL. The incoming
  device is SADDed after the novelty check, so the agent learns either way.
- ``account_baseline:{account_id}`` nightly hash — ``n_geo_90d`` drives the
  confidence score without a per-request Postgres COUNT.

Postgres fallback (asyncpg, only on Redis cache miss):
The implementation brief flagged "account_id was dropped from
transactions_raw/geo_events" as a blocker requiring a schema change
(Option A) or a lookup table (Option B). Verified against the live
``fraud_detection_global`` schema: **both tables already carry account_id**
(and ``idx_geo_account`` / ``idx_txn_account_ts`` exist), i.e. Option A is
already satisfied — no schema change, and the last-location query reads
``geo_events`` directly with no join.

Timestamps: ``geo_events.timestamp`` is TIMESTAMP WITHOUT TIME ZONE; all
naive datetimes in this module are interpreted as UTC.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

import asyncpg
import redis.asyncio as aioredis
from redis import RedisError
from redis.backoff import NoBackoff
from redis.retry import Retry

from agents.velocity_agent import RedisUnavailableError  # canonical, shared
from feature_engineering.config import load_config
from feature_engineering.geo_features import haversine_km

logger = logging.getLogger(__name__)


class PostgresUnavailableError(Exception):
    """Postgres cannot serve the fallback path; the caller maps this to 503."""


LAST_LOCATION_SQL = """
SELECT latitude, longitude, "timestamp"
FROM geo_events
WHERE account_id = $1 AND "timestamp" < $2
ORDER BY "timestamp" DESC
LIMIT 1
"""

OBSERVATION_COUNT_SQL = """
SELECT count(*) FROM geo_events WHERE account_id = $1
"""

DEVICE_FINGERPRINT_SQL = """
SELECT is_rooted_or_jailbroken, vpn_detected, num_accounts_seen_on_device,
       is_shared_device, risk_signal_count
FROM device_fingerprints
WHERE device_id = $1
"""


def _acfg(cfg: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return (cfg or load_config())["geo_agent"]


def _clip01(x: float) -> float:
    return min(1.0, max(0.0, x))


def _as_utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def _pg_connect_kwargs(dsn: str) -> dict[str, Any]:
    """Translate the config's libpq-style DSN into asyncpg kwargs."""
    if dsn.startswith(("postgres://", "postgresql://")):
        return {"dsn": dsn}
    key_map = {"dbname": "database", "host": "host", "port": "port",
               "user": "user", "password": "password"}
    kwargs: dict[str, Any] = {}
    for token in dsn.split():
        key, _, value = token.partition("=")
        if key in key_map:
            kwargs[key_map[key]] = value
    return kwargs


async def _fetchrow(pg_pool: asyncpg.Pool, query: str, *args: Any):
    try:
        return await pg_pool.fetchrow(query, *args)
    except (asyncpg.PostgresError, OSError) as exc:
        raise PostgresUnavailableError(f"geo fallback query failed: {exc}") from exc


# -- signal 1: travel feasibility --------------------------------------------


async def get_last_location(
    account_id: str,
    current_ts: datetime,
    r: aioredis.Redis,
    pg_pool: asyncpg.Pool,
    *,
    cfg: Mapping[str, Any] | None = None,
) -> tuple[float, float, datetime] | None:
    """Last known (lat, lon, ts) for the account; None if no history at all.

    Redis first; on cache miss, the most recent prior geo_event from
    Postgres. The Redis hash is repopulated implicitly when the caller
    records the current transaction as the new last location.
    """
    acfg = _acfg(cfg)
    key = acfg["key_prefixes"]["last_location"] + account_id
    try:
        cached = await r.hgetall(key)
    except RedisError as exc:
        raise RedisUnavailableError(f"geo last-location read failed: {exc}") from exc
    if cached:
        return (
            float(cached["lat"]),
            float(cached["lon"]),
            datetime.fromtimestamp(int(cached["ts_epoch_ms"]) / 1000, tz=timezone.utc),
        )
    row = await _fetchrow(
        pg_pool, LAST_LOCATION_SQL, account_id, _as_utc(current_ts).replace(tzinfo=None)
    )
    if row is None:
        return None
    return float(row["latitude"]), float(row["longitude"]), _as_utc(row["timestamp"])


async def travel_feasibility_signal(
    account_id: str,
    current_lat: float,
    current_lon: float,
    current_ts: datetime,
    r: aioredis.Redis,
    pg_pool: asyncpg.Pool,
    *,
    cfg: Mapping[str, Any] | None = None,
) -> float:
    """Implied travel speed vs the max-plausible threshold, as a gradient.

    score = clip((speed/max_kmh - gradient_start) / (gradient_full -
    gradient_start)): 0 below half the threshold, 0.5 exactly AT it, 1 at
    1.5x — a gradient near the boundary, not a hard boolean. Distances under
    ``min_km`` score 0 (IP-geolocation jitter); real distance covered in
    under ``min_time_delta_s`` (or with out-of-order timestamps) scores 1.
    First-ever transaction for the account: 0.0 — the low confidence score
    carries the uncertainty, not a false positive here.
    """
    travel = _acfg(cfg)["travel"]
    last = await get_last_location(account_id, current_ts, r, pg_pool, cfg=cfg)
    if last is None:
        return 0.0
    prev_lat, prev_lon, prev_ts = last
    km = float(haversine_km(prev_lat, prev_lon, current_lat, current_lon))
    if km < travel["min_km"]:
        return 0.0
    dt_s = (_as_utc(current_ts) - prev_ts).total_seconds()
    if dt_s <= travel["min_time_delta_s"]:
        return 1.0  # teleport: real distance in no (or negative) elapsed time
    speed_kmh = km / (dt_s / 3600.0)
    ratio = speed_kmh / travel["max_plausible_kmh"]
    return _clip01(
        (ratio - travel["gradient_start_ratio"])
        / (travel["gradient_full_ratio"] - travel["gradient_start_ratio"])
    )


# -- signal 2: device novelty -------------------------------------------------


async def device_novelty_signal(
    account_id: str,
    device_id: str,
    r: aioredis.Redis,
    pg_pool: asyncpg.Pool,
    *,
    cfg: Mapping[str, Any] | None = None,
) -> float:
    """How suspicious is this device for this account?

    Known device (in ``devices:known:{account_id}``) -> 0. Unknown device ->
    ``unknown_device_score`` base; the account's first-ever device ->
    ``first_device_score`` (neutral-low, per the cold-start contract). Either
    novelty base is then pushed higher by the system-wide fingerprint:
    rooted/jailbroken, shared device, seen on many accounts. A device_id
    absent from ``device_fingerprints`` contributes no enrichment (neutral
    metadata) but keeps its novelty base. The device is SADDed to the known
    set afterwards regardless, so the agent learns for next time.
    """
    acfg = _acfg(cfg)
    device_cfg = acfg["device"]
    key = acfg["key_prefixes"]["known_devices"] + account_id
    try:
        pipe = r.pipeline(transaction=False)
        pipe.sismember(key, device_id)
        pipe.scard(key)
        known, n_known = await pipe.execute()
    except RedisError as exc:
        raise RedisUnavailableError(f"known-devices read failed: {exc}") from exc

    if known:
        score = 0.0
    else:
        base = (
            device_cfg["first_device_score"]
            if int(n_known) == 0
            else device_cfg["unknown_device_score"]
        )
        row = await _fetchrow(pg_pool, DEVICE_FINGERPRINT_SQL, device_id)
        bonus = 0.0
        if row is not None:
            if row["is_rooted_or_jailbroken"]:
                bonus += device_cfg["rooted_bonus"]
            if row["is_shared_device"]:
                bonus += device_cfg["shared_bonus"]
            if (row["num_accounts_seen_on_device"] or 0) >= device_cfg["multi_account_min"]:
                bonus += device_cfg["multi_account_bonus"]
        score = _clip01(base + bonus)

    try:
        pipe = r.pipeline(transaction=True)
        pipe.sadd(key, device_id)
        pipe.expire(key, acfg["ttl"]["known_devices_s"])
        await pipe.execute()
    except RedisError as exc:
        raise RedisUnavailableError(f"known-devices update failed: {exc}") from exc
    return score


# -- confidence ----------------------------------------------------------------


def confidence_score(observation_count: int, threshold: int = 20) -> float:
    """Smooth cold-start ramp over prior geo_events on file.

    Independent threshold from the Velocity Agent's (20 vs 50) — geolocation
    stabilizes with fewer observations than spending patterns do. Production
    value lives in feature_config.yaml (geo_agent.confidence).
    """
    if threshold <= 0:
        return 1.0
    return min(1.0, max(0, observation_count) / threshold)


async def get_observation_count(
    account_id: str,
    r: aioredis.Redis,
    pg_pool: asyncpg.Pool,
    *,
    cfg: Mapping[str, Any] | None = None,
) -> int:
    """Prior geo_events for the account: nightly baseline cache, else COUNT.

    The Postgres COUNT result is memoized in ``geo:obs:{account_id}`` (1h
    TTL) so accounts the nightly job hasn't baselined yet — exactly the
    cold-start accounts — don't cost a Postgres round trip per transaction.
    """
    acfg = _acfg(cfg)
    baseline_key = acfg["key_prefixes"]["baseline"] + account_id
    obs_key = acfg["key_prefixes"]["observation_count"] + account_id
    try:
        pipe = r.pipeline(transaction=False)
        pipe.hget(baseline_key, "n_geo_90d")
        pipe.get(obs_key)
        baseline_n, cached_n = await pipe.execute()
    except RedisError as exc:
        raise RedisUnavailableError(f"observation-count read failed: {exc}") from exc
    if baseline_n is not None:
        return int(float(baseline_n))
    if cached_n is not None:
        return int(cached_n)
    row = await _fetchrow(pg_pool, OBSERVATION_COUNT_SQL, account_id)
    count = int(row["count"]) if row else 0
    try:
        await r.set(obs_key, count, ex=acfg["ttl"]["observation_count_s"])
    except RedisError as exc:
        raise RedisUnavailableError(f"observation-count write failed: {exc}") from exc
    return count


# -- the agent -------------------------------------------------------------------


class GeoAgent:
    """Phase 1 Geo Agent: Redis-first, asyncpg fallback, no Neo4j."""

    def __init__(
        self,
        redis_client: aioredis.Redis | None = None,
        pg_pool: asyncpg.Pool | None = None,
        cfg: dict[str, Any] | None = None,
    ) -> None:
        """Clients may be injected (tests); otherwise built in :meth:`connect`."""
        self.cfg = cfg or load_config()
        self.acfg = self.cfg["geo_agent"]
        self.redis = redis_client
        self.pg_pool = pg_pool
        self._owns_clients = redis_client is None and pg_pool is None

    async def connect(self) -> None:
        """Create the Redis client and asyncpg pool if none were injected."""
        rc = self.cfg["redis"]
        if self.redis is None:
            # BlockingConnectionPool: concurrent requests QUEUE for a
            # connection when the pool is saturated. The default async pool
            # raises ConnectionError("Too many connections") under bursts.
            pool = aioredis.BlockingConnectionPool(
                host=rc["host"],
                port=rc["port"],
                max_connections=rc["max_connections"],
                socket_timeout=rc["socket_timeout_s"],
                socket_connect_timeout=rc["socket_timeout_s"],
                decode_responses=True,
                retry=Retry(NoBackoff(), retries=0),  # fail fast, same as Velocity
            )
            self.redis = aioredis.Redis(connection_pool=pool)
        if self.pg_pool is None:
            self.pg_pool = await asyncpg.create_pool(
                **_pg_connect_kwargs(self.cfg["database"]["dsn"]),
                min_size=5,
                max_size=20,
            )

    async def close(self) -> None:
        if self._owns_clients:
            if self.redis is not None:
                await self.redis.aclose()
            if self.pg_pool is not None:
                await self.pg_pool.close()

    async def evaluate(
        self,
        account_id: str,
        txn_id: str,
        device_id: str,
        latitude: float,
        longitude: float,
        timestamp: datetime,
    ) -> tuple[float, float, dict[str, float]]:
        """Returns (risk_score, confidence_score, per-signal breakdown).

        Reads last-known state BEFORE overwriting it with this transaction.
        Raises :class:`RedisUnavailableError` / :class:`PostgresUnavailableError`
        instead of inventing a score — the fallback policy is the
        orchestrator's call.
        """
        ts = _as_utc(timestamp)
        travel = await travel_feasibility_signal(
            account_id, latitude, longitude, ts, self.redis, self.pg_pool, cfg=self.cfg
        )
        device = await device_novelty_signal(
            account_id, device_id, self.redis, self.pg_pool, cfg=self.cfg
        )
        observations = await get_observation_count(
            account_id, self.redis, self.pg_pool, cfg=self.cfg
        )
        confidence = confidence_score(
            observations, threshold=self.acfg["confidence"]["observation_threshold"]
        )

        # Record this event as the account's new last-known state (the cache
        # the NEXT transaction will hit).
        key = self.acfg["key_prefixes"]["last_location"] + account_id
        try:
            pipe = self.redis.pipeline(transaction=True)
            pipe.hset(key, mapping={
                "lat": latitude,
                "lon": longitude,
                "ts_epoch_ms": int(ts.timestamp() * 1000),
                "device_id": device_id,
            })
            pipe.expire(key, self.acfg["ttl"]["last_location_s"])
            await pipe.execute()
        except RedisError as exc:
            raise RedisUnavailableError(f"geo last-location write failed: {exc}") from exc

        weights = self.acfg["weights"]
        risk = _clip01(
            weights["travel_feasibility"] * travel + weights["device_novelty"] * device
        )
        return risk, confidence, {
            "travel_feasibility": travel,
            "device_novelty": device,
        }
