"""Pure mapping functions: pipeline/DB shapes -> the frontend's types/banking.ts.

The frontend keeps its existing TypeScript interfaces (Transaction,
FraudAnalysis, AgentResult, ModelVerdict); everything here shapes backend data
into those camelCase contracts so no React component needs to change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# -- txn-type maps ---------------------------------------------------------------

# Mirror of frontend lib/trackb.ts:txnTypeForTransfer().
def txn_type_for_transfer(destination: str, bank_or_wallet: str,
                          mode: str | None) -> str:
    if mode == "bill":
        return "UTILITY_BILL"
    if mode == "topup":
        return "MOBILE_TOPUP"
    if mode == "qr":
        return "KHALTI_QR"
    if destination == "wallet":
        return "KHALTI_QR" if "khalti" in (bank_or_wallet or "").lower() else "ESEWA_P2P"
    if destination == "other_bank":
        return "RTGS"
    return "ESEWA_P2P"


# Raw dataset txn_type -> frontend display TransactionType.
RAW_TO_FRONTEND_TYPE: dict[str, str] = {
    "ESEWA_P2P": "transfer",
    "RTGS": "transfer",
    "SWIFT_OUTWARD": "transfer",
    "KHALTI_QR": "qr_payment",
    "CARD_POS": "payment",
    "ATM_WITHDRAWAL": "withdrawal",
    "UTILITY_BILL": "payment",
    "MOBILE_TOPUP": "topup",
}

RAW_CHANNEL_TO_FRONTEND: dict[str, str] = {
    "MOBILE_APP": "mobile",
    "WEB": "web",
    "ATM": "atm",
    "BRANCH": "branch",
}

RAW_AUTH_TO_FRONTEND: dict[str, str] = {
    "MPIN": "PIN",
    "CARD_PIN": "PIN",
    "BIOMETRIC": "BIOMETRIC",
    "OTP_SMS": "OTP",
    "OTP_EMAIL": "OTP",
}


def flag_from_score(score: float) -> str:
    """Same thresholds as frontend lib/trackb.ts:flagFromScore()."""
    if score >= 0.85:
        return "CRITICAL"
    if score >= 0.6:
        return "HIGH"
    if score >= 0.3:
        return "MEDIUM"
    return "LOW"


# Synthesis fraud_pattern -> Track-B FraudType shown in the UI.
PATTERN_TO_FRAUD_TYPE: dict[str, str | None] = {
    "fraud_ring": "FRAUD_RING",
    "money_laundering": "MONEY_MULE",
    "rapid_transfers": "SMURFING",
    "novel_pattern": None,
}


# -- agent outcome -> AgentResult --------------------------------------------------


def _agent_reasons(name: str, outcome: dict[str, Any]) -> list[str]:
    """Human-readable reasons extracted from each agent's explanation payload."""
    explanation = outcome.get("explanation")
    if isinstance(explanation, str):
        return [explanation]
    if not isinstance(explanation, dict):
        return []
    if name == "graph":
        reasons = explanation.get("reasons")
        out = [str(r) for r in reasons] if isinstance(reasons, list) else []
        if explanation.get("flag"):
            out.append(f"network flag {explanation['flag']}")
        return out
    if name == "geo":
        return [f"{k.replace('_', ' ')}: {v:.2f}" if isinstance(v, (int, float))
                else f"{k.replace('_', ' ')}: {v}"
                for k, v in explanation.items()]
    if name == "behavior":
        out = []
        if explanation.get("weights_profile"):
            out.append(f"{explanation['weights_profile']} history profile "
                       f"({explanation.get('history_count', 0)} prior txns)")
        breakdown = explanation.get("model_breakdown") or {}
        for model, detail in breakdown.items():
            if isinstance(detail, dict) and detail.get("risk") is not None:
                out.append(f"{model} risk {float(detail['risk']):.2f}")
            elif isinstance(detail, dict) and detail.get("status") not in (None, "ok"):
                out.append(f"{model}: {detail.get('status')}")
        return out
    return []


