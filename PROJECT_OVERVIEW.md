# Project Overview — Agentic Multi-Model Fraud Detection System

> Complete developer documentation for the *"Agentic Multi-Model Framework for Real-Time Fraud Detection in Nepal's Digital Payment Ecosystem"* (Softwarica College, 2026).
>
> This document explains **what we are building, how every folder is organized, which ML models we trained and why, how the ML flow works, and how a transaction moves through the whole system.** Read this first if you're new to the project or picking it back up after a break.

---

## Table of Contents

1. [What This Project Is](#1-what-this-project-is)
2. [The Big Picture (Architecture)](#2-the-big-picture-architecture)
3. [The Life of One Transaction](#3-the-life-of-one-transaction)
4. [Repository Folder Structure](#4-repository-folder-structure)
5. [The 6 Microservices (Detailed)](#5-the-6-microservices-detailed)
6. [The Synthesis Math (How Scores Combine)](#6-the-synthesis-math-how-scores-combine)
7. [The Decision & OTP Logic](#7-the-decision--otp-logic)
8. [Machine Learning — Models We Trained](#8-machine-learning--models-we-trained)
9. [The ML Flow (End to End)](#9-the-ml-flow-end-to-end)
10. [The Data Layer (3 Databases)](#10-the-data-layer-3-databases)
11. [Shared Code](#11-shared-code)
12. [Evaluation](#12-evaluation)
13. [Frontend](#13-frontend)
14. [Technology Stack](#14-technology-stack)
15. [How to Run Everything](#15-how-to-run-everything)
16. [Work In Progress](#16-work-in-progress)

---

## 1. What This Project Is

A **real-time fraud detection system** for digital payments in Nepal (eSewa, Khalti, ConnectIPS-style platforms).

For every transaction, the system produces one of three verdicts in **under ~800ms**:

| Verdict | Meaning |
|---|---|
| **PASS** | Transaction looks safe — let it through |
| **OTP** | Suspicious — challenge the user with a one-time code (SMS + email) |
| **BLOCK** | Very likely fraud — stop it |

The core idea: instead of a single monolithic model, we use **three independent "agents"**, each analyzing the transaction from a different angle. Their opinions are then fused by a **synthesis brain** using a confidence-weighted formula. Every decision comes with a **SHAP explanation** for auditability (required for financial regulation).

**The three agents:**

- **Velocity Agent** — "Is this happening too fast / too big?"
- **Geo Agent** — "Is the location and network context suspicious?"
- **Behavior Agent** — "Does this match how this person normally behaves?" (the ML/AI one)

---

## 2. The Big Picture (Architecture)

```
                ┌─────────────┐
   Transaction  │ API Gateway │  (port 8000 — the front door)
   ───────────► │  (ingest)   │
                └──────┬──────┘
                       │ fan-out to 3 agents in parallel
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
  ┌───────────┐  ┌───────────┐  ┌──────────────┐
  │ Velocity  │  │   Geo     │  │  Behavior     │
  │  Agent    │  │  Agent    │  │  Agent        │
  │  :8001    │  │  :8002    │  │  :8003        │
  │ (Postgres)│  │(PG+Neo4j) │  │ (XGBoost +    │
  │           │  │           │  │  IsoForest +  │
  │           │  │           │  │  LSTM + SHAP) │
  └─────┬─────┘  └─────┬─────┘  └──────┬────────┘
        │ risk+conf     │ risk+conf     │ risk+conf+shap
        └──────────────┼───────────────┘
                       ▼
              ┌──────────────────┐
              │ Synthesis Agent   │  :8004
              │ - fraud pattern   │
              │ - 2-layer weights │
              │ - fusion formula  │
              │ - disagreement chk│
              └────────┬──────────┘
                       ▼
              ┌──────────────────┐
              │ Decision/OTP Svc  │  :8005
              │ PASS / OTP / BLOCK│
              │ Sparrow SMS+Email │
              └────────┬──────────┘
                       ▼
              PostgreSQL audit log
              (verdict + SHAP + weights)
```

Everything runs as **independent Dockerized microservices**. A single `docker-compose up` brings up the whole stack (PostgreSQL, Neo4j, Redis, and all 6 services).

Each agent returns **two numbers**:
- **`risk_score`** (0.0–1.0): how dangerous the agent thinks the transaction is.
- **`confidence`** (0.0–1.0): how sure the agent is about its own answer.

The confidence is what makes the system smart — an unsure agent's vote automatically counts for less in the final fusion.

---

## 3. The Life of One Transaction

1. A transaction (`txn_id` + `account_id` + `transaction_type`) hits the **API Gateway** at `POST /evaluate/all`.
2. The gateway calls the **Velocity**, **Geo**, and **Behavior** agents. Each returns `risk_score` + `confidence`.
3. The gateway forwards all three verdicts to the **Synthesis Agent**.
4. Synthesis classifies the likely **fraud pattern**, picks context-aware **weights**, and computes a single **`final_score`** using the confidence-weighted formula. It also runs a **disagreement check**.
5. The gateway sends `final_score` to the **Decision/OTP Service**, which maps it to **PASS / OTP / BLOCK**.
6. If OTP: dual-path codes are sent via **SMS (Sparrow)** and **email**; user has 3 minutes.
7. The full verdict + SHAP explanation + weights are logged to **PostgreSQL** for audit.

The orchestration lives in `backend/services/api-gateway/app/main.py` (`/evaluate/all`).

---

## 4. Repository Folder Structure

```
fraud-detection-system/
├── README.md                 # short project intro (the paper abstract)
├── PROJECT_OVERVIEW.md       # ← this file
├── frontend/                 # Next.js + React dashboard (UI)
└── backend/                  # the whole detection system
    ├── pyproject.toml        # single uv project — all Python deps + dev tools
    ├── run_service.py        # unified entrypoint (run one service by name)
    ├── train.py              # one-shot: build features + train all models
    ├── Dockerfile            # one image; SERVICE env var selects which agent
    ├── docker-compose.yml    # brings up DBs + all 6 services
    │
    ├── services/             # the 6 microservices (agent code only)
    │   ├── api-gateway/           # :8000 public entrypoint + orchestration
    │   ├── velocity-agent/        # :8001 speed/frequency risk
    │   ├── geo-agent/             # :8002 location + graph risk
    │   ├── behavior-agent/        # :8003 ML models + SHAP
    │   ├── synthesis-agent/       # :8004 weight blending + fusion
    │   ├── decision-otp-service/  # :8005 PASS/OTP/BLOCK + OTP interlock
    │   └── fraud-detection-pipeline/  # WIP unified Redis-cached rewrite
    │
    ├── shared/               # code shared by all services
    │   ├── schemas/               # Pydantic contracts (transaction, risk, events)
    │   ├── config/                # central settings (DB URLs, thresholds, keys)
    │   ├── constants/             # service names, Redis channel names
    │   ├── utils/                 # redis pub/sub, serialization
    │   ├── explainability/        # SHAP helpers
    │   └── routers/               # shared /health endpoint factory
    │
    ├── ml/                   # offline machine learning
    │   ├── features/              # clean raw CSVs → feature_table.csv
    │   ├── training/              # train scripts (xgboost, isoforest, lstm, meta)
    │   ├── mlflow/                # experiment-tracking config
    │   └── models/                # trained artifacts (.pkl/.pt) — gitignored
    │
    ├── eval/                 # offline validation (AUROC, precision/recall, F1)
    ├── scripts/              # data loaders (Postgres, Neo4j)
    ├── docker/               # DB init: Postgres SQL, Neo4j cypher, Redis conf
    ├── datasets/             # raw sample data (CSV + JSON)
    ├── datasets_processed/   # feature_table.csv (generated)
    └── tests/                # consolidated pytest suite
```

**Design note:** all six services share **one** `pyproject.toml`, **one** `Dockerfile`, and **one** `run_service.py`. The `SERVICE` environment variable (or a CLI arg) selects which app to boot. This keeps dependency management and deployment simple.

---

## 5. The 6 Microservices (Detailed)

Every service is a **FastAPI** app under `services/<name>/app/`, structured as:

```
services/<name>/app/
├── main.py           # FastAPI app + startup (DB/model loading)
├── routers/          # /evaluate endpoints
└── <domain>.py       # the actual logic (velocity_check, geo_check, synthesis, …)
```

| Service | Port | Reads from | Purpose |
|---|---|---|---|
| api-gateway | 8000 | (other services) | Public entrypoint + pipeline orchestration |
| velocity-agent | 8001 | PostgreSQL | Transaction velocity / frequency risk |
| geo-agent | 8002 | PostgreSQL + Neo4j | Location + graph-context risk |
| behavior-agent | 8003 | PostgreSQL + ML models | XGBoost / IsoForest / LSTM + SHAP |
| synthesis-agent | 8004 | (agent outputs) | Two-layer weight blending + fusion |
| decision-otp-service | 8005 | (final score) | PASS/OTP/BLOCK + dual-path OTP |

### 5a. API Gateway (`services/api-gateway`)
The only public-facing service. Its main endpoint `POST /evaluate/all` chains the whole pipeline: velocity → geo → behavior → synthesis → decision, and returns everything plus total `latency_ms`. It also exposes individual proxy routes (`/evaluate/velocity`, `/evaluate/geo`, etc.) and OTP routes (`/otp/initiate`, `/otp/verify`).

### 5b. Velocity Agent (`services/velocity-agent/app/velocity_check.py`)
Reads pre-computed **velocity snapshots** from PostgreSQL and sums risk points. Signals include:

| Signal | Risk added |
|---|---|
| Amount z-score > 5.0 (way above normal) | +0.40 |
| Amount z-score > 3.5 | +0.30 |
| 3+ transactions in 1 minute | +0.25 |
| Hourly rate above the customer's normal | +0.15 |
| New counterparty (never sent before) | +0.20 |
| Dormant account waking up with big amount | +0.25 |
| Weekend + night | +0.10 |
| 3+ unique recipients in 1 hour | +0.15 |

The final `risk_score` is the capped sum. **Confidence** is based on how well we know the customer (0.50 for unknown/dormant → 0.95 for well-established). Catches **rapid-transfer / account-draining** fraud.

### 5c. Geo Agent (`services/geo-agent/app/geo_check.py`)
The most complex agent. Two parts:

**Part 1 — Location signals (PostgreSQL):**

| Signal | Risk added |
|---|---|
| Impossible travel (two far-apart logins too fast) | +0.50 |
| Rooted/jailbroken device with locale mismatch | +0.40 |
| Tor exit node | +0.30 |
| VPN detected | +0.20 |
| Brand-new device (never seen for this account) | +0.25 |
| Datacenter IP | +0.15 |

**Part 2 — Graph signals (Neo4j Cypher queries):**

- **Shared IP** — account linked to other accounts (`+0.20` cap).
- **Circular flow** — money loops A→B→C→A within 1–3 hops (`+0.25`).
- **Fraud-ring proximity** — shortest path to a known fraud seed account: 1 hop `+0.35`, 2 hops `+0.25`, 3 hops `+0.10`. Catches the **COMM-042 smurfing ring**.

**Resilience:** graph queries have a 5s timeout. If Neo4j is down or slow, the graph part is **skipped** and the agent **lowers its own confidence** instead of failing.

### 5d. Behavior Agent (`services/behavior-agent/app/behavior_check.py`)
The ML brain. On startup it loads the pre-trained models into memory (`model_loader.py`). Per request it:

1. Builds a **feature vector** from the transaction joined with customer, geo, velocity, and device data.
2. Runs **XGBoost** (fraud probability) and **Isolation Forest** (anomaly score).
3. If the account has **50+ transactions** and the LSTM is loaded, also runs the **LSTM** sequence model (otherwise it's skipped — cold-start handling).
4. **Blends** the model scores:
   - No LSTM: `risk = 0.6·XGB + 0.4·IsoForest`
   - With LSTM: `risk = 0.4·XGB + 0.3·IsoForest + 0.3·LSTM`
5. Computes a **SHAP explanation** (top 5 features pushing the score up/down), with a 2s timeout.
6. Sets **confidence** based on feature completeness, LSTM availability, and dormancy (0.40 → 0.95).

### 5e. Synthesis Agent (`services/synthesis-agent/app/synthesis.py`)
Combines the three agent verdicts — see [Section 6](#6-the-synthesis-math-how-scores-combine).

### 5f. Decision / OTP Service (`services/decision-otp-service`)
Maps the final score to a verdict and runs the OTP challenge — see [Section 7](#7-the-decision--otp-logic).

---

## 6. The Synthesis Math (How Scores Combine)

This is the mathematical core (paper §IV-E), in `services/synthesis-agent/`.

**Step 1 — Classify the fraud pattern** (`pattern_classifier.py`). Based on which agent's score dominates:
- Velocity dominates → **Rapid Transfers**
- Geo dominates → **Fraud Ring**
- All three elevated & close together → **Money Laundering**
- Behavior dominates / low agreement → **Novel Pattern**

**Step 2 — Layer 1 weights** (by transaction type, `weights.py` / `risk.py` Table I):

| Transaction type | Velocity | Geo | Behavior |
|---|---|---|---|
| P2P transfer | 0.45 | 0.25 | 0.30 |
| Merchant payment | 0.30 | 0.35 | 0.35 |
| ATM withdrawal | 0.40 | 0.40 | 0.20 |
| Bill payment | 0.25 | 0.30 | 0.45 |

**Step 3 — Layer 2 weights** (by fraud pattern, Table II):

| Fraud pattern | Velocity | Geo | Behavior |
|---|---|---|---|
| Rapid transfers | 0.60 | 0.15 | 0.25 |
| Fraud ring | 0.20 | 0.55 | 0.25 |
| Money laundering | 0.35 | 0.30 | 0.35 |
| Novel pattern | 0.33 | 0.33 | 0.34 |

**Step 4 — Blend the two layers (50/50):**
```
w_i = 0.5 · w1_i(transaction_type) + 0.5 · w2_i(fraud_pattern)
```

**Step 5 — Confidence-weighted fusion (Eq. 2):**
```
final_score = Σ(w_i · r_i · c_i) / Σ(w_i · c_i)      for i ∈ {velocity, geo, behavior}
```
where `r_i` = risk score and `c_i` = confidence of each agent.

**Step 6 — Disagreement check:** if the population variance of the three risk scores ≥ `0.04`, the decision is forced to **OTP** regardless of `final_score` — when the models strongly disagree, a user challenge is the safe move.

---

## 7. The Decision & OTP Logic

**Thresholds** (`decision-otp-service/app/decision.py`):

| Final score | Verdict |
|---|---|
| `< 0.30` | **PASS** |
| `0.30 – 0.70` | **OTP** |
| `> 0.70` | **BLOCK** |

**Dual-path OTP interlock** (`otp_interlock.py`): on an OTP verdict, a 6-digit code is sent to **both** SMS (Sparrow) and email. Rules:
- 3-minute verification window per code.
- **Both** codes must be verified to pass.
- If either code expires or fails → **auto-block**.

This dual-path design defends against **SIM-swap attacks** (attacker who hijacks the phone still can't read the email code). *Note: SMS/email dispatch is currently mocked with print statements — wire real APIs for production.*

---

## 8. Machine Learning — Models We Trained

Four models are trained **offline** and loaded at service startup (never trained per-request). All training lives in `backend/ml/training/` and is tracked with **MLflow**.

| Model | File | Algorithm | Used by | Trained to detect... | Retrain cadence |
|---|---|---|---|---|---|
| **XGBoost** | `xgboost_model.pkl` | Gradient-boosted trees | Behavior Agent | Known/labeled fraud (supervised primary classifier) | Weekly on confirmed labels |
| **Isolation Forest** | `isolation_forest_model.pkl` | Unsupervised anomaly detection | Behavior Agent | Novel anomalies + cold-start (works without labels) | Monthly |
| **LSTM** | `lstm_model.pt` | Recurrent neural net (PyTorch) | Behavior Agent | Sequential/behavioral drift for users with 50+ transactions | Weekly per cohort |
| **Meta-learner** | `meta_learner_model.pkl` | Random Forest | Synthesis Agent | Learns to fuse `(r,c)` agent tuples into a final verdict | Weekly |

### 8a. XGBoost (`train_xgboost.py`)
Supervised binary classifier. Handles class imbalance via `scale_pos_weight`. Config: 200 trees, depth 5, learning rate 0.05, subsample 0.8. Outputs a fraud probability; logs AUROC / precision / recall / F1 to MLflow. This is the Behavior Agent's **primary** signal.

### 8b. Isolation Forest (`train_isolation_forest.py`)
**Unsupervised** — trained on all rows without labels. `contamination` is set to the empirical fraud rate. Because it doesn't need labels, it handles **cold-start** and **novel** fraud that XGBoost has never seen. Evaluation against labels is reference-only.

### 8c. LSTM (`train_lstm.py`)
A 2-layer stacked LSTM (hidden dim 64, dropout 0.2) over per-account transaction **sequences** (window of 64 events; features: `amount_npr`, `hour_of_day`, `is_night`, `amount_ratio`, `vel_z_score_amount`). Each account's sequence is labeled by the fraud flag of its most recent transaction. Padded/masked for short histories. Only meaningful for users with long histories — hence the **50+ transaction gate** at inference.

### 8d. Meta-learner (`train_meta_learner.py`)
A Random Forest that learns the fusion itself, taking the tuple `(r_velocity, r_geo, r_behavior, c_velocity, c_geo, c_behavior, transaction_type)` → fraud. **Currently trained on mocked agent scores** (a placeholder) until the three agents produce held-out evaluation outputs; swap `generate_mock_agent_scores()` for real tuples then.

> **Small-dataset caveat:** the current sample data is ~1,000 rows with ~18 fraud cases. Metrics will be unstable and the LSTM isn't truly meaningful yet. The code is production-shaped — re-validate on the full IEEE-CIS / PaySim / live data.

---

## 9. The ML Flow (End to End)

```
   datasets/*.csv, *.json          (raw sample data)
            │
            ▼
   ml/features/clean_transactions.py     ← clean & normalize each source
            │
            ▼
   ml/features/build_features.py         ← join transactions + customer + geo +
            │                              velocity + device + otp + baseline +
            │                              labels; engineer temporal/amount/
            │                              merchant features; one-hot encode
            ▼
   datasets_processed/feature_table.csv  ← model-ready table (is_fraud last col)
            │
            ▼
   ml/training/run_all_training.py       ← orchestrates all 4 trainers
            ├── train_xgboost.py          → ml/models/xgboost_model.pkl
            ├── train_isolation_forest.py → ml/models/isolation_forest_model.pkl
            ├── train_lstm.py             → ml/models/lstm_model.pt
            └── train_meta_learner.py     → ml/models/meta_learner_model.pkl
            │
            │  (every run logged to MLflow: params, metrics, artifacts)
            ▼
   ml/models/  +  feature_columns.json  +  training_manifest.json
            │
            ▼
   behavior-agent loads .pkl/.pt at startup  (models/ is bind-mounted into the container)
   synthesis-agent loads meta_learner_model.pkl
```

**Feature engineering highlights** (`build_features.py`):
- **Temporal:** `hour_of_day`, `day_of_week`, `is_weekend`, `is_night`.
- **Amount:** `amount_ratio` (amount ÷ customer's monthly average), `is_structuring_amount` (just under reporting thresholds).
- **Merchant:** `is_fraud_merchant` (known bad counterparties).
- **Joins:** geo (`geo_*`), velocity (`vel_*`), device (`dev_*`), OTP logs, rule-engine baseline.
- **Encoding:** transaction type integer-encoded; categorical fields one-hot encoded.
- **Excluded from features:** IDs, timestamps, free text, and label metadata (see `data_utils.EXCLUDE_FROM_FEATURES`).

`feature_columns.json` pins the exact column order so **training and inference stay aligned**. `training_manifest.json` records the last run's metrics + an artifact checklist.

**Run it all with one command:** `python train.py` (features + all training). Or step-by-step: `python -m ml.features.run_pipeline` then `python -m ml.training.run_all_training`.

**MLflow experiments:** `behavior_agent_xgboost`, `behavior_agent_isolation_forest`, `behavior_agent_lstm`, `synthesis_meta_learner`.

---

## 10. The Data Layer (3 Databases)

| Store | What lives here | Init |
|---|---|---|
| **PostgreSQL** | Transactions ledger, customer profiles, device fingerprints, velocity snapshots, geo events, OTP logs, and the **audit trail** (verdict + SHAP + weights per decision) | `docker/postgres/init.sql` |
| **Neo4j** | Account/merchant/device **graph** — shared-IP detection, circular-flow detection (A→B→C→A), and fraud-ring proximity (COMM-042 ring) | `docker/neo4j/constraints.cypher` |
| **Redis** | Fast recency caches (Geo Agent) and **Streams** for inter-service messaging | `docker/redis/redis.conf` |

Loaders under `backend/scripts/` populate Postgres and Neo4j from the sample datasets.

**Sample datasets** (`backend/datasets/`): `transactions_raw.csv`, `customer_profiles.csv`, `geo_events.csv`, `velocity_snapshots.csv`, `device_fingerprints.json`, `account_graph_nodes/edges.csv`, `otp_logs.csv`, `fraud_labels_train.csv`, `rule_engine_baseline_predictions.csv`, `comm042_ring_members.json`, and a held-out `fraud_labels_eval_HIDDEN.csv` (elevated ~3.2% fraud — for final scoring only, never train on it).

---

## 11. Shared Code

`backend/shared/` prevents each service from duplicating logic.

| Path | Purpose |
|---|---|
| `schemas/risk.py` | **The contract.** `AgentVerdict` (risk + confidence + latency), `Layer1Weights`, `Layer2Weights`, `BlendedWeights`, `SynthesisResult`, `SHAPExplanation`, and the `FraudPattern` / `TransactionType` / `DecisionAction` enums |
| `schemas/transaction.py` | Normalized transaction payload |
| `schemas/events.py` | Redis-stream event envelope |
| `config/settings.py` | Central config (DB URLs, Redis host, thresholds, API keys) from env vars |
| `constants/channels.py` | Redis Streams/Pub-Sub channel names |
| `constants/service_names.py` | Canonical service identifiers for logs/audit |
| `utils/redis_pubsub.py` | Thin publish/subscribe wrapper over Redis Streams |
| `utils/serialization.py` | JSON encode/decode (datetime, Decimal, etc.) |
| `explainability/shap_utils.py` | Shared SHAP computation for the Behavior Agent |
| `routers/health.py` | `/health` endpoint factory used by every service |

---

## 12. Evaluation

`backend/eval/` validates the system offline against the synthetic datasets:
- Computes **AUROC, Precision, Recall, F1, PR-AUC** against `fraud_labels_train.csv`.
- Checks the **7 hidden fraud patterns** (structuring amounts, fraud merchants, night-fraud timing, rooted-device locale mismatch, dormancy-break, new-beneficiary fraud, COMM-042 smurfing ring).
- Compares against `rule_engine_baseline_predictions.csv` — the legacy system to beat (AUROC 0.71, FPR 14%, Recall 62%).

Available as `offline_validation.py` and `offline_validation.ipynb`.

---

## 13. Frontend

`frontend/` is a **Next.js 16 + React 19 + TypeScript + Tailwind CSS v4** app — the dashboard/UI layer that will surface transactions, verdicts, and SHAP explanations. Standard Next.js commands: `npm run dev`, `npm run build`, `npm start`.

---

## 14. Technology Stack

| Layer | Technology | Why |
|---|---|---|
| API framework | **FastAPI** (Python) | fast, typed, async web APIs |
| Architecture | **Microservices** | agents scale and deploy independently |
| Containers | **Docker + docker-compose** | one command runs the whole system |
| Relational DB | **PostgreSQL 15** | ledger, profiles, audit trail |
| Graph DB | **Neo4j 5** | fraud-ring / circular-flow detection |
| Cache / messaging | **Redis 7** | recency caches + inter-service Streams |
| ML — supervised | **XGBoost** | primary fraud classifier |
| ML — anomaly | **Isolation Forest** (scikit-learn) | cold-start + novel fraud |
| ML — sequence | **LSTM** (PyTorch) | per-user behavioral drift |
| ML — fusion | **Random Forest** meta-learner | learned score fusion |
| Explainability | **SHAP** | per-decision feature attribution |
| Experiment tracking | **MLflow** | training runs, metrics, artifacts |
| Data processing | **pandas / numpy** | feature engineering |
| Dependency mgmt | **uv** (`pyproject.toml`) | single project for all services + ML |
| Frontend | **Next.js / React / TypeScript / Tailwind** | dashboard UI |

> The paper specifies **Apache Kafka** for event broadcast; this implementation uses **Redis Streams** instead (same fan-out at our scale, no separate Kafka+Zookeeper cluster). The abstraction lives in `shared/utils/redis_pubsub.py` if you need to swap back.

---

## 15. How to Run Everything

**Full stack (databases + all services) via Docker:**
```bash
cd backend
docker compose up --build
```

**Run a single service locally (no Docker):**
```bash
cd backend
uv sync --all-groups
uv run python run_service.py behavior-agent   # or api-gateway, geo-agent, …
```

**Train the models:**
```bash
cd backend
python train.py                          # features + all training → ml/models/
mlflow ui --backend-store-uri mlruns     # view experiment runs
```

**Run tests:**
```bash
cd backend
uv run pytest
```

Every service exposes `GET /health`. Ports: gateway 8000, velocity 8001, geo 8002, behavior 8003, synthesis 8004, decision/OTP 8005.

**Try the full pipeline:**
```bash
curl -X POST http://localhost:8000/evaluate/all \
  -H "Content-Type: application/json" \
  -d '{"txn_id":"TXN-123","account_id":"ACC-456","transaction_type":"p2p_transfer"}'
```

---

## 16. Running & Testing Each Agent (Commands + Example Payloads)

This section is a hands-on cheat sheet: how to start each agent, an **example payload that is known to work**, and how to tell if it's healthy. All examples use real IDs from the sample dataset.

### 16.1 Prerequisites

1. `backend/.env` must define the database connections:
   ```
   DATABASE_URL=postgresql://<user>:<pass>@<host>/<db>      # Postgres (e.g. Neon)
   NEO4J_URI=neo4j://127.0.0.1:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<password>
   NEO4J_DATABASE=fraud-detection                            # IMPORTANT: not the default 'neo4j'
   ```
2. Install deps once: `cd backend && uv sync --all-groups`
3. Data must be loaded into Postgres + Neo4j, and models present in `ml/models/`.

### 16.2 Start the services

**Option A — all at once (Docker):**
```bash
cd backend
docker compose up --build
```

**Option B — one service at a time (local, no Docker).** Run each in its own terminal:
```bash
cd backend
APP_ENV=production uv run python run_service.py velocity-agent        # :8001
APP_ENV=production uv run python run_service.py geo-agent             # :8002
APP_ENV=production uv run python run_service.py behavior-agent        # :8003
APP_ENV=production uv run python run_service.py synthesis-agent       # :8004
APP_ENV=production uv run python run_service.py decision-otp-service  # :8005
```

**The API gateway locally** needs the agent URLs pointed at localhost (they default to Docker hostnames):
```bash
cd backend
VELOCITY_AGENT_URL=http://127.0.0.1:8001 \
GEO_AGENT_URL=http://127.0.0.1:8002 \
BEHAVIOR_AGENT_URL=http://127.0.0.1:8003 \
SYNTHESIS_AGENT_URL=http://127.0.0.1:8004 \
DECISION_OTP_URL=http://127.0.0.1:8005 \
APP_ENV=production uv run python run_service.py api-gateway           # :8000
```

### 16.3 Health checks

```bash
for p in 8000 8001 8002 8003 8004 8005; do
  printf "port %s: " $p; curl -s -m5 -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:$p/health
done
```
Every port should print `HTTP 200`.

### 16.4 Per-agent test payloads (verified working)

**Velocity Agent** — `POST :8001/evaluate`
```bash
curl -s -X POST http://127.0.0.1:8001/evaluate -H 'Content-Type: application/json' \
  -d '{"txn_id":"TXN-20260405-61BEFECB","account_id":"ACC-0000648"}'
```
✅ Working looks like: `risk_score ~0.6`, `confidence 0.95`, with a `breakdown` object. (404 = that txn_id isn't in the DB.)

**Geo Agent** — `POST :8002/evaluate` (use an account near a fraud ring to see the graph fire)
```bash
curl -s -X POST http://127.0.0.1:8002/evaluate -H 'Content-Type: application/json' \
  -d '{"txn_id":"TXN-20260224-6AD4A74B","account_id":"ACC-0000716"}'
```
✅ Working looks like: `risk_score 1.0`, and `fraud_ring_details.is_near_fraud_seed: true` with `nearest_fraud_node_id: "ACC-0011204"`, and non-zero `shared_ip_risk / circular_flow_risk / fraud_ring_proximity_risk`. If all three graph values are `0.0` **and** confidence is capped at 0.6, Neo4j isn't being reached (check `NEO4J_DATABASE`).

**Behavior Agent** — `POST :8003/evaluate`
```bash
curl -s -X POST http://127.0.0.1:8003/evaluate -H 'Content-Type: application/json' \
  -d '{"txn_id":"TXN-20260405-61BEFECB","account_id":"ACC-0000648"}'
```
✅ Working looks like: `models_used: ["xgboost","isolation_forest"]` (LSTM appears only for accounts with 50+ txns), plus `model_scores` and `user_profile`.

**Synthesis Agent** — `POST :8004/evaluate/synthesise` (takes the three agent verdicts)
```bash
curl -s -X POST http://127.0.0.1:8004/evaluate/synthesise -H 'Content-Type: application/json' \
  -d '{"transaction_id":"TXN-1","transaction_type":"p2p_transfer",
       "velocity":{"risk_score":0.7,"confidence":0.9,"latency_ms":12},
       "geo":{"risk_score":0.3,"confidence":0.8,"latency_ms":20},
       "behavior":{"risk_score":0.5,"confidence":0.85,"latency_ms":45}}'
```
✅ Working looks like: `result.final_score`, `result.fraud_pattern`, `result.decision`.

**Decision / OTP Service** — `POST :8005/evaluate/decision`
```bash
curl -s -X POST http://127.0.0.1:8005/evaluate/decision -H 'Content-Type: application/json' \
  -d '{"transaction_id":"TXN-1","final_score":0.5}'
```
✅ Working looks like: `decision: "OTP"` (0.5 is in the OTP band). Try `0.10` → PASS, `0.90` → BLOCK.

**OTP challenge flow** (via gateway):
```bash
# 1) start challenge (codes are printed to the decision-otp-service log by the mock dispatcher)
curl -s -X POST http://127.0.0.1:8000/otp/initiate -H 'Content-Type: application/json' \
  -d '{"transaction_id":"TXN-1","user_id":"ACC-0000716","phone":"9800000000","email":"a@b.com"}'
# 2) verify (wrong codes -> auto BLOCK, correct codes -> both_verified/PASS)
curl -s -X POST http://127.0.0.1:8000/otp/verify -H 'Content-Type: application/json' \
  -d '{"transaction_id":"TXN-1","sms_code":"000000","email_code":"000000"}'
```

### 16.5 Full pipeline (all 5 stages at once) — `POST :8000/evaluate/all`

```bash
curl -s -X POST http://127.0.0.1:8000/evaluate/all -H 'Content-Type: application/json' \
  -d '{"txn_id":"TXN-20260224-6AD4A74B","account_id":"ACC-0000716","transaction_type":"p2p_transfer"}'
```

✅ Returns `agents` (all three verdicts), `synthesis` (final_score + pattern + decision), `decision`, and total `latency_ms`. `transaction_type` must be one of `p2p_transfer | merchant_payment | atm_withdrawal | bill_payment`.

**Reference results (verified against the sample data):**

| Test account | Final score | Pattern | Decision |
|---|---|---|---|
| `ACC-0000716` (near fraud ring) | ~0.68 | fraud_ring | OTP |
| `ACC-0000648` (known-fraud txn) | ~0.56 | rapid_transfers | OTP |
| `ACC-0000728` (normal) | ~0.37 | fraud_ring | OTP/PASS |

### 16.6 Stop all services

```bash
for p in 8000 8001 8002 8003 8004 8005; do kill $(lsof -ti tcp:$p) 2>/dev/null; done
```

---

## 17. Work In Progress

`backend/services/fraud-detection-pipeline/` is a newer **unified rewrite** that merges the agents into a single service with a **Redis cache in front of PostgreSQL** for lower latency. The velocity logic (`app/agents/velocity_agent.py`) first checks Redis (`redis_cache.py`) and falls back to Postgres on a cache miss, loading all velocity snapshots into Redis at startup. This is not yet committed to git and is the actively-developed part of the project.

Other production TODOs:
- Replace mocked Sparrow SMS / email dispatch with real API clients.
- Replace the meta-learner's mocked agent scores with real held-out agent outputs.
- Re-train and re-validate all models on full-scale data (IEEE-CIS / PaySim / live).

---

## Authors

Manash Lamichhane, Pratik Joshi, Dikshanta Chapagain, Biplov Gautam, Pawan Acharya — Softwarica College, Kathmandu, Nepal.
