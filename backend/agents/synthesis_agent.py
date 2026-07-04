"""Synthesis Agent — paper §IV-E, the two-layer confidence-weighted fusion.

This is the "Synthesis agent" box in the proposal's Fig. 1. It does not talk
to any datastore and runs no model of its own: it consumes the risk/confidence
verdicts already produced by the other agents and fuses them into one final
score, a fraud-pattern label, a disagreement statistic, and a PASS/OTP/BLOCK
decision.

Pipeline (exactly the flowchart):

    agent verdicts ─▶ 1. classify fraud pattern (Layer 2 selector)
                     2. Layer 1 weights   w¹(transaction_type)   [Table I]
                     3. Layer 2 weights   w²(fraud_pattern)      [Table II]
                     4. blend 50/50       wᵢ = 0.5·w¹ᵢ + 0.5·w²ᵢ   (Eq. 1)
                     5. fuse              S = Σ wᵢcᵢrᵢ / Σ wᵢcᵢ     (Eq. 2)
                     6. disagreement      var({rᵢ}) ≥ τ  ⇒ force OTP
                     7. decision          S<0.30 PASS · ≤0.70 OTP · >0.70 BLOCK

Four agents, behavior optional
------------------------------
The paper fuses three agents (velocity, geo, behavior). This codebase splits
the paper's Geo Agent into ``geo`` (travel + device) and ``graph`` (the Neo4j
network checks), so the canonical agent set here is::

    velocity · geo · graph · behavior

The Behavior Agent (XGBoost + Isolation Forest + LSTM) is **not built yet**.
:func:`synthesise` therefore accepts whichever agents are present and
renormalizes over them: with behavior absent it fuses the three that exist,
and the moment a behavior verdict is passed in, it is folded into both the
weighting and the fusion with no further changes. That is the whole point of
Eq. 2's denominator — a missing agent contributes nothing and its weight mass
is redistributed proportionally across the agents that did report.

Everything here is a pure function of its inputs (no I/O, no globals mutated),
so it is trivially unit-testable and safe to call from the request path.
"""

from __future__ import annotations

from typing import Mapping

from shared.schemas.risk import (
    AgentVerdict,
    AgentWeights,
    BlendedWeights,
    DecisionAction,
    FraudPattern,
    Layer1Weights,
    Layer2Weights,
    SynthesisResult,
    TransactionType,
)

# Canonical agent order. ``graph`` sits between geo and behavior; behavior is
# last because it is the one not yet wired in.
AGENTS: tuple[str, ...] = ("velocity", "geo", "graph", "behavior")

# -- tunable thresholds ------------------------------------------------------
# Kept here (not in feature_config.yaml) because they are the paper's own
# constants and carry no per-deployment secrets. Override via SynthesisConfig
# if a deployment needs to recalibrate (paper §V-C).

# A risk score at or above this counts as "elevated" for pattern detection.
ELEVATED_THRESHOLD = 0.50
# Money laundering = several agents elevated AND tightly clustered (coordinated
# activity lighting up multiple signals at once). Max-minus-min risk spread
# must stay at or below this for the "clustered" test.
MONEY_LAUNDERING_MAX_SPREAD = 0.20
# Disagreement check (paper §IV-E-4): population variance of the risk scores.
# At/above this the verdict is forced to OTP regardless of the fused score, so
# a single confidently-wrong agent cannot drive a PASS or a BLOCK on its own.
DISAGREEMENT_VARIANCE_THRESHOLD = 0.04
# Decision layer (paper Table III).
TAU_LOW = 0.30
TAU_HIGH = 0.70


class SynthesisConfig:
    """Overridable copy of the module constants (paper §V-C recalibration)."""

    def __init__(
        self,
        *,
        elevated_threshold: float = ELEVATED_THRESHOLD,
        money_laundering_max_spread: float = MONEY_LAUNDERING_MAX_SPREAD,
        disagreement_variance_threshold: float = DISAGREEMENT_VARIANCE_THRESHOLD,
        tau_low: float = TAU_LOW,
        tau_high: float = TAU_HIGH,
    ) -> None:
        self.elevated_threshold = elevated_threshold
        self.money_laundering_max_spread = money_laundering_max_spread
        self.disagreement_variance_threshold = disagreement_variance_threshold
        self.tau_low = tau_low
        self.tau_high = tau_high


