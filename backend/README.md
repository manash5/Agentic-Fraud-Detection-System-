# Fraud Detection Backend — Implementation Guide
 
> Companion implementation for *"An Agentic Multi-Model Framework for Real-Time Fraud Detection in Nepal's Digital Payment Ecosystem"* (Softwarica College, 2026)
 
This README explains how the codebase maps to the paper's architecture, how to run it locally, and where to find each component if you're picking this project back up after a break.
 
---
 
## 1. The Big Picture
 
A transaction comes in → gets broadcast to **3 parallel agents** → their scores get **fused** by a synthesis layer → a **decision** (PASS / OTP / BLOCK) comes out, with a SHAP explanation logged for audit.
 
```
                ┌─────────────┐
   Transaction  │ API Gateway │
   ───────────► │  (ingest)   │
                └──────┬──────┘
                       │ broadcast (Redis Streams)
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
  ┌───────────┐  ┌───────────┐  ┌──────────────┐
  │ Velocity  │  │   Geo     │  │  Behavior     │
  │  Agent    │  │  Agent    │  │  Agent        │
  │ (Redis)   │  │(Redis+PG  │  │ (XGBoost +    │
  │           │  │ +Neo4j)   │  │ IsoForest +   │
  │           │  │           │  │ LSTM + SHAP)  │
  └─────┬─────┘  └─────┬─────┘  └──────┬────────┘
        │ (risk, conf)  │ (risk, conf)  │ (risk, conf, shap)
        └──────────────┼───────────────┘
                       ▼
              ┌──────────────────┐
              │ Synthesis Agent   │
              │ - pattern classif │
              │ - 2-layer weights │
              │ - fusion formula  │
              │ - disagreement chk│
              └────────┬──────────┘
                       ▼
              ┌──────────────────┐
              │ Decision/OTP Svc  │
              │ PASS / OTP / BLOCK│
              │ Sparrow SMS+Email │
              └────────┬──────────┘
                       ▼
                PostgreSQL audit log
                (verdict + SHAP + weights)
```
 
Everything runs as **independent Dockerized microservices** that talk to each other over Redis Streams. `docker-compose.yml` brings up the whole stack (Postgres, Neo4j, Redis, and all 6 services) with one command.
 
---
 
## 2. Folder Structure

```
backend/
├── pyproject.toml         # single uv project — all deps + dev tools
├── run_service.py         # unified entrypoint for every microservice
├── Dockerfile             # one image, SERVICE env selects which agent
├── docker-compose.yml
├── datasets/              # raw sample CSVs + JSON
├── datasets_processed/    # feature_table.csv (from ml.features.run_pipeline)
├── docker/                # Postgres, Neo4j, Redis init
├── ml/
│   ├── features/          # data cleaning + feature engineering
│   ├── training/          # offline model training scripts
│   ├── mlflow/            # tracking config
│   └── models/            # trained artifacts (gitignored)
├── eval/                  # offline validation script + notebook
├── services/              # agent code only (app/ + domain logic)
├── shared/                # schemas, config, redis utils, health router
└── tests/                 # consolidated pytest suite
```

---

## 3. The Services

Each agent is a FastAPI app under `services/<name>/app/`. All share one dependency file and one Docker image.

```
services/<name>/app/
├── main.py           # FastAPI app
├── routers/          # /evaluate endpoints
└── …                 # domain modules (synthesis, otp, model_loader, etc.)
```

| Service | Port | What it does |
|---|---|---|
| **api-gateway** | 8000 | Public entrypoint (ingestion) |
| **velocity-agent** | 8001 | Transaction velocity risk from Postgres snapshots |
| **geo-agent** | 8002 | Location + graph-context risk (Neo4j) |
| **behavior-agent** | 8003 | XGBoost / IsoForest / LSTM + SHAP |
| **synthesis-agent** | 8004 | Two-layer weight blending + fusion |
| **decision-otp-service** | 8005 | PASS / OTP / BLOCK + dual-path OTP |

**Run one service locally:**

```bash
cd backend
uv sync --all-groups
uv run python run_service.py behavior-agent   # or geo-agent, synthesis-agent, …
```

**Run tests:**

```bash
uv run pytest
```
 
---
 
## 4. Shared Code (`shared/`)
 
| Path | Purpose |
|---|---|
| `shared/config/settings.py` | Central config (DB URLs, Redis host, thresholds, API keys) loaded via env vars. |
| `shared/constants/channels.py` | Names of the Redis Streams/Pub-Sub channels each service publishes/subscribes to. |
| `shared/constants/service_names.py` | Canonical service identifiers used in logs and audit records. |
| `shared/schemas/transaction.py` | Pydantic model for the normalized transaction payload (matches `transactions_raw.csv` columns). |
| `shared/schemas/risk.py` | `AgentVerdict`, `Layer1Weights`, `Layer2Weights`, `SynthesisResult`, `SHAPExplanation` — the contract every agent and the synthesis layer must speak. |
| `shared/schemas/events.py` | Event envelope format used on the Redis stream (transaction + metadata). |
| `shared/utils/redis_pubsub.py` | Thin wrapper for publishing/subscribing to Redis Streams. |
| `shared/utils/serialization.py` | JSON encode/decode helpers (datetime, Decimal, etc.). |
| `shared/explainability/shap_utils.py` | Shared SHAP computation helpers used by `behavior-agent`. |
| `shared/routers/health.py` | Shared `/health` endpoint factory used by all services. |
 
---
 
