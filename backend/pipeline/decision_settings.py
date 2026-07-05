"""Live decision thresholds, shared by the API process and the orchestrator.

The admin Settings page PUTs {otpThreshold, blockThreshold,
disagreementThreshold} which the API persists to Postgres (app_settings) and
mirrors into Redis `config:thresholds`. Both processes read that key here —
through a short in-process cache — so a change takes effect in the running
pipeline within seconds, without restarts.

No FastAPI imports and a synchronous Redis read on purpose: fuse() in
pipeline/agent_runner.py is a sync function called from both processes. Any
Redis problem falls back to the paper's defaults (0.30 / 0.70 / 0.04).
"""

from __future__ import annotations

import json
import logging
import os
import time

import redis

from agents.synthesis_agent import SynthesisConfig

logger = logging.getLogger("decision-settings")

THRESHOLDS_KEY = "config:thresholds"
_CACHE_TTL_S = 5.0

_cached: SynthesisConfig | None = None
_cached_at: float = 0.0
_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis(
            host=os.environ.get("FRAUD_REDIS_HOST", "localhost"),
            port=int(os.environ.get("FRAUD_REDIS_PORT", "6379")),
            decode_responses=True,
            socket_timeout=0.5,
            socket_connect_timeout=0.5,
        )
    return _client


def current_config() -> SynthesisConfig:
    """The SynthesisConfig in force right now (cached for a few seconds)."""
    global _cached, _cached_at
    now = time.monotonic()
    if _cached is not None and now - _cached_at < _CACHE_TTL_S:
        return _cached
    cfg = SynthesisConfig()
    try:
        raw = _redis().get(THRESHOLDS_KEY)
        if raw:
            values = json.loads(raw)
            cfg = SynthesisConfig(
                tau_low=float(values.get("otpThreshold", cfg.tau_low)),
                tau_high=float(values.get("blockThreshold", cfg.tau_high)),
                disagreement_variance_threshold=float(
                    values.get("disagreementThreshold",
                               cfg.disagreement_variance_threshold)),
            )
    except (redis.RedisError, ValueError, TypeError) as exc:
        logger.debug("Threshold read failed (%s) — using defaults", exc)
    _cached, _cached_at = cfg, now
    return cfg
