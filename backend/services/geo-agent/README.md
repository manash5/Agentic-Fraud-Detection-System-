# Geo Agent

FastAPI microservice for the paper's §IV-C-2 Geo Agent, **Phase 1**: travel
feasibility + device fingerprint novelty, backed by `agents/geo_agent.py`.
Redis-first hot path with an asyncpg fallback on cache miss; well under the
paper's 20–50 ms Geo Agent budget (~1–2 ms warm).

**Deliberately absent (not stubbed):** shared-IP, circular-flow and
fraud-ring graph checks. Those belong to a future Graph Agent built from
`account_graph_nodes.csv` / `account_graph_edges.csv` — this agent's risk
aggregation simply does not include them. No Neo4j anywhere in this service.

## Data sources — what touches what

| Path | Storage | When |
|---|---|---|
| Last-known location | Redis `geo:last:{account_id}` (24h TTL), read **before** being overwritten | Every request |
| Known devices | Redis `devices:known:{account_id}` SET (90d TTL), learned on every request | Every request |
| Location fallback | Postgres `geo_events` (by `account_id` — the column exists; the brief's "Option A vs B" blocker did not apply to this schema) | Redis miss only |
| Device metadata | Postgres `device_fingerprints` (198,803 rows, loaded by `scripts/load_device_fingerprints.py`) | Unknown devices only |
| Confidence | Redis `account_baseline:{account_id}` `n_geo_90d` → `geo:obs:` cache → Postgres COUNT | Baseline miss only |

Redis or Postgres unreachable → **503**, never a made-up score.

## The two signals

- **travel_feasibility** — haversine distance from last known location ÷
  elapsed time vs `max_plausible_kmh` (900, matching the batch
  `impossible_travel` definition). Gradient, not a boolean: 0 at ≤450 km/h,
  0.5 exactly at 900, 1.0 at ≥1350. Sub-50 km hops are treated as
  IP-geolocation jitter.
- **device_novelty** — 0 for known devices; 0.6 base for a device new to the
  account (0.2 if it's the account's first-ever device), pushed higher by
  rooted/jailbroken (+0.2), shared device (+0.1), seen on ≥3 accounts (+0.1).

Risk = 50/50 weighted sum — the paper doesn't specify the Geo Agent's
internal sub-weights; this default is documented in `feature_config.yaml`
(`geo_agent:` section) along with every other threshold.

## Run locally

From `backend/`:

```bash
uv run uvicorn app.main:app --reload --port 8002 --app-dir services/geo-agent
```

## Endpoint

```bash
curl -X POST http://localhost:8002/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "txn_id": "TXN-1",
    "account_id": "ACC-1000159",
    "device_id": "DEV-565127",
    "latitude": 55.7558,
    "longitude": 37.6173,
    "timestamp": "2026-05-01T15:30:00Z"
  }'
```

```json
{
  "txn_id": "TXN-1",
  "agent_name": "geo-agent",
  "risk_score": 1.0,
  "confidence_score": 0.1,
  "signals": {"travel_feasibility": 1.0, "device_novelty": 1.0},
  "latency_ms": 1.861
}
```

## Tests

From `backend/`:

```bash
uv run pytest services/geo-agent/tests   # endpoint tests
uv run pytest tests/test_geo_agent.py    # signals, fallback, concurrent load
```
