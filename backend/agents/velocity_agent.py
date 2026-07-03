"""Velocity Agent — paper §IV-C-1, Redis-only hot path.

Implements the Velocity Agent from "An Agentic Multi-Model Framework for
Real-Time Fraud Detection in Nepal's Digital Payment Ecosystem": five
signals over per-account Redis state, aggregated into a risk score plus a
cold-start-aware confidence score. Target: 1-2 ms per evaluation, a handful
of pipelined Redis round trips, no Postgres/Neo4j anywhere in this path.

Redis layout (all names, windows, TTLs and thresholds live in
``feature_config.yaml`` under ``velocity_agent:`` — no magic numbers here):

- ``user:{account_id}:count_2min`` / ``user:{account_id}:count_1hr``
  sorted sets, score = epoch-ms, member = txn_id. ZADD + ZREMRANGEBYSCORE +
  EXPIRE on every event; ZCARD then gives the live window count (which
  includes the current transaction).
- ``account_baseline:{account_id}`` hash — ``hist_txn_count_2min_mean``,
  ``hist_txn_count_1hr_mean``, ``hist_amount_avg``, ``hist_amount_std``,
  ``observation_count``. Refreshed daily by
  ``feature_engineering.nightly_baseline_job`` (same hash the batch layer
  already maintains; the hist_* fields are additive).
- ``user_type_dist:{account_id}`` hash — txn_type -> share of the account's
  trailing-30d history. Velocity-scoped only (``txn_type`` never needs to
  join the shared feature tables); also refreshed by the nightly job.

Signal 4 — balance integrity — is STUBBED (spec option (a)):
``transactions_raw`` has no ``balance_before``/``balance_after`` columns, so
:func:`balance_integrity_signal` returns ``None`` for missing inputs, the
agent passes ``None``, and the aggregation renormalizes over the remaining
four signals. The formula is already implemented behind the ``None`` guard,
so wiring it in later is a one-line change in :meth:`VelocityAgent.evaluate`
(pass the real fields). No synthetic balance data is fabricated.

Aggregation weights are a judgment call the paper leaves open (it only
fixes the Synthesis Agent's Table I weights); the rationale lives next to
the ``weights:`` block in feature_config.yaml.

Deliberately NOT folded in (paper fidelity): ``structuring_proximity``,
``night_burst_interaction``, ``dormancy_break`` and
``velocity_acceleration`` from the batch feature-engineering layer. Those
are dataset-driven enrichments, not part of the paper's five-signal
Velocity Agent definition.

``evaluate`` is async per the service contract but issues synchronous Redis
commands: at a 1-2 ms budget against a local Redis, event-loop task
switching would cost more than the sub-millisecond blocking calls it saves.
"""

from __future__ import annotations

import logging
import math
from datetime import timezone
from typing import Any, Mapping

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from feature_engineering.config import load_config
from feature_engineering.redis_client import RedisUnavailable
from shared.schemas.transaction import TransactionEvent

logger = logging.getLogger(__name__)


class RedisUnavailableError(RedisUnavailable):
    """Redis cannot serve the hot path.

    Raised instead of returning a default score: the fallback policy belongs
    to the Synthesis Agent / FastAPI orchestration layer, not to this agent.
    Subclasses the batch layer's :class:`RedisUnavailable` so existing
    except-clauses (e.g. the nightly job's) keep working.
    """


