# Fraud Detection Backend — Implementation Guide

> Companion implementation for *"An Agentic Multi-Model Framework for Real-Time Fraud Detection in Nepal's Digital Payment Ecosystem"* (Softwarica College, 2026)

---

## 1. The Big Picture

A transaction comes in → **4 agents run in parallel** (velocity, geo, graph, behavior) → **Synthesis** fuses their scores → a **decision** (`PASS` / `OTP` / `BLOCK`) comes out, with **SHAP explanations** and a Postgres audit record.

```
                         POST /evaluate  (sync — one response)
                         POST /pipeline/submit  (async — Kafka)
                                    │
                                    ▼
              ┌─────────────────────────────────────────────┐
              │  Parallel fan-out (asyncio.gather)            │
              │  Velocity │ Geo │ Graph │ Behavior (+ SHAP) │
              └─────────────────────┬───────────────────────┘
                                    │ risk + confidence each
                                    ▼
              ┌─────────────────────────────────────────────┐
              │  Synthesis (pure math, no I/O)              │
              │  pattern → Table I/II weights → fuse → decide│
              └─────────────────────┬───────────────────────┘
                                    ▼
              decision + final_score + shap + agent_explanations
                                    │
                                    ▼
              Postgres synthesis_audit (verdict + SHAP + weights)
```

**Two entry points:**

| Path | Endpoint | Returns |
|---|---|---|
| **Sync** | `POST /evaluate` | Full decision + all agent outcomes + SHAP in one HTTP response |
| **Async** | `POST /pipeline/submit` | `202 Accepted` immediately; orchestrator processes via Kafka |

---

## 2. Folder Structure (what matters today)

```
backend/
├── app/main.py              # unified FastAPI — all agents + /evaluate + /pipeline/submit
├── agents/                  # velocity, geo, graph, behavior, synthesis (pure math)
├── behavior_agent/          # XGBoost + IsoForest + LSTM builders/scorers + SHAP
├── synthesis_agent/         # txn_type mapping, synthesis endpoint, audit store
├── pipeline/                # agent_runner (parallel fan-out), explanations, audit helper
├── kafka_bus/               # Kafka envelope, producer, orchestrator consumer
├── shared/                  # schemas, SHAP utils, config
├── models/                  # trained artifacts (gitignored except manifests)
└── tests/
```

---

## 3. Running Locally

### Prerequisites

- Postgres (`fraud_detection_global`) with reference tables loaded
- Redis (velocity + geo)
- Neo4j (`fraud-detection` database, graph loaded)
- Kafka on `localhost:9092` (only for async `/pipeline/submit` path)

### Start the unified API

```bash
cd backend
uv sync
uv run uvicorn app.main:app --port 8000
```

### Health check

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

Expect `"velocity"`, `"geo"`, `"graph"`, `"behavior"`, `"synthesis"`, `"kafka"` all `"ok"`.

### Best test — full pipeline in one call

```bash
curl -s -X POST http://localhost:8000/evaluate \
  -H 'Content-Type: application/json' \
  -d '{
    "txn_id":"TXN-20260528-32895DA9",
    "account_id":"ACC-1002022",
    "txn_type":"ATM_WITHDRAWAL",
    "amount":543.61,
    "device_id":"DEV-4C4CFB",
    "latitude":27.7172,
    "longitude":85.3240
  }' | python3 -m json.tool
```

**Response includes:**

| Field | Meaning |
|---|---|
| `decision` | `PASS`, `OTP`, or `BLOCK` |
| `final_score` | Fused fraud score 0–1 |
| `agents_used` | Which agents contributed |
| `agent_outcomes` | Per-agent risk, confidence, explanation, latency |
| `explanations` | Same data, audit-friendly shape |
| `shap` | Top-10 XGBoost feature contributions (behavior agent) |
| `weights_applied` | Synthesis blend weights used |

### Kafka async path

Terminal 1 — API (already running). Terminal 2 — orchestrator:

```bash
cd backend
uv run python -m kafka_bus.orchestrator
```

Terminal 3 — submit:

```bash
curl -s -X POST http://localhost:8000/pipeline/submit \
  -H 'Content-Type: application/json' \
  -d '{"txn_id":"TXN-20260528-32895DA9","account_id":"ACC-1002022","txn_type":"ATM_WITHDRAWAL","amount":543.61,"device_id":"DEV-4C4CFB","latitude":27.7172,"longitude":85.3240}'
```

Watch events: `kafka-console-consumer --bootstrap-server localhost:9092 --topic fraud-events --from-beginning`

Event flow: `transaction_received` → `velocity_completed` / `geo_completed` / `graph_completed` / `behavior_completed` → `synthesis_completed` → **`final_decision`** (includes `decision` + `shap`).

---

## 4. Individual Agent Endpoints (debugging)

All mounted in the same app on port 8000:

| Endpoint | Agent |
|---|---|
| `POST /velocity/evaluate` | Velocity (Redis sliding windows) |
| `POST /geo/evaluate` | Geo (travel + device) |
| `POST /graph/evaluate` | Graph (Neo4j network) |
| `POST /agents/behavior/evaluate` | Behavior (XGBoost + IsoForest + LSTM + **SHAP**) |
| `POST /agents/synthesis/evaluate` | Synthesis only (pass in pre-computed scores) |

