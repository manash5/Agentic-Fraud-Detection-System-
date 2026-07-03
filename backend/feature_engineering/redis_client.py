"""Thin wrapper around the Redis hot-path state for the Velocity agent.

Key design (all names/TTLs from feature_config.yaml):

- ``velocity:{account_id}``      sorted set, score = txn epoch-ms, member = txn_id.
  ZADD on every txn, ZREMRANGEBYSCORE trims beyond the longest window (7d),
  ZCOUNT per window boundary yields txn_count_1m/5m/15m/1h/24h/7d without
  touching Postgres.
- ``velocity_amt:{account_id}``  sorted set, score = txn epoch-ms,
  member = "{amount:.2f}:{txn_id}" (txn_id suffix keeps members unique).
  ZRANGEBYSCORE over the 1h/24h boundaries gives total_amount windows.
- ``account_baseline:{account_id}``  hash with the nightly 30d/90d baselines
  (avg/std amount, distance stats). Written by nightly_baseline_job, 26h TTL.

Every key carries a TTL (velocity keys: 8d — one day past the longest window;
baseline: 26h), so idle accounts self-clean. Run Redis with
``maxmemory-policy volatile-ttl``: under memory pressure only expirable keys
are evicted, and Postgres can always rebuild what was lost — Redis is a
derived cache, never the source of truth.

Window semantics: an event is inside window ``w`` iff
``score > now_ms - w*1000`` and ``score <= now_ms`` — and the current txn is
ZADDed *before* counting, so every count includes the current transaction
(minimum value 1). The batch SQL mirrors this with
``RANGE ... AND CURRENT ROW``.

All Redis errors surface as :class:`RedisUnavailable`; callers degrade to the
Postgres fallback instead of failing the transaction.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

import redis

from feature_engineering.config import load_config

logger = logging.getLogger(__name__)


class RedisUnavailable(Exception):
    """Raised when Redis cannot serve the hot path; caller must fall back."""


class VelocityStateStore:
    """Real-time per-account velocity state on Redis sorted sets."""

    def __init__(
        self,
        client: redis.Redis | None = None,
        cfg: dict[str, Any] | None = None,
    ) -> None:
        """``client`` may be injected (tests); otherwise built from config."""
        self.cfg = cfg or load_config()
        rc = self.cfg["redis"]
        self.prefixes = rc["key_prefixes"]
        self.velocity_ttl_s: int = rc["ttl"]["velocity_keys_s"]
        self.baseline_ttl_s: int = rc["ttl"]["baseline_s"]
        self.windows_s: dict[str, int] = self.cfg["velocity"]["windows_s"]
        self.amount_windows_s: dict[str, int] = self.cfg["velocity"]["amount_windows_s"]
        self.longest_window_ms: int = max(self.windows_s.values()) * 1000
        self.client = client or redis.Redis(
            host=rc["host"],
            port=rc["port"],
            socket_timeout=rc["socket_timeout_s"],
            socket_connect_timeout=rc["socket_timeout_s"],
            decode_responses=True,
        )

    # -- health -------------------------------------------------------------

    def ping(self) -> bool:
        """True if Redis answers PING."""
        try:
            return bool(self.client.ping())
        except redis.RedisError:
            return False

    def warn_if_bad_eviction_policy(self) -> None:
        """Log a warning when maxmemory-policy differs from the recommended one."""
        recommended = self.cfg["redis"]["recommended_maxmemory_policy"]
        try:
            policy = self.client.config_get("maxmemory-policy").get(
                "maxmemory-policy", ""
            )
        except redis.RedisError:
            return  # CONFIG may be disabled on managed Redis; not fatal
        if policy and policy != recommended:
            logger.warning(
                "Redis maxmemory-policy is %r; recommend %r (all velocity keys carry TTLs)",
                policy,
                recommended,
            )

    # -- hot path -----------------------------------------------------------

    def record_and_count(
        self, account_id: str, txn_id: str, ts_ms: int, amount_npr: float
    ) -> dict[str, float]:
        """Record one transaction and return all sliding-window aggregates.

        One round trip: ZADD both keys, trim entries older than the longest
        window, EXPIRE, then ZCOUNT per count-window and ZRANGEBYSCORE per
        amount-window. Counts include the just-added transaction.

        Raises :class:`RedisUnavailable` on any Redis failure.
        """
        vel_key = self.prefixes["velocity"] + account_id
        amt_key = self.prefixes["velocity_amount"] + account_id
        trim_before = ts_ms - self.longest_window_ms
        try:
            pipe = self.client.pipeline(transaction=True)
            pipe.zadd(vel_key, {txn_id: ts_ms})
            pipe.zadd(amt_key, {f"{amount_npr:.2f}:{txn_id}": ts_ms})
            pipe.zremrangebyscore(vel_key, "-inf", f"({trim_before}")
            pipe.zremrangebyscore(amt_key, "-inf", f"({trim_before}")
            pipe.expire(vel_key, self.velocity_ttl_s)
            pipe.expire(amt_key, self.velocity_ttl_s)
            for w_s in self.windows_s.values():
                pipe.zcount(vel_key, f"({ts_ms - w_s * 1000}", ts_ms)
            for w_s in self.amount_windows_s.values():
                pipe.zrangebyscore(amt_key, f"({ts_ms - w_s * 1000}", ts_ms)
            results = pipe.execute()
        except redis.RedisError as exc:
            raise RedisUnavailable(f"Redis hot path failed: {exc}") from exc

        n_setup = 6
        out: dict[str, float] = {}
        for i, name in enumerate(self.windows_s):
            out[name] = float(results[n_setup + i])
        offset = n_setup + len(self.windows_s)
        for i, name in enumerate(self.amount_windows_s):
            members = results[offset + i]
            out[name] = float(sum(float(m.split(":", 1)[0]) for m in members))
        return out

    # -- baseline cache -----------------------------------------------------

    def get_baseline(self, account_id: str) -> dict[str, float] | None:
        """Read the cached nightly baseline hash; None on cache miss.

        Raises :class:`RedisUnavailable` if Redis is down.
        """
        key = self.prefixes["baseline"] + account_id
        try:
            raw = self.client.hgetall(key)
        except redis.RedisError as exc:
            raise RedisUnavailable(f"Redis baseline read failed: {exc}") from exc
        if not raw:
            return None
        return {k: float(v) for k, v in raw.items() if k != "baseline_date"}

    def set_baseline(self, account_id: str, baseline: Mapping[str, Any]) -> None:
        """Cache one account's nightly baseline with the configured TTL."""
        key = self.prefixes["baseline"] + account_id
        try:
            pipe = self.client.pipeline(transaction=True)
            pipe.hset(key, mapping={k: str(v) for k, v in baseline.items()})
            pipe.expire(key, self.baseline_ttl_s)
            pipe.execute()
        except redis.RedisError as exc:
            raise RedisUnavailable(f"Redis baseline write failed: {exc}") from exc