def _acfg(cfg: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return (cfg or load_config())["velocity_agent"]


def _clip01(x: float) -> float:
    return min(1.0, max(0.0, x))


def _count_key(acfg: Mapping[str, Any], account_id: str, window: str) -> str:
    return f"{acfg['key_prefixes']['user']}{account_id}:{window}"


# -- Redis state ------------------------------------------------------------


def record_transaction(
    account_id: str,
    txn_id: str,
    ts_ms: int,
    r: redis.Redis,
    *,
    cfg: Mapping[str, Any] | None = None,
) -> None:
    """ZADD the txn into both sliding windows, evict aged members, set TTLs.

    One pipelined round trip. TTL = window + slack, so idle accounts
    self-clean (70 minutes for the 1hr key with the default slack).
    """
    acfg = _acfg(cfg)
    slack = acfg["key_ttl_slack_s"]
    try:
        pipe = r.pipeline(transaction=True)
        for window, w_s in acfg["windows_s"].items():
            key = _count_key(acfg, account_id, window)
            pipe.zadd(key, {txn_id: ts_ms})
            pipe.zremrangebyscore(key, "-inf", f"({ts_ms - w_s * 1000}")
            pipe.expire(key, w_s + slack)
        pipe.execute()
    except redis.RedisError as exc:
        raise RedisUnavailableError(f"velocity window update failed: {exc}") from exc


def get_baseline(
    account_id: str, r: redis.Redis, *, cfg: Mapping[str, Any] | None = None
) -> dict[str, float]:
    """Cached nightly baseline stats; empty dict on cache miss (cold start)."""
    acfg = _acfg(cfg)
    key = acfg["key_prefixes"]["baseline"] + account_id
    try:
        raw = r.hgetall(key)
    except redis.RedisError as exc:
        raise RedisUnavailableError(f"baseline read failed: {exc}") from exc
    out: dict[str, float] = {}
    for k, v in raw.items():
        try:
            value = float(v)
        except (TypeError, ValueError):
            continue  # non-numeric fields (e.g. baseline_date)
        if math.isfinite(value):  # a cached "nan"/"inf" must read as missing
            out[k] = value
    return out


def get_type_dist(
    account_id: str, r: redis.Redis, *, cfg: Mapping[str, Any] | None = None
) -> dict[str, float]:
    """Cached txn_type -> historical share; empty dict on cache miss."""
    acfg = _acfg(cfg)
    key = acfg["key_prefixes"]["type_dist"] + account_id
    try:
        raw = r.hgetall(key)
    except redis.RedisError as exc:
        raise RedisUnavailableError(f"type distribution read failed: {exc}") from exc
    return {k: float(v) for k, v in raw.items()}


def write_baseline(
    account_id: str,
    stats: Mapping[str, Any],
    r: redis.Redis,
    *,
    cfg: Mapping[str, Any] | None = None,
) -> None:
    """Cache one account's baseline hash (nightly-job / test helper)."""
    full = cfg or load_config()
    key = _acfg(full)["key_prefixes"]["baseline"] + account_id
    try:
        pipe = r.pipeline(transaction=True)
        pipe.hset(key, mapping={k: str(v) for k, v in stats.items()})
        pipe.expire(key, full["redis"]["ttl"]["baseline_s"])
        pipe.execute()
    except redis.RedisError as exc:
        raise RedisUnavailableError(f"baseline write failed: {exc}") from exc


def write_type_dist(
    account_id: str,
    dist: Mapping[str, float],
    r: redis.Redis,
    *,
    cfg: Mapping[str, Any] | None = None,
) -> None:
    """Cache one account's txn_type distribution (nightly-job / test helper)."""
    acfg = _acfg(cfg)
    key = acfg["key_prefixes"]["type_dist"] + account_id
    try:
        pipe = r.pipeline(transaction=True)
        pipe.delete(key)  # drop types that no longer appear in the window
        pipe.hset(key, mapping={k: str(v) for k, v in dist.items()})
        pipe.expire(key, acfg["type_dist_ttl_s"])
        pipe.execute()
    except redis.RedisError as exc:
        raise RedisUnavailableError(f"type distribution write failed: {exc}") from exc


# -- the five signals (each independently testable) --------------------------


def txn_count_signal(
    account_id: str,
    r: redis.Redis,
    *,
    cfg: Mapping[str, Any] | None = None,
    baseline: Mapping[str, float] | None = None,
) -> float:
    """Signal 1: live 2min/1hr counts vs the account's historical means.

    ZCARD assumes :func:`record_transaction` already trimmed the windows,
    which is always true on the hot path (the current event is recorded
    first, so counts include it). Deviation per window:
    ``(live - hist_mean) / (hist_mean + 1)`` — the +1 smooths the near-zero
    means typical of short windows — normalized by the configured
    saturation ratio and clipped to [0,1]; the worse window wins. A missing
    baseline reads as mean 0 (pure burst detection); the confidence score,
    not this signal, carries cold-start uncertainty downstream.
    """
    acfg = _acfg(cfg)
    windows = list(acfg["windows_s"])
    try:
        pipe = r.pipeline(transaction=False)
        for window in windows:
            pipe.zcard(_count_key(acfg, account_id, window))
        counts = pipe.execute()
    except redis.RedisError as exc:
        raise RedisUnavailableError(f"velocity count read failed: {exc}") from exc
    if baseline is None:
        baseline = get_baseline(account_id, r, cfg=cfg)
    saturation = acfg["txn_count"]["saturation_ratio"]
    score = 0.0
    for window, live in zip(windows, counts):
        mean = baseline.get(f"hist_txn_{window}_mean", 0.0)
        deviation = (float(live) - mean) / (mean + 1.0)
        score = max(score, _clip01(deviation / saturation))
    return score


def amount_vs_baseline_signal(
    amount_npr: float,
    account_id: str,
    r: redis.Redis,
    *,
    cfg: Mapping[str, Any] | None = None,
    baseline: Mapping[str, float] | None = None,
) -> float:
    """Signal 2: smooth ratio of the amount to ``hist_amount_avg``.

    ``(ratio - 1) / (ratio_saturation - 1)`` clipped to [0,1]: at or below
    the historical average -> 0, at saturation x average -> 1. An account
    with no usable average (missing baseline, or avg below ``min_avg_npr``)
    is an explicit cold-start case and returns the configured neutral score
    — never a divide-by-zero.
    """
    acfg = _acfg(cfg)
    if baseline is None:
        baseline = get_baseline(account_id, r, cfg=cfg)
    amount_cfg = acfg["amount"]
    avg = baseline.get("hist_amount_avg", 0.0)
    if avg < amount_cfg["min_avg_npr"]:
        return amount_cfg["cold_start_score"]
    ratio = amount_npr / avg
    return _clip01((ratio - 1.0) / (amount_cfg["ratio_saturation"] - 1.0))


def amount_spike_signal(
    amount_npr: float,
    account_id: str,
    r: redis.Redis,
    *,
    cfg: Mapping[str, Any] | None = None,
    baseline: Mapping[str, float] | None = None,
) -> float:
    """Signal 3: the paper's explicit >=5x spike — distinct from signal 2.

    Boolean-then-scaled: 0 below ``multiplier`` x average, jumps to
    ``base_score`` exactly AT it, then ramps linearly to 1.0 at
    ``full_score_multiplier``. Cold-start accounts return 0.0 — a spike is
    undefined without a baseline, and the confidence score signals the
    uncertainty to the synthesizer.
    """
    acfg = _acfg(cfg)
    if baseline is None:
        baseline = get_baseline(account_id, r, cfg=cfg)
    avg = baseline.get("hist_amount_avg", 0.0)
    if avg < acfg["amount"]["min_avg_npr"]:
        return 0.0
    spike = acfg["spike"]
    ratio = amount_npr / avg
    if ratio < spike["multiplier"]:
        return 0.0
    ramp = (ratio - spike["multiplier"]) / (
        spike["full_score_multiplier"] - spike["multiplier"]
    )
    return _clip01(spike["base_score"] + (1.0 - spike["base_score"]) * ramp)


def balance_integrity_signal(
    balance_before: float | None,
    balance_after: float | None,
    declared_amount: float | None,
    *,
    cfg: Mapping[str, Any] | None = None,
) -> float | None:
    """Signal 4: balance integrity — PENDING A DATA SOURCE, returns None.

    ``transactions_raw`` has no balance_before/balance_after columns, so the
    hot path calls this with ``None`` and the aggregation excludes it (spec
    option (a)). The formula below is real, so once a balance feed exists,
    passing the actual fields in :meth:`VelocityAgent.evaluate` is the only
    change needed: relative mismatch between the declared amount and the
    observed balance delta, saturating at ``mismatch_full_score_ratio``.
    """
    if balance_before is None or balance_after is None or declared_amount is None:
        return None
    acfg = _acfg(cfg)
    mismatch = abs((balance_before - balance_after) - declared_amount)
    relative = mismatch / max(abs(declared_amount), 1.0)
    return _clip01(relative / acfg["balance_integrity"]["mismatch_full_score_ratio"])


def txn_type_mismatch_signal(
    declared_type: str | None,
    account_id: str,
    r: redis.Redis,
    *,
    cfg: Mapping[str, Any] | None = None,
    type_dist: Mapping[str, float] | None = None,
) -> float:
    """Signal 5: rarity of the declared txn_type for this account.

    ``1 - share/common_share`` clipped to [0,1]: a type making up at least
    ``common_share`` of the account's history scores 0, an unseen type
    scores 1. No type history yet (or no declared type on the event) is a
    cold start and returns the configured neutral mid-range score — never
    0 or 1.
    """
    acfg = _acfg(cfg)
    mismatch_cfg = acfg["type_mismatch"]
    if type_dist is None:
        type_dist = get_type_dist(account_id, r, cfg=cfg)
    if not type_dist or not declared_type:
        return mismatch_cfg["cold_start_score"]
    share = type_dist.get(declared_type, 0.0)
    return _clip01(1.0 - share / mismatch_cfg["common_share"])


# -- confidence and aggregation ----------------------------------------------


def confidence_score(observation_count: int, threshold: int = 50) -> float:
    """Paper: 'For users below a configurable observation threshold,
    confidence is reduced to signal cold-start uncertainty to the downstream
    synthesizer.' Smooth ramp, not a hard cutoff, so the synthesizer sees a
    gradient rather than a step function. The production threshold lives in
    feature_config.yaml (velocity_agent.confidence.observation_threshold);
    the default here only mirrors it for standalone use.
    """
    if threshold <= 0:
        return 1.0
    return min(1.0, max(0, observation_count) / threshold)


def aggregate_risk(
    signals: Mapping[str, float | None], *, cfg: Mapping[str, Any] | None = None
) -> float:
    """Weighted mean over the signals that produced a score.

    ``None`` (the stubbed balance check, until a balance feed exists) is
    excluded and the remaining weights renormalized, so the stub never
    silently drags the score toward 0. Weights and their rationale live in
    feature_config.yaml.
    """
    weights = _acfg(cfg)["weights"]
    available = {name: s for name, s in signals.items() if s is not None}
    total_weight = sum(weights[name] for name in available)
    if total_weight <= 0:
        return 0.0
    weighted = sum(weights[name] * s for name, s in available.items())
    return _clip01(weighted / total_weight)


# -- the agent ---------------------------------------------------------------


class VelocityAgent:
    """Paper §IV-C-1 Velocity Agent over Redis sliding windows."""

    def __init__(
        self,
        client: redis.Redis | None = None,
        cfg: dict[str, Any] | None = None,
    ) -> None:
        """``client`` may be injected (tests); otherwise built from config."""
        self.cfg = cfg or load_config()
        self.acfg = self.cfg["velocity_agent"]
        rc = self.cfg["redis"]
        self.client = client or redis.Redis(
            host=rc["host"],
            port=rc["port"],
            socket_timeout=rc["socket_timeout_s"],
            socket_connect_timeout=rc["socket_timeout_s"],
            decode_responses=True,
            # redis-py retries connection errors 3x with backoff by default
            # (seconds, not ms). The hot path must surface
            # RedisUnavailableError within the socket timeout instead.
            retry=Retry(NoBackoff(), retries=0),
        )

    async def evaluate(self, event: TransactionEvent) -> tuple[float, float]:
        """Returns (risk_score, confidence_score), both in [0,1].

        Redis exclusively — no Postgres or Neo4j calls in this method; four
        pipelined round trips keep it inside the 1-2 ms budget. Raises
        :class:`RedisUnavailableError` if Redis cannot answer, rather than
        silently returning a default score: the fallback policy belongs to
        the Synthesis Agent / FastAPI orchestration layer.
        """
        account_id = event.user_id
        ts = event.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts_ms = int(ts.timestamp() * 1000)

        record_transaction(
            account_id, event.transaction_id, ts_ms, self.client, cfg=self.cfg
        )
        baseline = get_baseline(account_id, self.client, cfg=self.cfg)

        signals: dict[str, float | None] = {
            "txn_count": txn_count_signal(
                account_id, self.client, cfg=self.cfg, baseline=baseline
            ),
            "amount_vs_baseline": amount_vs_baseline_signal(
                event.amount, account_id, self.client, cfg=self.cfg, baseline=baseline
            ),
            "amount_spike": amount_spike_signal(
                event.amount, account_id, self.client, cfg=self.cfg, baseline=baseline
            ),
            # Stubbed until a balance data source exists — always None today;
            # wire in by passing the event's real balance fields here.
            "balance_integrity": balance_integrity_signal(
                None, None, event.amount, cfg=self.cfg
            ),
            "txn_type_mismatch": txn_type_mismatch_signal(
                event.txn_type, account_id, self.client, cfg=self.cfg
            ),
        }
        risk = aggregate_risk(signals, cfg=self.cfg)
        confidence = confidence_score(
            int(baseline.get("observation_count", 0)),
            threshold=self.acfg["confidence"]["observation_threshold"],
        )
        return risk, confidence
