# Geo Agent

The Geo Agent implements the paper's §IV-C-2 geographic risk layer as a FastAPI microservice. It evaluates location feasibility, anonymizing network signals, device fingerprint risk, and Neo4j account graph proximity to known fraud rings such as COMM-042.

## Risk Rules

The final risk score is the sum of rule contributions capped at `1.0`.

| Rule | Threshold | Risk |
|---|---:|---:|
| Impossible travel | `impossible_travel = true`, indicating speed above 900 km/h | `+0.50` |
| New device | Current `device_id` has not previously appeared for the account | `+0.25` |
| Unknown device fingerprint | `device_id` is missing from `device_fingerprints` | `+0.10` |
| Rooted device + locale mismatch | `is_rooted_or_jailbroken = true`, `locale = en_US`, and `ip_country = Nepal` | `+0.40` |
| VPN/Tor | VPN adds `+0.20`; Tor adds `+0.30` and takes precedence if both are present | up to `+0.30` |
| Datacenter IP | `is_datacenter = true` | `+0.15` |
| Shared IP graph proximity | Additional account neighbor found in Neo4j | up to `+0.20` |
| Circular money flow | A 1-3 hop account cycle is detected | `+0.25` |
| Fraud ring proximity | 1 hop from fraud seed `+0.35`, 2 hops `+0.25`, 3 hops `+0.10`, 4+ or none `+0.0` | up to `+0.35` |

Confidence is `0.95` when geo, device, country, and device id data are present; `0.75` when one is missing; and `0.50` when multiple fields are missing or this appears to be the account's first transaction. Impossible travel boosts confidence to `0.98`. Neo4j degradation caps confidence at `0.60`.

## Neo4j Queries

Shared IP accounts:

```cypher
MATCH (a:Account {id: $account_id})-[*1..1]-(other:Account)
WHERE other.id <> $account_id
RETURN count(distinct other) as shared_account_count
```

Circular flow:

```cypher
MATCH (a:Account {id: $account_id})-[*1..3]-(b:Account {id: $account_id})
RETURN count(*) > 0 as has_circular_flow
```

Fraud ring proximity:

```cypher
MATCH (a:Account {id: $account_id}), (fraud:Account {is_fraud_seed: true})
MATCH p = shortestPath((a)-[*1..4]-(fraud))
RETURN fraud.id as fraud_node, length(p) as distance
ORDER BY distance ASC LIMIT 1
```

## Run

The service reads `DATABASE_URL`, `NEO4J_URI`, `NEO4J_USERNAME`, and `NEO4J_PASSWORD` from `backend/.env`.

```bash
cd backend/services/geo-agent
uvicorn app.main:app --reload --port 8002
```

Health check:

```bash
curl http://localhost:8002/health
```

Evaluate a transaction:

```bash
curl -X POST http://localhost:8002/evaluate \
  -H "Content-Type: application/json" \
  -d '{"txn_id": "TXN-20260101-00000001", "account_id": "ACC-0000001"}'
```

Example response:

```json
{
  "txn_id": "TXN-20260101-00000001",
  "risk_score": 0.35,
  "confidence": 0.95,
  "breakdown": {
    "impossible_travel_risk": 0.0,
    "new_device_risk": 0.0,
    "rooted_locale_mismatch_risk": 0.0,
    "vpn_tor_risk": 0.0,
    "datacenter_risk": 0.0,
    "shared_ip_risk": 0.0,
    "circular_flow_risk": 0.0,
    "fraud_ring_proximity_risk": 0.35
  },
  "fraud_ring_details": {
    "is_near_fraud_seed": true,
    "nearest_fraud_node_distance_hops": 1,
    "nearest_fraud_node_id": "ACC-0011204"
  },
  "latency_ms": 58
}
```

## COMM-042 Testing

The Neo4j loader expects COMM-042 metadata at `backend/datasets/comm042_ring_members.json` and marks the collector plus ring members with `is_fraud_seed = true` and `community_id = "COMM-042"`.

To test manually, pick an account id from that JSON file's `ring_members` list or its `collector_account`, then call:

```bash
curl -X POST http://localhost:8002/evaluate \
  -H "Content-Type: application/json" \
  -d '{"txn_id": "TXN-20260101-00000001", "account_id": "ACC-COMM042-001"}'
```

If the graph has a shortest path to a fraud seed within three hops, the `fraud_ring_proximity_risk` field will be non-zero and `fraud_ring_details.is_near_fraud_seed` will be `true`.

## Single Backend API

When the full backend stack is running, the API Gateway exposes Geo and Velocity through one backend port:

```bash
cd backend
docker compose up --build api-gateway
```

Call Geo through the gateway:

```bash
curl -X POST http://localhost:8000/evaluate/geo \
  -H "Content-Type: application/json" \
  -d '{"txn_id": "TXN-20260101-00000001", "account_id": "ACC-0000001"}'
```

Call Velocity and Geo together:

```bash
curl -X POST http://localhost:8000/evaluate/both \
  -H "Content-Type: application/json" \
  -d '{"txn_id": "TXN-20260101-00000001", "account_id": "ACC-0000001"}'
```

## Tests

```bash
pytest backend/services/geo-agent/tests/
```