Behavior response includes `shap.top_features` — the top 10 features pushing fraud risk up or down for that transaction.

---

## 5. SHAP Explainability

- **Where computed:** `behavior_agent/scorers.py` during XGBoost scoring (config: `behavior_agent/config.yaml` → `shap.enabled`, `shap.top_k`).
- **Optimization:** `TreeExplainer` is built **once at model load** (`behavior_agent/artifacts.py`), not per request.
- **What you get:** top-|k| features only (default 10), not the full 87-dim vector — keeps responses and audit rows small.
- **Where stored:** `synthesis_audit.shap_explanation` and `synthesis_audit.agent_explanations` JSONB columns.
- **Other agents:** velocity/geo/graph return rule-based explanations in `agent_outcomes.*.explanation` (signals, reasons) — not SHAP.

Helpers live in `shared/explainability/shap_utils.py`.

---

## 6. Synthesis Formula (§IV-E)

Implemented in `agents/synthesis_agent.py` (pure functions) and called from `pipeline/agent_runner.fuse()`.

**Step 1 — Layer 1 weights** (`shared/schemas/risk.py` Table I): by mapped transaction type.

**Step 2 — Layer 2 weights** (Table II): by detected fraud pattern (`rapid_transfers`, `fraud_ring`, `money_laundering`, `novel_pattern`).

**Step 3 — Blend:** `w_i = 0.5 × w1_i + 0.5 × w2_i` for each agent (velocity, geo, graph, behavior).

**Step 4 — Fuse:** `S = Σ(w_i × c_i × r_i) / Σ(w_i × c_i)`

**Step 5 — Disagreement:** population variance ≥ 0.04 forces PASS → OTP.

**Step 6 — Decision:**
- `S < 0.30` → **PASS**
- `0.30 ≤ S ≤ 0.70` → **OTP**
- `S > 0.70` → **BLOCK**

**txn_type mapping:** `synthesis_agent/txn_type_mapping.py` — printed at startup.

**Zero-weight guard:** `synthesise()` raises if an agent reports a verdict but has zero weight in both tables.

---

## 7. Data Layer

| Store | Used by |
|---|---|
| **PostgreSQL** | transactions_raw, customer_profiles, behavior features, synthesis_audit |
| **Redis** | Velocity + Geo sliding windows |
| **Neo4j** | Graph agent (account network) |
| **Kafka** | Async pipeline event bus (`fraud-events` topic) |

---

## 8. Offline Validation + MLflow (`eval/`)

MLflow operates **outside the real-time path** — it never runs during `/evaluate` or Kafka
orchestration. Use it for weekly retraining validation and champion/challenger promotion.

```
eval/
├── metrics.py              # PR-AUC, AUROC, recall@P≥20%, F1
├── mlflow_tracking.py      # experiment setup, champion/challenger gate
├── offline_validation.py   # validate XGBoost, IsoForest, LSTM, rule baseline
├── run_offline_validation.py
└── paths.py
```

**Run from CLI:**

```bash
cd backend
uv sync --group ml
uv run python -m eval.run_offline_validation
```

**Or open the notebook:** `notebooks/offline_validation.ipynb`

**View MLflow UI:**

```bash
cd backend
uv run mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db --port 5000
```

Tracking store: `backend/mlruns/mlflow.db` (SQLite). Experiment:
`fraud-detection-offline-validation`.

| Model | Validation source |
|---|---|
| XGBoost | `datasets_processed/val_scored_xgboost.csv` (80k holdout) |
| Isolation Forest | `transactions_scored_isoforest.csv` joined to train labels |
| LSTM | `models/lstm/metrics.json` (test metrics from training) |
| Rule baseline | `datasets/rule_engine_baseline_predictions.csv` |

Champion/challenger: each validation run compares PR-AUC against the prior best run;
`promotion_gate.decision` is `promote` or `reject`.

---

## 9. Tests

```bash
cd backend
uv run pytest
```

Key suites: `tests/test_behavior_agent.py`, `tests/test_synthesis_agent.py`,
`tests/test_kafka_bus.py`, `tests/test_eval_metrics.py`.

---

## 10. Key Design Decisions

- **Unified app first:** one `uvicorn app.main:app` process runs all agents in-process; parallel fan-out via `asyncio.gather`, not HTTP hops between agents.
- **Kafka for async only:** `/pipeline/submit` publishes; `kafka_bus.orchestrator` consumes and coordinates. `/evaluate` works without Kafka.
- **Graph in fusion:** graph has Table I/II weights; runs in parallel with the other three agents.
- **SHAP per-request on XGBoost:** preloaded explainer, top-k truncation, stored in audit.
- **MLflow off hot path:** offline validation + champion/challenger only; SQLite store at `mlruns/mlflow.db`.
- **Cold-start behavior:** accounts with < 50 txns → LSTM abstains; synthesis renormalizes.

---

## Authors

Manash Lamichhane, Pratik Joshi, Dikshanta Chapagain, Biplov Gautam, Pawan Acharya — Softwarica College, Kathmandu, Nepal
