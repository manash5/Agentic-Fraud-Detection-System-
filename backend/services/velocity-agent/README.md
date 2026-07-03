# Velocity Agent

FastAPI microservice for the paper's §IV-C-1 Velocity Agent. It exposes a
single evaluation endpoint backed by `agents/velocity_agent.py`, which scores
each transaction from **Redis exclusively** — sliding-window transaction
counts, cached account baselines, and per-account txn-type distributions —
inside a 1–2 ms latency budget.

## Data sources — what touches what

| Path | Storage | When |
|---|---|---|
| Hot path (`POST /evaluate`) | **Redis only** — `user:{account_id}:count_2min` / `:count_1hr` sorted sets, `account_baseline:{account_id}` and `user_type_dist:{account_id}` hashes | Every request |
| Baseline refresh | Postgres (`transactions_raw` in `fraud_detection_global`) → Redis cache | Nightly, via `uv run python -m feature_engineering.nightly_baseline_job` from `backend/` |

This service itself **never opens a Postgres connection**. If the nightly job
hasn't cached a baseline for an account, the agent treats it as a cold start:
neutral/zero signals where a baseline would be needed, and a low confidence
score so the Synthesis Agent knows to lean on the other agents.

If Redis is unreachable, `/evaluate` returns **503** rather than a made-up
score — the fallback policy belongs to the orchestration layer.

The five signals, weighting rationale, and the stubbed balance-integrity
signal (no balance columns exist in the dataset) are documented in
`backend/agents/velocity_agent.py` and configured in
`backend/feature_engineering/feature_config.yaml` under `velocity_agent:`.

## Run locally

From `backend/` (the module needs `agents/`, `feature_engineering/` and
`shared/` importable, which `app/main.py` arranges itself):

```bash
uv run uvicorn app.main:app --reload --port 8001 --app-dir services/velocity-agent
```

Redis host/port come from `feature_config.yaml` (`localhost:6379`), overridable
with `FRAUD_REDIS_HOST` / `FRAUD_REDIS_PORT` (docker-compose's `REDIS_HOST` is
also honored).

## Endpoints

Health check:

```bash
curl http://localhost:8001/health
```

Evaluate a live transaction event (the event data rides in the payload — there
is no database lookup by `txn_id`):

```bash
curl -X POST http://localhost:8001/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "txn_id": "TXN-20260601-00000001",
    "account_id": "ACC-1000599",
    "amount_npr": 12000,
    "txn_type": "ESEWA_P2P",
    "timestamp": "2026-06-01T12:00:00Z"
  }'
```

Response:

```json
{
  "txn_id": "TXN-20260601-00000001",
  "agent_name": "velocity-agent",
  "risk_score": 0.58,
  "confidence": 0.04,
  "latency_ms": 0
}
```

`timestamp` and `txn_type` are optional (`timestamp` defaults to now-UTC; a
missing `txn_type` scores the type signal neutrally). `confidence` ramps from
0 to 1 as the account accumulates history (50-observation threshold, tunable
in `feature_config.yaml`).

## Tests

From `backend/`:

```bash
uv run pytest services/velocity-agent/tests   # service endpoint tests
uv run pytest tests/test_velocity_agent.py    # agent signals + p99 latency
```
