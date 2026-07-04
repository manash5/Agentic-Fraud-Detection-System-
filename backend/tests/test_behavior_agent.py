"""Tests for the paper §IV-C-3 Behavior Agent (agents.behavior_agent +
behavior_agent package).

Most tests need the live local Postgres (fraud_detection_global with the
reference tables loaded) and the exported model artifacts — they are skipped
cleanly when either is missing. Parity tests assert the input builders
reproduce the notebooks' own saved scores, which pins the entire feature
pipeline, not just shapes.
"""

from __future__ import annotations

import copy
import math

import asyncpg
import numpy as np
import pandas as pd
import pytest

from behavior_agent.config import BACKEND_DIR, load_config, pg_connect_kwargs

# ---- shared fixtures ------------------------------------------------------

RICH_TXN = None   # resolved once from the DB: latest txn of a >=50-txn account


def _db_available() -> bool:
    import psycopg2  # noqa: F401 — asyncpg needs a loop; do a cheap sync check
    try:
        import socket
        conn = psycopg2.connect("dbname=fraud_detection_global connect_timeout=2")
        conn.close()
        return True
    except Exception:
        return False


try:
    import psycopg2  # noqa: F401
    HAS_DB = _db_available()
except ImportError:
    HAS_DB = True  # let asyncpg fail loudly instead of silently skipping

needs_db = pytest.mark.skipif(not HAS_DB, reason="local Postgres unavailable")
needs_models = pytest.mark.skipif(
    not (BACKEND_DIR / "models" / "xgboost_behavior.json").exists(),
    reason="model artifacts not exported")


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def bundle(cfg):
    from behavior_agent.artifacts import load_bundle
    return load_bundle(cfg)


@pytest.fixture()
async def conn(cfg):
    c = await asyncpg.connect(**pg_connect_kwargs(cfg["database"]["dsn"]))
    yield c
    await c.close()


async def _nth_txn(conn, min_total: int, n: int) -> tuple[str, str]:
    """(account_id, txn_id) of the n-th txn of some account with >= min_total txns."""
    row = await conn.fetchrow(
        """
        WITH acct AS (
            SELECT account_id FROM transactions_raw
            GROUP BY account_id HAVING count(*) >= $1
            ORDER BY account_id LIMIT 1
        ), ranked AS (
            SELECT t.account_id, t.txn_id,
                   row_number() OVER (ORDER BY t.timestamp, t.txn_id) AS rn
            FROM transactions_raw t JOIN acct USING (account_id)
        )
        SELECT account_id, txn_id FROM ranked WHERE rn = $2
        """, min_total, n)
    assert row is not None
    return row["account_id"], row["txn_id"]


# ---- input builders (real txn_ids, parity against notebook outputs) -------


@needs_db
@needs_models
async def test_isolation_forest_builder_reproduces_training_scores(conn, bundle):
    from behavior_agent.input_builders import build_isolation_forest_input
    ref = pd.read_csv(BACKEND_DIR / "datasets_processed" /
                      "transactions_scored_isoforest.csv", nrows=2000).sample(
                          5, random_state=7)
    for _, row in ref.iterrows():
        vec = await build_isolation_forest_input(row["txn_id"], conn, bundle)
        assert vec.shape == (len(bundle.iso_features),)
        frame = pd.DataFrame(vec.reshape(1, -1), columns=bundle.iso_features)
        score = float(-bundle.iso_model.score_samples(frame)[0])
        assert math.isclose(score, row["anomaly_score"], abs_tol=1e-6), (
            f"{row['txn_id']}: built-features score {score} != notebook "
            f"{row['anomaly_score']}")


@needs_db
@needs_models
async def test_xgboost_builder_reproduces_validation_probas(conn, bundle):
    from behavior_agent.input_builders import build_xgboost_input
    ref = pd.read_csv(BACKEND_DIR / "datasets_processed" /
                      "val_scored_xgboost.csv", nrows=2000).sample(5, random_state=7)
    for _, row in ref.iterrows():
        vec = await build_xgboost_input(row["txn_id"], conn, bundle)
        assert vec.shape == (len(bundle.xgb_features),)
        proba = float(bundle.xgb_model.predict_proba(vec.reshape(1, -1))[0, 1])
        assert math.isclose(proba, row["fraud_proba"], abs_tol=1e-5), (
            f"{row['txn_id']}: built-features proba {proba} != notebook "
            f"{row['fraud_proba']}")