DEFAULT_CONFIG = SynthesisConfig()

# Weight tables materialized once from the schema defaults, so the schema stays
# the single source of truth for Tables I & II.
_LAYER1: dict[str, dict[str, float]] = {
    k: v for k, v in Layer1Weights().model_dump().items()
}
_LAYER2: dict[str, dict[str, float]] = {
    k: v for k, v in Layer2Weights().model_dump().items()
}


def _clip01(x: float) -> float:
    return min(1.0, max(0.0, x))


# -- step 1: fraud-pattern classification ------------------------------------


def classify_pattern(
    risks: Mapping[str, float], *, cfg: SynthesisConfig = DEFAULT_CONFIG
) -> FraudPattern:
    """Pick the Layer 2 row by which agents are shouting (paper §IV-E-2).

    Rules, in priority order:

    * three or more agents elevated and tightly clustered → **money laundering**
      (coordinated activity firing several signals at once);
    * graph or geo is the loudest → **fraud ring** (network structure);
    * velocity is the loudest → **rapid transfers**;
    * otherwise (behavior loudest, or nothing clearly dominant) → **novel
      pattern**.

    Only the agents actually present are considered, so this behaves sensibly
    before the Behavior Agent exists — it simply never resolves to
    ``novel_pattern`` via a behavior peak until behavior is wired in.
    """
    if not risks:
        return FraudPattern.NOVEL_PATTERN

    values = list(risks.values())
    spread = max(values) - min(values) if len(values) > 1 else 0.0
    elevated = [a for a, r in risks.items() if r >= cfg.elevated_threshold]

    if len(elevated) >= 3 and spread <= cfg.money_laundering_max_spread:
        return FraudPattern.MONEY_LAUNDERING

    dominant = max(risks, key=lambda a: risks[a])
    if dominant in ("graph", "geo"):
        return FraudPattern.FRAUD_RING
    if dominant == "velocity":
        return FraudPattern.RAPID_TRANSFERS
    return FraudPattern.NOVEL_PATTERN


# -- steps 2–4: two-layer weight selection and blend -------------------------


def layer1_weights(transaction_type: TransactionType | str) -> dict[str, float]:
    """Table I row for the transaction type (defaults to P2P if unknown)."""
    key = transaction_type.value if isinstance(transaction_type, TransactionType) else str(transaction_type)
    return dict(_LAYER1.get(key, _LAYER1[TransactionType.P2P_TRANSFER.value]))


def layer2_weights(pattern: FraudPattern | str) -> dict[str, float]:
    """Table II row for the detected fraud pattern."""
    key = pattern.value if isinstance(pattern, FraudPattern) else str(pattern)
    return dict(_LAYER2.get(key, _LAYER2[FraudPattern.NOVEL_PATTERN.value]))


def blend_weights(
    w1: Mapping[str, float], w2: Mapping[str, float]
) -> dict[str, float]:
    """Eq. 1 — equal 50/50 blend of the two layers, per agent."""
    return {a: 0.5 * w1.get(a, 0.0) + 0.5 * w2.get(a, 0.0) for a in AGENTS}


# -- step 5: confidence-weighted fusion --------------------------------------


def fuse(
    verdicts: Mapping[str, AgentVerdict], blended: Mapping[str, float]
) -> float:
    """Eq. 2 — S = Σ wᵢ·cᵢ·rᵢ / Σ wᵢ·cᵢ over the agents present.

    An agent reporting high risk with low confidence contributes
    proportionately less than one reporting the same risk with high
    confidence. If every present agent has zero effective weight·confidence
    (degenerate input), the score is 0.0.
    """
    numerator = 0.0
    denominator = 0.0
    for agent, verdict in verdicts.items():
        w = blended.get(agent, 0.0)
        wc = w * verdict.confidence
        numerator += wc * verdict.risk_score
        denominator += wc
    if denominator <= 0.0:
        return 0.0
    return _clip01(numerator / denominator)


