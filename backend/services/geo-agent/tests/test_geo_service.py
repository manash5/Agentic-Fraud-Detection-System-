"""Service-level tests: the single /evaluate endpoint over the Phase 1 agent.

Run from `backend/`: uv run pytest services/geo-agent/tests
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = Path(__file__).resolve().parents[3]
# Every service uses the package name `app` — purge cached modules so this
# file gets ITS service's app.main even when other service suites ran first.
for key in list(sys.modules):
    if key == "app" or key.startswith("app."):
        del sys.modules[key]
for path in (str(SERVICE_ROOT), str(BACKEND_ROOT)):
    while path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)

import app.main as service  # noqa: E402
from agents.geo_agent import GeoAgent  # noqa: E402
from feature_engineering.config import load_config  # noqa: E402
from tests.test_geo_agent import (  # noqa: E402
    BASE_TS,
    KATHMANDU,
    MOSCOW,
    DeadAsyncRedis,
    FakeAsyncRedis,
    FakePgPool,
    cache_location,
)

CFG = load_config()


def make_client(fake_redis=None, pg_pool=None):
    service.agent = GeoAgent(
        redis_client=fake_redis or FakeAsyncRedis(),
        pg_pool=pg_pool or FakePgPool(),
        cfg=CFG,
    )
    return TestClient(service.app)


def test_health_reports_service_name():
    with make_client() as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"service": "geo-agent", "status": "ok"}


def test_evaluate_response_shape_and_latency_budget():
    fake = FakeAsyncRedis()
    cache_location(fake, "ACC-A", *KATHMANDU, ts=BASE_TS - timedelta(hours=1))
    with make_client(fake_redis=fake) as client:
        response = client.post("/evaluate", json={
            "txn_id": "T1",
            "account_id": "ACC-A",
            "device_id": "DEV-1",
            "latitude": MOSCOW[0],
            "longitude": MOSCOW[1],
            "timestamp": BASE_TS.isoformat(),
        })
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"txn_id", "agent_name", "risk_score", "confidence_score", "signals", "latency_ms"}
    assert set(body["signals"]) == {"travel_feasibility", "device_novelty"}
    assert body["signals"]["travel_feasibility"] == 1.0  # KTM -> Moscow in 1h
    assert 0.0 <= body["risk_score"] <= 1.0
    # Paper budget for the Geo Agent is 20-50ms; Phase 1 has no Neo4j so a
    # single evaluation must land well under it.
    assert body["latency_ms"] < 20.0


def test_evaluate_cold_start_account():
    with make_client() as client:
        response = client.post("/evaluate", json={
            "txn_id": "T1",
            "account_id": "ACC-NEW",
            "device_id": "DEV-1",
            "latitude": KATHMANDU[0],
            "longitude": KATHMANDU[1],
        })
    assert response.status_code == 200
    body = response.json()
    assert body["confidence_score"] == 0.0
    assert body["risk_score"] <= 0.2  # neutral first device, no travel history


def test_redis_down_returns_503_not_fake_score():
    with make_client(fake_redis=DeadAsyncRedis()) as client:
        response = client.post("/evaluate", json={
            "txn_id": "T1",
            "account_id": "ACC-A",
            "device_id": "DEV-1",
            "latitude": KATHMANDU[0],
            "longitude": KATHMANDU[1],
        })
    assert response.status_code == 503


def test_missing_fields_rejected():
    with make_client() as client:
        response = client.post("/evaluate", json={"txn_id": "T1", "account_id": "ACC-A"})
    assert response.status_code == 422