@needs_db
@needs_models
async def test_lstm_builder_shapes_and_left_padding(conn, bundle):
    from behavior_agent.input_builders import build_lstm_input
    n_seq = len(bundle.lstm_manifest["seq_features"])
    n_static = len(bundle.lstm_manifest["static_features"])
    seq_len = bundle.lstm_seq_len

    # Full window at the account's latest txn.
    acct, txn = await _nth_txn(conn, 50, 50)
    full = await build_lstm_input(acct, txn, conn, bundle)
    assert full.seq.shape == (seq_len, n_seq)
    assert full.static.shape == (n_static,)
    assert full.length == seq_len
    assert full.history_count == 50

    # Short history: 3rd-ever txn -> left-pad with zeros, real steps at the end.
    acct, txn = await _nth_txn(conn, 50, 3)
    short = await build_lstm_input(acct, txn, conn, bundle)
    assert short.length == 3
    assert short.history_count == 3
    assert np.allclose(short.seq[: seq_len - 3], 0.0), "padding must be zeros"
    assert np.abs(short.seq[seq_len - 3:]).sum() > 0, "real steps must be non-zero"


@needs_db
@needs_models
async def test_builders_fail_loudly_on_unknown_txn(conn, bundle):
    from behavior_agent.input_builders import (
        TxnNotFoundError,
        build_isolation_forest_input,
        build_lstm_input,
        build_xgboost_input,
    )
    with pytest.raises(TxnNotFoundError):
        await build_isolation_forest_input("TXN-DOES-NOT-EXIST", conn, bundle)
    with pytest.raises(TxnNotFoundError):
        await build_xgboost_input("TXN-DOES-NOT-EXIST", conn, bundle)
    with pytest.raises(TxnNotFoundError):
        await build_lstm_input("ACC-NOPE", "TXN-DOES-NOT-EXIST", conn, bundle)


# ---- ensemble math (pure, no DB) -------------------------------------------


def _score(name, contributed, calibrated=None):
    from behavior_agent.scorers import ModelScore
    return ModelScore(name=name, contributed=contributed,
                      raw_score=calibrated, calibrated_score=calibrated)


def test_blend_agreement_and_coverage(cfg):
    from behavior_agent.ensemble import blend
    # All three agree at 0.8 -> confidence = coverage(3) * 1.0 = 1.0
    v = blend([_score("xgboost", True, 0.8),
               _score("isolation_forest", True, 0.8),
               _score("lstm", True, 0.8)], history_count=60, cfg=cfg)
    assert v.weights_profile == "rich"
    assert math.isclose(v.risk_score, 0.8, abs_tol=1e-9)
    assert math.isclose(v.confidence, 1.0, abs_tol=1e-9)

    # Sharp disagreement lowers confidence; abstaining LSTM lowers coverage.
    v2 = blend([_score("xgboost", True, 0.9),
                _score("isolation_forest", True, 0.1),
                _score("lstm", False)], history_count=30, cfg=cfg)
    assert v2.weights_profile == "medium"
    assert v2.model_breakdown["lstm"]["effective_weight"] == 0.0
    assert v2.confidence < 0.25  # 0.75 coverage * 0.2 agreement
    # medium weights: 0.6*0.9 + 0.4*0.1 = 0.58
    assert math.isclose(v2.risk_score, 0.58, abs_tol=1e-9)

    # Single contributor (cold start) -> confidence = coverage(1)
    v3 = blend([_score("xgboost", False),
                _score("isolation_forest", True, 0.7),
                _score("lstm", False)], history_count=2, cfg=cfg)
    assert v3.weights_profile == "cold_start"
    assert math.isclose(v3.risk_score, 0.7, abs_tol=1e-9)
    assert math.isclose(
        v3.confidence, cfg["confidence"]["coverage_by_n_contributing"][1], abs_tol=1e-9)


