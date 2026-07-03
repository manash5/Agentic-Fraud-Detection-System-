"""Service-level tests: the single /evaluate endpoint over the Redis-only agent.

Run from `backend/`: uv run pytest services/velocity-agent/tests
"""

from __future__ import annotations

import sys
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
from agents.velocity_agent import VelocityAgent, write_baseline, write_type_dist  # noqa: E402
from feature_engineering.config import load_config  # noqa: E402
from tests.test_velocity_agent import TYPE_DIST, WARM_BASELINE, DeadRedis, FakeRedis  # noqa: E402

CFG = load_config()


@pytest.fixture()
def client():
    with TestClient(service.app) as test_client:
        yield test_client


def test_health_reports_service_name(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"service": "velocity-agent", "status": "ok"}


def test_evaluate_cold_account(client):
    service.agent = VelocityAgent(client=FakeRedis(), cfg=CFG)
    response = client.post(
        "/evaluate",
        json={"txn_id": "T1", "account_id": "ACC-NEW", "amount_npr": 5000.0},
    )
    assert response.status_code == 200
    body = response.json()
    assert 0.0 <= body["risk_score"] <= 1.0
    assert body["confidence"] == 0.0  # no history yet
    assert body["agent_name"] == "velocity-agent"


def test_evaluate_warm_account_spike(client):
    fake = FakeRedis()
    write_baseline("ACC-A", WARM_BASELINE, fake, cfg=CFG)
    write_type_dist("ACC-A", TYPE_DIST, fake, cfg=CFG)
    service.agent = VelocityAgent(client=fake, cfg=CFG)
    response = client.post(
        "/evaluate",
        json={
            "txn_id": "T2",
            "account_id": "ACC-A",
            "amount_npr": 12_000.0,  # 12x the account's average
            "txn_type": "p2p",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["risk_score"] > 0.4
    assert body["confidence"] == 1.0


def test_redis_down_returns_503_not_fake_score(client):
    service.agent = VelocityAgent(client=DeadRedis(), cfg=CFG)
    response = client.post(
        "/evaluate",
        json={"txn_id": "T3", "account_id": "ACC-A", "amount_npr": 100.0},
    )
    assert response.status_code == 503


def test_missing_amount_is_rejected(client):
    response = client.post("/evaluate", json={"txn_id": "T4", "account_id": "ACC-A"})
    assert response.status_code == 422
