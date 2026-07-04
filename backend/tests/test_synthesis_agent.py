"""Tests for the paper §IV-E Synthesis Agent (agents.synthesis_agent).

Pure functions, no I/O — everything runs against hand-built AgentVerdicts.
Covers the fusion formula (Eq. 2), the two-layer weight blend (Eq. 1), the
pattern classifier, the disagreement override, the decision bands, and the
behavior-optional / graph-included contract the project requires.
"""

from __future__ import annotations

import math

import pytest

from agents.synthesis_agent import (
    AGENTS,
    blend_weights,
    classify_pattern,
    decide,
    disagreement_score,
    fuse,
    layer1_weights,
    layer2_weights,
    synthesise,
)
from shared.schemas.risk import (
    AgentVerdict,
    DecisionAction,
    FraudPattern,
    TransactionType,
)


def v(risk: float, conf: float = 1.0) -> AgentVerdict:
    return AgentVerdict(risk_score=risk, confidence=conf, latency_ms=1)


# -- Eq. 2 fusion ------------------------------------------------------------


def test_fuse_equal_weight_full_confidence_is_weighted_mean():
    verdicts = {"velocity": v(0.9), "geo": v(0.1)}
    blended = {"velocity": 0.5, "geo": 0.5}
    # (0.5·1·0.9 + 0.5·1·0.1) / (0.5·1 + 0.5·1) = 0.5
    assert fuse(verdicts, blended) == pytest.approx(0.5)


def test_fuse_low_confidence_agent_contributes_less():
    # Same risks, but the high-risk agent is unsure → score pulled toward the
    # confident low-risk agent.
    verdicts = {"velocity": v(0.9, conf=0.2), "geo": v(0.1, conf=1.0)}
    blended = {"velocity": 0.5, "geo": 0.5}
    # num = 0.5·0.2·0.9 + 0.5·1·0.1 = 0.09 + 0.05 = 0.14
    # den = 0.5·0.2 + 0.5·1 = 0.1 + 0.5 = 0.6
    assert fuse(verdicts, blended) == pytest.approx(0.14 / 0.6)


def test_fuse_zero_effective_weight_is_zero():
    verdicts = {"velocity": v(0.9, conf=0.0)}
    assert fuse(verdicts, {"velocity": 0.5}) == 0.0


# -- Eq. 1 blend -------------------------------------------------------------


def test_blend_is_5050_over_all_agents():
    w1 = {"velocity": 0.4, "geo": 0.2, "graph": 0.1, "behavior": 0.3}
    w2 = {"velocity": 0.1, "geo": 0.25, "graph": 0.45, "behavior": 0.2}
    blended = blend_weights(w1, w2)
    assert blended["velocity"] == pytest.approx(0.25)
    assert blended["graph"] == pytest.approx(0.275)
    assert set(blended) == set(AGENTS)


def test_weight_tables_cover_every_type_and_pattern():
    for t in TransactionType:
        row = layer1_weights(t)
        assert set(row) >= {"velocity", "geo", "graph", "behavior"}
        assert sum(row.values()) == pytest.approx(1.0, abs=1e-9)
    for p in FraudPattern:
        row = layer2_weights(p)
        assert sum(row.values()) == pytest.approx(1.0, abs=1e-9)


# -- pattern classifier ------------------------------------------------------


def test_velocity_dominant_is_rapid_transfers():
    risks = {"velocity": 0.9, "geo": 0.1, "graph": 0.1}
    assert classify_pattern(risks) is FraudPattern.RAPID_TRANSFERS


def test_graph_dominant_is_fraud_ring():
    risks = {"velocity": 0.2, "geo": 0.3, "graph": 0.9}
    assert classify_pattern(risks) is FraudPattern.FRAUD_RING


def test_all_elevated_and_clustered_is_money_laundering():
    risks = {"velocity": 0.7, "geo": 0.75, "graph": 0.8}
    assert classify_pattern(risks) is FraudPattern.MONEY_LAUNDERING


def test_behavior_dominant_is_novel_pattern():
    risks = {"velocity": 0.2, "geo": 0.1, "graph": 0.1, "behavior": 0.8}
    assert classify_pattern(risks) is FraudPattern.NOVEL_PATTERN


# -- disagreement ------------------------------------------------------------


def test_disagreement_variance():
    # var of {0.9, 0.1} about mean 0.5 = 0.16
    assert disagreement_score({"a": 0.9, "b": 0.1}) == pytest.approx(0.16)
    assert disagreement_score({"a": 0.5}) == 0.0


def test_high_disagreement_forces_otp_out_of_pass():
    # Low fused score would PASS, but wide spread forces OTP.
    decision, forced = decide(final_score=0.1, disagreement=0.16)
    assert decision is DecisionAction.OTP and forced is True


def test_disagreement_never_downgrades_block():
    decision, forced = decide(final_score=0.95, disagreement=0.16)
    assert decision is DecisionAction.BLOCK and forced is False


@pytest.mark.parametrize(
    "score,expected",
    [(0.0, DecisionAction.PASS), (0.29, DecisionAction.PASS),
     (0.30, DecisionAction.OTP), (0.70, DecisionAction.OTP),
     (0.71, DecisionAction.BLOCK), (1.0, DecisionAction.BLOCK)],
)
def test_decision_bands(score, expected):
    decision, _ = decide(final_score=score, disagreement=0.0)
    assert decision is expected


# -- end-to-end synthesise ---------------------------------------------------


def test_synthesise_without_behavior_uses_three_agents():
    """Behavior omitted today → fused over velocity/geo/graph, renormalized."""
    verdicts = {"velocity": v(0.6), "geo": v(0.4), "graph": v(0.5)}
    result = synthesise(verdicts, TransactionType.P2P_TRANSFER)
    assert result.agents_used == ["velocity", "geo", "graph"]
    assert result.weights_applied.behavior == 0.0  # absent agent → 0
    assert 0.0 <= result.final_score <= 1.0
    assert isinstance(result.fraud_pattern, FraudPattern)


def test_synthesise_with_behavior_plugs_in():
    """Adding a behavior verdict folds it into weighting and fusion."""
    verdicts = {
        "velocity": v(0.2), "geo": v(0.2), "graph": v(0.2), "behavior": v(0.9),
    }
    result = synthesise(verdicts, TransactionType.BILL_PAYMENT)
    assert "behavior" in result.agents_used
    assert result.weights_applied.behavior > 0.0
    # bill_payment + novel_pattern both weight behavior heavily, and behavior
    # is the loud one → score should be pulled up meaningfully.
    assert result.final_score > 0.4


def test_synthesise_requires_at_least_one_agent():
    with pytest.raises(ValueError):
        synthesise({}, TransactionType.P2P_TRANSFER)


def test_synthesise_result_is_audit_complete():
    verdicts = {"velocity": v(0.6), "geo": v(0.4), "graph": v(0.5)}
    r = synthesise(verdicts, TransactionType.P2P_TRANSFER)
    # every field the NRB audit trail needs is populated
    assert r.layer1_weights is not None and r.layer2_weights is not None
    assert r.decision in DecisionAction
    assert math.isfinite(r.disagreement_score)