def agent_outcome_to_result(name: str, outcome: dict[str, Any]) -> dict[str, Any] | None:
    """AgentOutcome dict (from a *_completed event) -> frontend AgentResult."""
    if outcome.get("status") != "ok" or outcome.get("risk_score") is None:
        return None
    risk = float(outcome["risk_score"])
    return {
        "agent": name,
        "risk": round(risk, 4),
        "confidence": round(float(outcome.get("confidence") or 0.0), 4),
        "flag": flag_from_score(risk),
        "inferenceMs": round(float(outcome.get("latency_ms") or 0.0), 1),
        "reasons": _agent_reasons(name, outcome),
    }


def shap_to_features(shap: dict[str, Any] | None) -> list[dict[str, Any]]:
    """primary_shap_summary payload -> frontend ShapFeature[]."""
    if not shap or not isinstance(shap.get("top_features"), list):
        return []
    return [
        {
            "feature": str(item.get("feature", "")),
            "contribution": round(float(item.get("value", 0.0)), 4),
            "value": str(item.get("direction", "")),
        }
        for item in shap["top_features"]
    ]


def baseline_rule_decision(amount: float, hour: int) -> str:
    """The v25 rule engine the model is benchmarked against: pure
    amount/hour thresholds, blind to velocity/geo/network context."""
    if amount >= 500_000:
        return "BLOCK"
    if amount >= 100_000 or hour < 6 or hour >= 22:
        return "OTP"
    return "PASS"


def build_txn_log_entry(
    *,
    txn_id: str,
    evaluated_at: str,
    agents: dict[str, dict[str, Any]],
    weights: dict[str, Any],
    final_score: float,
    decision: str,
    baseline_decision: str,
    total_ms: float,
) -> dict[str, Any]:
    """One transactions_logs.json record (the model-verdict sample shape).

    ``decision``/``baseline_decision`` are the internal PASS/OTP/BLOCK labels;
    they are emitted in Track-B terms (ALLOW / OTP_ONLY / BLOCK)."""

    def block(name: str) -> dict[str, Any]:
        o = agents.get(name) or {}
        score = o.get("risk_score")
        return {
            "score": round(float(score), 2) if score is not None else None,
            "flag": flag_from_score(float(score)) if score is not None else "N/A",
            "inference_ms": round(float(o.get("latency_ms") or 0.0)),
        }

    return {
        "txn_id": txn_id,
        "evaluated_at": evaluated_at,
        "agent_verdicts": {
            "velocity_agent": block("velocity"),
            "geo_agent": block("geo"),
            "behavior_agent": block("behavior"),
            "graph_agent": block("graph"),
            "synthesis_agent": {
                "final_score": round(float(final_score), 2),
                "final_decision": DECISION_TO_TRACKB.get(decision, "ALLOW"),
                "weights_applied": {
                    "velocity": round(float(weights.get("velocity", 0.0)), 2),
                    "geo": round(float(weights.get("geo", 0.0)), 2),
                    "behavior": round(float(weights.get("behavior", 0.0)), 2),
                    "graph": round(float(weights.get("graph", 0.0)), 2),
                },
                "total_pipeline_ms": round(float(total_ms)),
            },
        },
        "baseline_rule_engine_decision": DECISION_TO_TRACKB.get(baseline_decision, "ALLOW"),
        "baseline_correct": baseline_decision == decision,
    }