# -- step 6: disagreement check ----------------------------------------------


def disagreement_score(risks: Mapping[str, float]) -> float:
    """Population variance of the present risk scores (paper §IV-E-4)."""
    values = list(risks.values())
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


# -- step 7: decision mapping ------------------------------------------------


def decide(
    final_score: float,
    disagreement: float,
    *,
    cfg: SynthesisConfig = DEFAULT_CONFIG,
) -> tuple[DecisionAction, bool]:
    """Table III thresholds, with the disagreement override.

    Returns ``(decision, otp_forced_by_disagreement)``. When agents disagree
    past the variance threshold the verdict is nudged to OTP — but only if the
    score-based verdict was not already OTP, and never downgrading a BLOCK
    (a confident BLOCK stands; the safe move on genuine disagreement is to
    challenge rather than pass).
    """
    if final_score < cfg.tau_low:
        base = DecisionAction.PASS
    elif final_score <= cfg.tau_high:
        base = DecisionAction.OTP
    else:
        base = DecisionAction.BLOCK

    if disagreement >= cfg.disagreement_variance_threshold and base == DecisionAction.PASS:
        return DecisionAction.OTP, True
    return base, False


# -- orchestration: the whole agent in one call ------------------------------


def synthesise(
    verdicts: Mapping[str, AgentVerdict],
    transaction_type: TransactionType | str,
    *,
    cfg: SynthesisConfig = DEFAULT_CONFIG,
) -> SynthesisResult:
    """Run the full §IV-E pipeline over the agent verdicts that are present.

    ``verdicts`` maps agent name (a subset of :data:`AGENTS`) to its
    :class:`AgentVerdict`. Absent agents — behavior today — are simply omitted;
    the two-layer weighting and Eq. 2 renormalize over whoever reported.

    Raises ``ValueError`` if no agents are supplied (there is nothing to fuse),
    or if a reporting agent has zero weight in BOTH Table I and Table II — that
    verdict would otherwise be silently discarded by Eq. 2 while still being
    listed in ``agents_used``.
    """
    present = {a: v for a, v in verdicts.items() if a in AGENTS}
    if not present:
        raise ValueError(
            "synthesise() requires at least one agent verdict "
            f"(known agents: {', '.join(AGENTS)})"
        )

    risks = {a: v.risk_score for a, v in present.items()}

    pattern = classify_pattern(risks, cfg=cfg)
    w1 = layer1_weights(transaction_type)
    w2 = layer2_weights(pattern)
    blended = blend_weights(w1, w2)

    # Guard: an agent that reports a verdict but carries zero weight in both
    # layers would be silently dropped by Eq. 2 while still being listed in
    # agents_used.
    unweighted = sorted(a for a in present if blended.get(a, 0.0) == 0.0)
    if unweighted:
        raise ValueError(
            f"agent(s) {', '.join(unweighted)} reported a verdict but have "
            "zero blended weight across Table I and Table II — add them to "
            "Layer1Weights/Layer2Weights in shared/schemas/risk.py before "
            "wiring them into synthesis, otherwise their verdicts would be "
            "silently ignored"
        )

    final_score = fuse(present, blended)
    disagreement = disagreement_score(risks)
    decision, forced = decide(final_score, disagreement, cfg=cfg)

    # Report blended weights only for the agents that actually contributed;
    # absent agents show 0.0 so the audit record is unambiguous.
    blended_out = {a: (blended[a] if a in present else 0.0) for a in AGENTS}

    return SynthesisResult(
        final_score=final_score,
        weights_applied=BlendedWeights(**blended_out),
        fraud_pattern=pattern,
        disagreement_score=disagreement,
        decision=decision,
        layer1_weights=AgentWeights(**w1),
        layer2_weights=AgentWeights(**w2),
        agents_used=[a for a in AGENTS if a in present],
        otp_forced_by_disagreement=forced,
    )
