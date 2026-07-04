"""Tests for the paper §IV-E Synthesis Agent (agents.synthesis_agent +
synthesis_agent package: txn_type mapping, FastAPI endpoint, Postgres audit).

Endpoint/audit/mapping-coverage tests need the live local Postgres
(fraud_detection_global) and are skipped cleanly when it is missing; the
fusion-math and guard tests are pure and always run.
"""

from __future__ import annotations

import math
import re
import uuid
from pathlib import Path

import pytest

from agents.synthesis_agent import synthesise
from shared.schemas.risk import (
    AgentVerdict,
    DecisionAction,
    FraudPattern,
    Layer1Weights,
    Layer2Weights,
    TransactionType,
)
from synthesis_agent.txn_type_mapping import (
    DEFAULT_TXN_TYPE,
    RAW_TXN_TYPE_MAP,
    map_txn_type,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _db_available() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect("dbname=fraud_detection_global connect_timeout=2")
        conn.close()
        return True
    except Exception:
        return False


HAS_DB = _db_available()
needs_db = pytest.mark.skipif(not HAS_DB, reason="local Postgres unavailable")


def _verdict(risk: float, confidence: float = 1.0) -> AgentVerdict:
    return AgentVerdict(risk_score=risk, confidence=confidence, latency_ms=0)


# ---- Step 0B: Table I / II values are pinned --------------------------------
#
# The schema defaults were diffed against the proposal paper's Table I and
# Table II and DO NOT match it (reported, see README "Synthesis Agent" note):
# the paper keys Table I by HIGH_VALUE_P2P / QR_PAYMENT / REMITTANCE /
# POS_PAYMENT while the codebase uses the four TransactionType categories
# below, and every Table II row's values differ from the paper's. The schema
# is this codebase's source of truth; these pins exist so any FURTHER silent
# drift of the weights fails a test instead of quietly changing every fused
# score.

def test_table1_values_pinned() -> None:
    assert Layer1Weights().model_dump() == {
        "p2p_transfer": {"velocity": 0.35, "geo": 0.20, "graph": 0.20, "behavior": 0.25},
        "merchant_payment": {"velocity": 0.25, "geo": 0.25, "graph": 0.25, "behavior": 0.25},
        "atm_withdrawal": {"velocity": 0.30, "geo": 0.30, "graph": 0.25, "behavior": 0.15},
        "bill_payment": {"velocity": 0.20, "geo": 0.25, "graph": 0.20, "behavior": 0.35},
    }


def test_table2_values_pinned() -> None:
    assert Layer2Weights().model_dump() == {
        "rapid_transfers": {"velocity": 0.50, "geo": 0.10, "graph": 0.15, "behavior": 0.25},
        "fraud_ring": {"velocity": 0.15, "geo": 0.30, "graph": 0.40, "behavior": 0.15},
        "money_laundering": {"velocity": 0.25, "geo": 0.25, "graph": 0.25, "behavior": 0.25},
        "novel_pattern": {"velocity": 0.25, "geo": 0.25, "graph": 0.20, "behavior": 0.30},
    }


# ---- Step 0A: zero-weight guard ---------------------------------------------


def test_graph_verdict_fuses_with_table_weights() -> None:
    verdicts = {
        "velocity": _verdict(0.4),
        "geo": _verdict(0.4),
        "graph": _verdict(0.9),
    }
    result = synthesise(verdicts, TransactionType.P2P_TRANSFER)
    assert "graph" in result.agents_used
    assert result.weights_applied.graph > 0.0


def test_guard_silent_for_weighted_agents() -> None:
    verdicts = {
        "velocity": _verdict(0.4),
        "geo": _verdict(0.4),
        "behavior": _verdict(0.4),
    }
    result = synthesise(verdicts, TransactionType.P2P_TRANSFER)
    assert result.agents_used == ["velocity", "geo", "behavior"]


def test_no_agents_raises() -> None:
    with pytest.raises(ValueError, match="at least one"):
        synthesise({}, TransactionType.P2P_TRANSFER)


# ---- fusion renormalization over present agents ------------------------------


def test_renormalizes_over_two_present_agents() -> None:
    # p2p_transfer + geo dominant -> fraud_ring:
    #   blended velocity = 0.5*0.35+0.5*0.15 = 0.25 ; geo = 0.5*0.20+0.5*0.30 = 0.25
    verdicts = {"velocity": _verdict(0.2, 0.9), "geo": _verdict(0.6, 0.8)}
    result = synthesise(verdicts, TransactionType.P2P_TRANSFER)

    assert result.fraud_pattern == FraudPattern.FRAUD_RING
    assert result.agents_used == ["velocity", "geo"]
    assert result.weights_applied.behavior == 0.0  # absent agent audited as 0
    assert result.weights_applied.velocity == pytest.approx(0.25)
    assert result.weights_applied.geo == pytest.approx(0.25)

    expected = (0.25 * 0.9 * 0.2 + 0.25 * 0.8 * 0.6) / (0.25 * 0.9 + 0.25 * 0.8)
    assert result.final_score == pytest.approx(expected)


# ---- disagreement handling ---------------------------------------------------


def test_high_disagreement_forces_pass_to_otp() -> None:
    # risks 0.0 / 0.5: fused = 0.40*0.5/0.725 ≈ 0.276 (PASS band) but the
    # population variance 0.0625 >= 0.04 forces the challenge.
    verdicts = {"velocity": _verdict(0.0), "geo": _verdict(0.5)}
    result = synthesise(verdicts, TransactionType.P2P_TRANSFER)

    assert result.final_score < 0.30
    assert result.disagreement_score == pytest.approx(0.0625)
    assert result.decision == DecisionAction.OTP
    assert result.otp_forced_by_disagreement is True


def test_confident_block_not_downgraded_by_disagreement() -> None:
    verdicts = {"velocity": _verdict(0.3, 0.1), "geo": _verdict(1.0, 1.0)}
    result = synthesise(verdicts, TransactionType.P2P_TRANSFER)

    assert result.disagreement_score >= 0.04
    assert result.final_score > 0.70
    assert result.decision == DecisionAction.BLOCK
    assert result.otp_forced_by_disagreement is False


# ---- txn_type mapping ---------------------------------------------------------


def test_known_txn_types_map_explicitly() -> None:
    assert map_txn_type("ESEWA_P2P") == TransactionType.P2P_TRANSFER
    assert map_txn_type("RTGS") == TransactionType.P2P_TRANSFER
    assert map_txn_type("SWIFT_OUTWARD") == TransactionType.P2P_TRANSFER
    assert map_txn_type("KHALTI_QR") == TransactionType.MERCHANT_PAYMENT
    assert map_txn_type("CARD_POS") == TransactionType.MERCHANT_PAYMENT
    assert map_txn_type("ATM_WITHDRAWAL") == TransactionType.ATM_WITHDRAWAL
    assert map_txn_type("UTILITY_BILL") == TransactionType.BILL_PAYMENT
    assert map_txn_type("MOBILE_TOPUP") == TransactionType.BILL_PAYMENT


def test_unmapped_txn_type_defaults_loudly(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="synthesis-agent"):
        assert map_txn_type("CRYPTO_SWAP") == DEFAULT_TXN_TYPE
    assert any("CRYPTO_SWAP" in r.message for r in caplog.records)


@needs_db
def test_every_dataset_txn_type_is_mapped() -> None:
    import psycopg2
    conn = psycopg2.connect("dbname=fraud_detection_global")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT txn_type FROM transactions_raw")
            dataset_types = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()
    unmapped = dataset_types - set(RAW_TXN_TYPE_MAP)
    assert not unmapped, f"dataset txn_type values missing from RAW_TXN_TYPE_MAP: {unmapped}"


# ---- endpoint + synchronous audit write ---------------------------------------


@needs_db
def test_endpoint_fuses_and_persists_audit() -> None:
    from fastapi.testclient import TestClient
    from synthesis_agent.api import app

    txn_id = f"TEST-SYN-{uuid.uuid4().hex[:12]}"
    body = {
        "txn_id": txn_id,
        "txn_type": "ESEWA_P2P",
        "velocity": {"risk_score": 0.2, "confidence": 0.9},
        "geo": {"risk_score": 0.6, "confidence": 0.8},
        "graph": None,
        "behavior": None,
    }
    with TestClient(app) as client:
        resp = client.post("/agents/synthesis/evaluate", json=body)
    assert resp.status_code == 200, resp.text
    payload = resp.json()

    expected = (0.25 * 0.9 * 0.2 + 0.25 * 0.8 * 0.6) / (0.25 * 0.9 + 0.25 * 0.8)
    assert math.isclose(payload["final_score"], expected, rel_tol=1e-9)
    assert payload["agents_used"] == ["velocity", "geo"]
    assert payload["txn_type_mapped"] == "p2p_transfer"
    assert payload["fraud_pattern"] == "fraud_ring"
    assert payload["latency_ms"] > 0

    # The audit row must already be queryable — the write is synchronous.
    import psycopg2
    conn = psycopg2.connect("dbname=fraud_detection_global")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT txn_type_raw, txn_type_mapped, fraud_pattern, decision,
                          final_score, agents_used, otp_forced_by_disagreement
                   FROM synthesis_audit WHERE txn_id = %s""",
                (txn_id,),
            )
            rows = cur.fetchall()
            cur.execute("DELETE FROM synthesis_audit WHERE txn_id = %s", (txn_id,))
            conn.commit()
    finally:
        conn.close()
    assert len(rows) == 1
    raw, mapped, pattern, decision, score, agents_used, forced = rows[0]
    assert (raw, mapped, pattern) == ("ESEWA_P2P", "p2p_transfer", "fraud_ring")
    assert decision == payload["decision"]
    assert math.isclose(score, payload["final_score"], rel_tol=1e-9)
    assert agents_used == ["velocity", "geo"]
    assert forced == payload["otp_forced_by_disagreement"]


@needs_db
def test_endpoint_includes_graph_when_provided() -> None:
    from fastapi.testclient import TestClient
    from synthesis_agent.api import app

    body = {
        "txn_id": f"TEST-SYN-{uuid.uuid4().hex[:12]}",
        "txn_type": "ESEWA_P2P",
        "velocity": {"risk_score": 0.2, "confidence": 0.9},
        "geo": {"risk_score": 0.6, "confidence": 0.8},
        "graph": {"risk_score": 0.9, "confidence": 0.9},
    }
    with TestClient(app) as client:
        resp = client.post("/agents/synthesis/evaluate", json=body)
    assert resp.status_code == 200, resp.text
    assert "graph" in resp.json()["agents_used"]


# ---- Redis-free guarantee ------------------------------------------------------


def test_synthesis_package_is_redis_free() -> None:
    # Match real Redis usage only (imports / `redis.` attribute access), not the
    # substring "redis" inside prose like "redistributed" — a bare substring
    # scan yields false positives and does not prove anything about behavior.
    redis_use = re.compile(r"\bimport\s+redis\b|\bfrom\s+redis\b|\bredis\.\w")
    for name in ("api.py", "txn_type_mapping.py", "__init__.py"):
        source = (BACKEND_DIR / "synthesis_agent" / name).read_text().lower()
        assert not redis_use.search(source), name
    core = (BACKEND_DIR / "agents" / "synthesis_agent.py").read_text().lower()
    assert not redis_use.search(core)