def test_blend_raises_when_nothing_contributed(cfg):
    from behavior_agent.ensemble import blend
    with pytest.raises(ValueError):
        blend([_score("xgboost", False), _score("isolation_forest", False),
               _score("lstm", False)], history_count=5, cfg=cfg)


# ---- agent: cold-start vs rich history --------------------------------------


@needs_db
@needs_models
async def test_agent_cold_start_vs_rich_history(cfg, bundle):
    from agents.behavior_agent import BehaviorAgent
    agent = BehaviorAgent(cfg=cfg, bundle=bundle)
    await agent.connect()
    try:
        async with agent.pg_pool.acquire() as c:
            rich_acct, rich_txn = await _nth_txn(c, 50, 50)
            cold_acct, cold_txn = await _nth_txn(c, 50, 3)

        rich = await agent.evaluate(rich_acct, rich_txn)
        assert rich.weights_profile == "rich"
        assert all(rich.model_breakdown[m]["contributed"]
                   for m in ("xgboost", "isolation_forest", "lstm"))

        cold = await agent.evaluate(cold_acct, cold_txn)
        assert cold.weights_profile == "cold_start"
        assert cold.model_breakdown["lstm"]["contributed"] is False
        assert cold.model_breakdown["isolation_forest"]["contributed"] is True
        # LSTM abstention is the COMMON case; it must cost confidence.
        assert cold.confidence < rich.confidence

        for v in (rich, cold):
            assert 0.0 <= v.risk_score <= 1.0
            assert 0.0 <= v.confidence <= 1.0
    finally:
        await agent.close()


# ---- failure paths -----------------------------------------------------------


def test_model_missing_is_a_distinct_loud_error(cfg):
    from behavior_agent.artifacts import ModelMissingError, load_bundle
    broken = copy.deepcopy(cfg)
    broken["models"]["xgboost"]["model"] = "models/DOES_NOT_EXIST.json"
    with pytest.raises(ModelMissingError, match="XGBoost model"):
        load_bundle(broken)


@needs_models
async def test_agent_postgres_down_is_distinct_503_error(cfg, bundle):
    from agents.behavior_agent import BehaviorAgent, PostgresUnavailableError
    broken = copy.deepcopy(cfg)
    broken["database"]["dsn"] = "postgresql://nobody@127.0.0.1:1/void"
    agent = BehaviorAgent(cfg=broken, bundle=bundle)
    with pytest.raises(PostgresUnavailableError):
        await agent.connect()


# ---- endpoint integration -----------------------------------------------------


@needs_db
@needs_models
async def test_endpoint_shape_and_latency(cfg):
    import httpx

    from behavior_agent.api import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as client:
            health = await client.get("/agents/behavior/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"

            from agents.behavior_agent import BehaviorAgent  # noqa: F401
            from behavior_agent.api import agent
            async with agent.pg_pool.acquire() as c:
                acct, txn = await _nth_txn(c, 50, 50)

            payload = {"account_id": acct, "txn_id": txn}
            # warm-up request (prepared statements / first inference)
            r0 = await client.post("/agents/behavior/evaluate", json=payload)
            assert r0.status_code == 200
            # warm request must sit inside the paper's 100ms budget
            r = await client.post("/agents/behavior/evaluate", json=payload)
            assert r.status_code == 200
            body = r.json()
            assert set(body) >= {"risk_score", "confidence", "model_breakdown",
                                 "latency_ms", "history_count", "weights_profile"}
            assert 0.0 <= body["risk_score"] <= 1.0
            assert 0.0 <= body["confidence"] <= 1.0
            for m in ("xgboost", "isolation_forest", "lstm"):
                assert m in body["model_breakdown"]
            assert body["latency_ms"] < cfg["latency"]["budget_ms"], (
                f"warm request took {body['latency_ms']}ms > "
                f"{cfg['latency']['budget_ms']}ms budget")

            nf = await client.post("/agents/behavior/evaluate",
                                   json={"account_id": "ACC-X", "txn_id": "TXN-X"})
            assert nf.status_code == 404
