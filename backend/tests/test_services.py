

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _app(service: str):
    service_dir = BACKEND_ROOT / "services" / service

    # Each service uses the package name `app` — purge cached modules between loads.
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]

    for path in (str(service_dir), str(BACKEND_ROOT)):
        while path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)

    return importlib.import_module("app.main").app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "service,expected",
    [
        ("api-gateway", "api-gateway"),
        ("geo-agent", "geo-agent"),
        ("velocity-agent", "velocity-agent"),
        ("behavior-agent", "behavior-agent"),
        ("synthesis-agent", "synthesis-agent"),
        ("decision-otp-service", "decision-otp-service"),
    ],
)
async def test_health(service: str, expected: str) -> None:
    app = _app(service)
    if service == "behavior-agent":
        from app.model_loader import load_feature_table_index, load_models

        app.state.models = load_models()
        app.state.feature_index = load_feature_table_index()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == expected


@pytest.mark.asyncio
async def test_geo_evaluate() -> None:
    app = _app("geo-agent")
    # Swap in in-memory fakes so the test needs no live Redis/Postgres.
    from agents.geo_agent import GeoAgent
    from tests.test_geo_agent import FakeAsyncRedis, FakePgPool

    sys.modules["app.main"].agent = GeoAgent(
        redis_client=FakeAsyncRedis(), pg_pool=FakePgPool()
    )
    transport = ASGITransport(app=app)
    payload = {
        "txn_id": "txn-001",
        "account_id": "ACC-1",
        "device_id": "DEV-1",
        "latitude": 27.7172,
        "longitude": 85.3240,
    }
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/evaluate", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert 0.0 <= body["risk_score"] <= 1.0
    assert body["confidence_score"] == 0.0  # unseen account: cold start


@pytest.mark.asyncio
async def test_velocity_evaluate() -> None:
    app = _app("velocity-agent")
    # Swap in the in-memory fake so the test needs no live Redis.
    from agents.velocity_agent import VelocityAgent
    from tests.test_velocity_agent import FakeRedis

    sys.modules["app.main"].agent = VelocityAgent(client=FakeRedis())
    transport = ASGITransport(app=app)
    payload = {"txn_id": "txn-002", "account_id": "ACC-1", "amount_npr": 75000.0, "txn_type": "ESEWA_P2P"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/evaluate", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert 0.0 <= body["risk_score"] <= 1.0
    assert body["confidence"] == 0.0  # unseen account: cold-start confidence


@pytest.mark.asyncio
async def test_behavior_evaluate_with_shap() -> None:
    app = _app("behavior-agent")
    from app.model_loader import load_feature_table_index, load_models

    models = load_models()
    app.state.models = models
    app.state.feature_index = load_feature_table_index()
    transport = ASGITransport(app=app)

    if models.loaded and app.state.feature_index:
        txn_id = next(iter(app.state.feature_index))
        payload = {"transaction_id": txn_id}
    else:
        from app.model_loader import BEHAVIOR_FEATURE_NAMES

        payload = {
            "transaction_id": "txn-heuristic",
            "features": [1000.0, 14.0, 2.0, 3.0, 10.0, 500.0, 0.1, 30.0, 0.0, 5.0],
        }
        assert len(payload["features"]) == len(BEHAVIOR_FEATURE_NAMES)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/evaluate/risk", json=payload)
    body = response.json()
    assert response.status_code == 200
    assert body["shap"] is not None


@pytest.mark.asyncio
async def test_synthesis_evaluate() -> None:
    app = _app("synthesis-agent")
    transport = ASGITransport(app=app)
    payload = {
        "transaction_id": "txn-004",
        "transaction_type": "p2p_transfer",
        "velocity": {"risk_score": 0.7, "confidence": 0.9, "latency_ms": 12},
        "geo": {"risk_score": 0.3, "confidence": 0.8, "latency_ms": 20},
        "behavior": {"risk_score": 0.5, "confidence": 0.85, "latency_ms": 45},
    }
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/evaluate/synthesise", json=payload)
    assert response.status_code == 200
    assert "final_score" in response.json()["result"]


@pytest.mark.asyncio
async def test_decision_thresholds() -> None:
    app = _app("decision-otp-service")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pass_resp = await client.post("/evaluate/decision", json={"transaction_id": "t1", "final_score": 0.10})
        otp_resp = await client.post("/evaluate/decision", json={"transaction_id": "t2", "final_score": 0.50})
        block_resp = await client.post("/evaluate/decision", json={"transaction_id": "t3", "final_score": 0.90})
    assert pass_resp.json()["decision"] == "PASS"
    assert otp_resp.json()["decision"] == "OTP"
    assert block_resp.json()["decision"] == "BLOCK"