def build_fraud_analysis(
    *,
    agents: dict[str, dict[str, Any]],
    synthesis: dict[str, Any],
    final: dict[str, Any],
    amount: float,
    hour: int,
    total_ms: float,
) -> dict[str, Any]:
    """Accumulated agent outcomes + synthesis/final event payloads -> FraudAnalysis."""
    agent_results = [
        r for name, o in agents.items()
        if (r := agent_outcome_to_result(name, o)) is not None
    ]
    final_score = float(final.get("final_score") or synthesis.get("final_score") or 0.0)
    pattern = str(final.get("fraud_pattern") or synthesis.get("fraud_pattern") or "novel_pattern")
    decision = str(final.get("decision", "PASS"))
    weights = synthesis.get("weights_applied") or {}
    used = [r["agent"] for r in agent_results]
    confidence = (
        sum(r["confidence"] for r in agent_results) / len(agent_results)
        if agent_results else 0.0
    )
    fraud_type = PATTERN_TO_FRAUD_TYPE.get(pattern)
    if fraud_type is None and decision == "BLOCK":
        fraud_type = "ACCOUNT_TAKEOVER"
    baseline = baseline_rule_decision(amount, hour)
    return {
        "agents": agent_results,
        "synthesis": {
            "finalRisk": round(final_score, 4),
            "confidence": round(confidence, 4),
            "pattern": pattern if decision != "PASS" else "none",
            "fraudType": fraud_type if decision != "PASS" else None,
            "decision": decision,
            "weights": {
                "velocity": round(float(weights.get("velocity", 0.0)), 4),
                "geo": round(float(weights.get("geo", 0.0)), 4),
                "behavior": round(float(weights.get("behavior", 0.0)), 4),
                "graph": round(float(weights.get("graph", 0.0)), 4),
            },
            "disagreement": round(float(final.get("disagreement_score")
                                        or synthesis.get("disagreement_score") or 0.0), 4),
            "inferenceMs": round(total_ms, 1),
        },
        "shap": shap_to_features(final.get("shap")),
        "baselineDecision": baseline,
        "baselineCorrect": baseline == decision,
        "agentsUsed": used,
    }


# -- DB row -> frontend shapes -----------------------------------------------------


def _iso(ts: Any) -> str:
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    return str(ts)


def row_to_transaction(row: Any) -> dict[str, Any]:
    """app_transactions record -> frontend Transaction."""
    fraud = row["fraud"]
    if isinstance(fraud, str):
        import json
        fraud = json.loads(fraud)
    return {
        "id": row["id"],
        "reference": row["reference"],
        "customerId": row["customer_id"],
        "customerName": row["customer_name"],
        "accountId": row["account_id"],
        "accountNumber": row["account_number"],
        "counterparty": {
            "name": row["cp_name"],
            "accountNumber": row["cp_account"],
            "bank": row["cp_bank"],
            "isWallet": row["cp_is_wallet"],
        },
        "amount": float(row["amount"]),
        "direction": row["direction"],
        "type": row["type"],
        "channel": row["channel"],
        "status": row["status"],
        "decision": row["decision"] or "PASS",
        "riskScore": float(row["risk_score"] or 0.0),
        "latencyMs": float(row["latency_ms"] or 0.0),
        "location": {
            "city": row["location_city"] or "Kathmandu",
            "lat": float(row["location_lat"] or 27.7172),
            "lng": float(row["location_lng"] or 85.324),
        },
        "device": row["device"] or "Mobile App",
        "ipAddress": row["ip_address"] or "",
        "remarks": row["remarks"] or "",
        "timestamp": _iso(row["ts"]),
        "fraud": fraud,
        "txnType": row["txn_type"],
        "counterpartyId": row["counterparty_id"] or "",
        "fraudType": row["fraud_type"],
        "authMethod": row["auth_method"] or "PIN",
        "merchantCategoryCode": row["mcc"] or "6011",
        "isVpn": row["is_vpn"],
        "isTor": row["is_tor"],
        "impossibleTravel": row["impossible_travel"],
        "prevTxnKm": float(row["prev_txn_km"] or 0.0),
        "prevTxnDeltaMin": float(row["prev_txn_delta_min"] or 0.0),
        "zScoreAmount": float(row["z_score_amount"] or 0.0),
        "txnCount1m": int(row["txn_count_1m"] or 0),
        "dormancyBreak": row["dormancy_break"],
        "nightFlag": row["night_flag"],
        "newCounterpartyFlag": row["new_counterparty_flag"],
        "deviceId": row["device_id"] or "",
    }


def row_to_customer(row: Any) -> dict[str, Any]:
    """app_customers record -> frontend Customer."""
    return {
        "id": row["id"],
        "name": row["name"],
        "gender": row["gender"],
        "accountNumber": row["account_number"],
        "mobile": row["mobile"],
        "email": row["email"],
        "address": row["address"],
        "city": row["city"],
        "kycStatus": row["kyc_status"],
        "riskLevel": row["risk_level"],
        "joinedAt": _iso(row["joined_at"]),
        "avatarColor": row["avatar_color"],
        "citizenshipNo": row["citizenship_no"],
        "branch": row["branch"],
        "district": row["district"],
        "province": row["province"],
        "kycTier": row["kyc_tier"],
        "isDormant": row["is_dormant"],
        "numBeneficiariesRegistered": row["num_beneficiaries_registered"],
    }