## 5. Data Layer
 
| Store | What lives here | Init script |
|---|---|---|
| **PostgreSQL** | Transaction ledger, customer profiles, device fingerprints, OTP logs, audit trail (verdict + SHAP + weights per decision), LSTM training corpus | `docker/postgres/init.sql` |
| **Neo4j** | Account/merchant/device graph — used for shared-IP detection, circular flow detection (A→B→C→A), and fraud-ring proximity (e.g. the COMM-042 smurfing ring) | `docker/neo4j/constraints.cypher` |
| **Redis** | Recency caches for the Geo Agent and the Streams used for inter-service messaging | `docker/redis/redis.conf` |
 
To populate Postgres/Neo4j from sample data, use loaders under `scripts/` (TBD) or load from `datasets_processed/` directly.
 
---
 
## 6. The Synthesis Formula (synthesis-agent)
 
This is the mathematical core from §IV-E of the paper. If you're debugging weird scores, this is where to look.
 
**Step 1 — Layer 1 weights** (`app/weights.py`, Table I): pick a row based on `transaction_type`.
 
**Step 2 — Layer 2 weights** (`app/pattern_classifier.py` + `app/weights.py`, Table II): classify the likely fraud pattern (Rapid transfers / Fraud ring / Money laundering / Novel pattern) from the three agents' raw scores, then pick a row.
 
**Step 3 — Blend**:
```
w_i = 0.5 * w1_i(transaction_type) + 0.5 * w2_i(fraud_pattern)
```
 
**Step 4 — Fuse** (`app/synthesis.py`):
```
S = Σ(w_i * c_i * r_i) / Σ(w_i * c_i)     for i in {velocity, geo, behavior}
```
where `r_i` = risk score, `c_i` = confidence score from each agent.
 
**Step 5 — Disagreement check**: if `variance({r_velocity, r_geo, r_behavior})` exceeds a configured threshold, force the decision toward OTP regardless of `S`.
 
**Step 6 — Decision** (`decision-otp-service`):
- `S < 0.30` → PASS
- `0.30 ≤ S ≤ 0.70` → OTP (dual-path SMS + email)
- `S > 0.70` → BLOCK
---
 
## 7. Running Locally

```bash
cd backend
uv sync --all-groups

# Infrastructure + all services
docker compose up --build

# Or run a single service without Docker
uv run python run_service.py api-gateway

# Feature pipeline + training → ml/models/
python train.py
```

Each service exposes `GET /health`.

```bash
uv run pytest
```
 
---
 
## 8. Model Training (`ml/`)
 
The Behavior Agent's models are **not trained at request time** — they're trained offline and loaded into memory at service startup.
 
| Script | Trains | Cadence (per paper) |
|---|---|---|
| `ml/training/train_xgboost.py` | Primary fraud classifier | On IEEE-CIS / PaySim initially, retrained weekly on confirmed labels |
| `ml/training/train_isolation_forest.py` | Cold-start anomaly detector | Monthly, contamination = empirical fraud rate |
| `ml/training/train_lstm.py` | Per-user sequence model (only for users with 50+ tx) | Weekly, per cohort |
| `ml/training/train_meta_learner.py` | Random Forest over `(r1,r2,r3,c1,c2,c3,t)` tuples | Weekly |
 
All runs are tracked via **MLflow** (`mlruns/`). Trained artifacts land in `ml/models/`, bind-mounted into `behavior-agent`. See `ml/README.md`.
 
---
 
## 9. Evaluation (`eval/`)
 
For offline validation against the synthetic datasets (matching the GIBL hackathon evaluation criteria):
 
- Computes **AUROC, Precision/Recall, F1, PR-AUC** against `fraud_labels_train.csv`
- Checks the 7 hidden patterns embedded in the data (structuring amounts, fraud merchants, night-fraud timing, rooted-device locale mismatch, dormancy-break, new-beneficiary fraud, COMM-042 smurfing ring)
- Compares results against `rule_engine_baseline_predictions.csv` (the legacy system this project aims to beat: AUROC 0.71, FPR 14%, Recall 62%)
---
 
## 10. Key Design Decisions / Gotchas
 
- **Redis Streams instead of Kafka**: the paper specifies Kafka for event broadcast. For this implementation we use Redis Streams instead — same fan-out/durability guarantees at our scale, and it lets us avoid running a separate Kafka+Zookeeper cluster locally. If you need to swap back to Kafka, the abstraction lives in `shared/utils/redis_pubsub.py`.
- **Cold-start handling**: if a user has fewer than 50 transactions, `behavior-agent` skips the LSTM and lowers its confidence score accordingly — this is *automatic*, not a special-cased branch in synthesis-agent. The confidence-weighted formula handles the discounting naturally.
- **SHAP is computed per-request** in `behavior-agent`, not as a separate batch job — it's part of the audit record written to Postgres by `decision-otp-service`.
- **`fraud_labels_eval_HIDDEN.csv`** in `datasets/` is a held-out evaluation set with an elevated fraud rate (~3.2%) — don't train on it, only use it for final scoring.
---
 
## 11. Status
 
This is a working implementation of the system architecture described in the accompanying paper. Production validation against live Global IME Bank transaction data is the next phase; current testing uses the synthetic datasets in `datasets/` plus IEEE-CIS and PaySim for initial model training.
 
## Authors
 
Manash Lamichhane, Pratik Joshi, Dikshanta Chapagain, Biplov Gautam, Pawan Acharya — Softwarica College, Kathmandu, Nepal