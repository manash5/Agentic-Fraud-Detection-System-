from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from shared.constants.service_names import API_GATEWAY
from shared.routers.health import health_router

VELOCITY_AGENT_URL = os.environ.get("VELOCITY_AGENT_URL", "http://velocity-agent:8001")
GEO_AGENT_URL = os.environ.get("GEO_AGENT_URL", "http://geo-agent:8002")
BEHAVIOR_AGENT_URL = os.environ.get("BEHAVIOR_AGENT_URL", "http://behavior-agent:8003")
SYNTHESIS_AGENT_URL = os.environ.get("SYNTHESIS_AGENT_URL", "http://synthesis-agent:8004")
DECISION_OTP_URL = os.environ.get("DECISION_OTP_URL", "http://decision-otp-service:8005")

app = FastAPI(
    title="Fraud Detection API Gateway",
    version="0.2.0",
    description="Public entrypoint for transaction risk requests.",
)

app.include_router(health_router(API_GATEWAY))


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class EvaluateRequest(BaseModel):
    txn_id: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)


class EvaluateAllRequest(BaseModel):
    txn_id: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    transaction_type: str = Field(
        "p2p_transfer",
        description="p2p_transfer | merchant_payment | atm_withdrawal | bill_payment",
    )


class DecisionRequest(BaseModel):
    transaction_id: str = Field(..., min_length=1)
    final_score: float = Field(..., ge=0.0, le=1.0)


class OTPInitiateRequest(BaseModel):
    transaction_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=1)
    email: str = Field(..., min_length=1)


class OTPVerifyRequest(BaseModel):
    transaction_id: str = Field(..., min_length=1)
    sms_code: str | None = None
    email_code: str | None = None


# ---------------------------------------------------------------------------
# Individual agent proxy routes
# ---------------------------------------------------------------------------

@app.post("/evaluate/velocity")
def evaluate_velocity(body: EvaluateRequest) -> dict[str, Any]:
    return _post_agent(f"{VELOCITY_AGENT_URL}/evaluate", body.model_dump())


@app.post("/evaluate/geo")
def evaluate_geo(body: EvaluateRequest) -> dict[str, Any]:
    return _post_agent(f"{GEO_AGENT_URL}/evaluate", body.model_dump())


@app.post("/evaluate/behavior")
def evaluate_behavior(body: EvaluateRequest) -> dict[str, Any]:
    return _post_agent(f"{BEHAVIOR_AGENT_URL}/evaluate", body.model_dump())


@app.post("/evaluate/synthesise")
def evaluate_synthesise(body: dict[str, Any]) -> dict[str, Any]:
    return _post_agent(f"{SYNTHESIS_AGENT_URL}/evaluate/synthesise", body)


@app.post("/evaluate/decision")
def evaluate_decision(body: DecisionRequest) -> dict[str, Any]:
    return _post_agent(f"{DECISION_OTP_URL}/evaluate/decision", body.model_dump())


# ---------------------------------------------------------------------------
# OTP routes
# ---------------------------------------------------------------------------

@app.post("/otp/initiate")
def otp_initiate(body: OTPInitiateRequest) -> dict[str, Any]:
    return _post_agent(f"{DECISION_OTP_URL}/evaluate/otp/initiate", body.model_dump())


@app.post("/otp/verify")
def otp_verify(body: OTPVerifyRequest) -> dict[str, Any]:
    return _post_agent(f"{DECISION_OTP_URL}/evaluate/otp/verify", body.model_dump())


# ---------------------------------------------------------------------------
# Full orchestrated pipeline
# ---------------------------------------------------------------------------

@app.post("/evaluate/all")
def evaluate_all(body: EvaluateAllRequest) -> dict[str, Any]:
    """Chain all five agents: velocity → geo → behavior → synthesis → decision."""
    started = time.perf_counter()
    agent_payload = {"txn_id": body.txn_id, "account_id": body.account_id}

    velocity = _post_agent(f"{VELOCITY_AGENT_URL}/evaluate", agent_payload)
    geo = _post_agent(f"{GEO_AGENT_URL}/evaluate", agent_payload)
    behavior = _post_agent(f"{BEHAVIOR_AGENT_URL}/evaluate", agent_payload)

    synthesis = _post_agent(
        f"{SYNTHESIS_AGENT_URL}/evaluate/synthesise",
        {
            "transaction_id": body.txn_id,
            "transaction_type": body.transaction_type,
            "velocity": _to_verdict(velocity),
            "geo": _to_verdict(geo),
            "behavior": _to_verdict(behavior),
        },
    )

    final_score: float = synthesis["result"]["final_score"]
    decision = _post_agent(
        f"{DECISION_OTP_URL}/evaluate/decision",
        {"transaction_id": body.txn_id, "final_score": final_score},
    )

    return {
        "txn_id": body.txn_id,
        "account_id": body.account_id,
        "agents": {"velocity": velocity, "geo": geo, "behavior": behavior},
        "synthesis": synthesis["result"],
        "decision": decision,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


# ---------------------------------------------------------------------------
# Legacy combined route (kept for backward compatibility)
# ---------------------------------------------------------------------------

@app.post("/evaluate/both")
def evaluate_both(body: EvaluateRequest) -> dict[str, Any]:
    started = time.perf_counter()
    payload = body.model_dump()
    velocity = _post_agent(f"{VELOCITY_AGENT_URL}/evaluate", payload)
    geo = _post_agent(f"{GEO_AGENT_URL}/evaluate", payload)
    return {
        "txn_id": body.txn_id,
        "account_id": body.account_id,
        "agents": {"velocity": velocity, "geo": geo},
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_verdict(agent_response: dict[str, Any]) -> dict[str, Any]:
    """Extract the AgentVerdict fields that synthesis-agent expects."""
    return {
        "risk_score": agent_response.get("risk_score", 0.0),
        "confidence": agent_response.get("confidence", agent_response.get("confidence_score", 0.5)),
        "latency_ms": agent_response.get("latency_ms", 0),
    }


def _post_agent(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8") or exc.reason
        raise HTTPException(status_code=exc.code, detail=detail) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=503, detail=f"Agent unavailable: {exc.reason}") from exc