def row_to_account(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "customerId": row["customer_id"],
        "type": row["type"],
        "name": row["name"],
        "accountNumber": row["account_number"],
        "balance": float(row["balance"]),
        "currency": row["currency"],
        "status": row["status"],
        "interestRate": float(row["interest_rate"]),
    }


def row_to_card(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "customerId": row["customer_id"],
        "type": row["type"],
        "scheme": row["scheme"],
        "number": row["number"],
        "holder": row["holder"],
        "expiry": row["expiry"],
        "status": row["status"],
        "limit": float(row["card_limit"]),
    }


# -- synthesis_audit row -> Track-B ModelVerdict -----------------------------------

DECISION_TO_TRACKB: dict[str, str] = {"PASS": "ALLOW", "OTP": "OTP_ONLY", "BLOCK": "BLOCK"}


def audit_row_to_model_verdict(row: Any, txn: dict[str, Any] | None,
                               account_id: str = "") -> dict[str, Any]:
    """synthesis_audit record (+ optional app txn for context) -> ModelVerdict."""
    import json

    def _load(v: Any) -> dict[str, Any]:
        if v is None:
            return {}
        return json.loads(v) if isinstance(v, str) else v

    verdicts = _load(row["input_verdicts"])
    explanations = _load(row["agent_explanations"])
    weights = _load(row["blended_weights"])
    final_score = float(row["final_score"])
    decision = str(row["decision"])

    agent_verdicts = []
    for agent, verdict in verdicts.items():
        risk = float(verdict.get("risk_score", 0.0))
        expl = explanations.get(agent) or {}
        reasons = _agent_reasons(agent, expl) if isinstance(expl, dict) else []
        agent_verdicts.append({
            "agent": agent,
            "score": round(risk, 4),
            "flag": flag_from_score(risk),
            "inference_ms": round(float(verdict.get("latency_ms") or 0.0), 1),
            "reasons": reasons,
        })
    agent_verdicts.append({
        "agent": "synthesis",
        "score": round(final_score, 4),
        "flag": flag_from_score(final_score),
        "inference_ms": 1.0,
        "reasons": [f"disagreement_score={float(row['disagreement_score']):.3f}"],
    })

    fraud_decision = DECISION_TO_TRACKB.get(decision, "ALLOW")
    if decision == "BLOCK" and row["otp_forced_by_disagreement"]:
        fraud_decision = "BLOCK_AND_OTP"
    pattern = str(row["fraud_pattern"])
    amount = float(txn["amount"]) if txn else 0.0
    hour = 12
    if txn:
        try:
            hour = datetime.fromisoformat(txn["timestamp"]).hour
        except (ValueError, TypeError):
            pass
    baseline = baseline_rule_decision(amount, hour)
    return {
        "txn_id": row["txn_id"],
        "account_id": account_id,
        "txn_type": row["txn_type_raw"],
        "agent_verdicts": agent_verdicts,
        "weights_applied": {
            "velocity": round(float(weights.get("velocity", 0.0)), 4),
            "geo": round(float(weights.get("geo", 0.0)), 4),
            "behavior": round(float(weights.get("behavior", 0.0)), 4),
            "graph": round(float(weights.get("graph", 0.0)), 4),
        },
        "fraud_probability": round(final_score, 4),
        "fraud_decision": fraud_decision,
        "fraud_type_predicted": PATTERN_TO_FRAUD_TYPE.get(pattern) if decision != "PASS" else None,
        "baseline_decision": DECISION_TO_TRACKB.get(baseline, "ALLOW"),
        "baseline_correct": baseline == decision,
        "total_pipeline_ms": float(txn["latencyMs"]) if txn else 0.0,
        "disagreement_score": round(float(row["disagreement_score"]), 4),
    }
