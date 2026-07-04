"""Behavior Agent — ensemble of the three trained behavior models
(XGBoost + Isolation Forest + two-branch LSTM) behind one evaluate() call.

Combines all available model outputs through an ensemble blend and emits a
single consolidated risk score together with a confidence score reflecting
which models contributed (paper Section IV-C-3). Expected latency ~100ms with
all models preloaded.

Layout: this module is the agent entry point (same shape as geo_agent /
velocity_agent); the heavy lifting lives in the ``behavior_agent`` package —
per-model input builders, scorers, calibration and the blend formula.

Failure semantics:
  - a missing model artifact  -> ModelMissingError   (endpoint: 503, distinct detail)
  - Postgres unreachable      -> PostgresUnavailableError (endpoint: 503)
  - unknown txn / account     -> TxnNotFoundError    (endpoint: 404)
  - a single model missing its input rows -> that model is marked
    contributed=False with the error recorded in the breakdown; the blend
    proceeds on the remaining models and confidence drops accordingly. If NO
    model can score, AllModelsFailedError is raised (endpoint: 422).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import asyncpg

from behavior_agent.artifacts import ModelBundle, ModelMissingError, load_bundle
from behavior_agent.config import load_config, pg_connect_kwargs
from behavior_agent.ensemble import BehaviorVerdict, blend
from behavior_agent.input_builders import (
    MissingInputError,
    TxnNotFoundError,
    account_history_count,
)
from behavior_agent.scorers import (
    ModelScore,
    score_isolation_forest,
    score_lstm,
    score_xgboost,
)

logger = logging.getLogger("behavior-agent")

__all__ = ["BehaviorAgent", "AllModelsFailedError", "PostgresUnavailableError",
           "ModelMissingError", "TxnNotFoundError", "MissingInputError"]


class PostgresUnavailableError(Exception):
    """Postgres could not be reached (distinct from a missing model)."""


class AllModelsFailedError(Exception):
    """Every model failed to build its input — nothing to blend."""


class BehaviorAgent:
    """Preloads all models once; each evaluate() is DB reads + inference."""

    def __init__(self, cfg: dict[str, Any] | None = None,
                 pg_pool: asyncpg.Pool | None = None,
                 bundle: ModelBundle | None = None):
        """Pool/bundle may be injected (tests); otherwise built in connect()."""
        self.cfg = cfg or load_config()
        self.pg_pool = pg_pool
        self.bundle = bundle

    async def connect(self) -> None:
        """Load model artifacts and open the asyncpg pool.

        ModelMissingError propagates as-is — the caller must be able to tell
        "model not exported" apart from "database down".
        """
        if self.bundle is None:
            self.bundle = load_bundle(self.cfg)  # raises ModelMissingError
        if self.pg_pool is None:
            db = self.cfg["database"]
            try:
                self.pg_pool = await asyncpg.create_pool(
                    min_size=db["pool_min_size"], max_size=db["pool_max_size"],
                    **pg_connect_kwargs(db["dsn"]))
            except (asyncpg.PostgresError, OSError) as exc:
                raise PostgresUnavailableError(str(exc)) from exc

    async def close(self) -> None:
        if self.pg_pool is not None:
            await self.pg_pool.close()
            self.pg_pool = None

    async def evaluate(self, account_id: str, txn_id: str) -> BehaviorVerdict:
        if self.bundle is None or self.pg_pool is None:
            raise PostgresUnavailableError("agent not connected — call connect() first")
        # One asyncpg connection can't run concurrent queries, so each scorer
        # borrows its own pooled connection and the three run in parallel.
        async def _with_conn(fn, *args):
            async with self.pg_pool.acquire() as conn:
                return await fn(*args, conn, self.bundle)

        try:
            async with self.pg_pool.acquire() as conn:
                # History drives both LSTM gating and the blend weights.
                history = await account_history_count(account_id, txn_id, conn)

            async def _lstm(a: str, t: str, conn, bundle):
                return await score_lstm(
                    a, t, conn, bundle, history_count=history,
                    min_history=self.cfg["history"]["lstm_min_history"])

            results = await asyncio.gather(
                _with_conn(score_xgboost, txn_id),
                _with_conn(score_isolation_forest, txn_id),
                _with_conn(_lstm, account_id, txn_id),
                return_exceptions=True,
            )
        except (asyncpg.PostgresError, OSError) as exc:
            raise PostgresUnavailableError(str(exc)) from exc

        scores: list[ModelScore] = []
        for name, res in zip(("xgboost", "isolation_forest", "lstm"), results):
            if isinstance(res, ModelScore):
                scores.append(res)
            elif isinstance(res, MissingInputError):
                # One model's inputs are unbuildable for this txn: it did not
                # contribute; the reason is surfaced, never zero-filled over.
                logger.warning("behavior: %s did not fire for %s: %s", name, txn_id, res)
                scores.append(ModelScore(name=name, contributed=False,
                                         detail={"error": str(res)}))
            elif isinstance(res, (asyncpg.PostgresError, OSError)):
                raise PostgresUnavailableError(str(res)) from res
            else:
                raise res  # programming error — do not mask it

        if not any(s.contributed for s in scores):
            raise AllModelsFailedError(
                f"txn {txn_id}: no model could score — "
                + "; ".join(f"{s.name}: {s.detail.get('error', 'abstained')}"
                            for s in scores))

        return blend(scores, history_count=history, cfg=self.cfg)

    async def evaluate_timed(self, account_id: str,
                             txn_id: str) -> tuple[BehaviorVerdict, float]:
        """evaluate() plus wall latency in ms, warning over the paper budget."""
        started = time.monotonic()
        verdict = await self.evaluate(account_id, txn_id)
        latency_ms = (time.monotonic() - started) * 1000
        budget = self.cfg["latency"]["budget_ms"]
        if latency_ms > budget:
            logger.warning("behavior: %s took %.1fms (> %dms budget)",
                           txn_id, latency_ms, budget)
        return verdict, latency_ms
