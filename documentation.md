# Agentic Fraud Detection System: Technical Documentation

Real-time, multi-agent fraud detection for digital payments in Nepal, built for Global IME Bank. A transaction comes in, four specialized risk agents score it in parallel, a synthesis agent fuses their verdicts, and the system returns PASS, OTP, or BLOCK with a full explanation and audit record.

---

## Table of Contents

1. [Understanding the Problem](#1-understanding-the-problem)
2. [Data Pre-processing](#2-data-pre-processing)
3. [Structure of the Codebase](#3-structure-of-the-codebase)
4. [Training the Models](#4-training-the-models)
5. [The Agents](#5-the-agents)
6. [The Synthesis Agent](#6-the-synthesis-agent)
7. [Tools and Technologies](#7-tools-and-technologies)
8. [Deployment Guide](#8-deployment-guide)

---

## 1. Understanding the Problem

Digital payment fraud in Nepal (wallet transfers, QR payments, card, ATM, remittance) rarely announces itself in a single transaction. A fraudulent transfer usually looks normal in isolation; the evidence is spread across different dimensions: how fast the account is transacting, where and from what device, how money flows through the account network, and how the behavior compares to the customer's own history. Legacy rule engines look at one transaction at a time and perform poorly (the baseline rule engine in our data scores AUROC 0.51 with 2% precision). The core problem we solve is combining these scattered signals into one fast, explainable decision: for every transaction, produce a PASS / OTP / BLOCK verdict in real time, with per-agent reasoning and an audit trail suitable for regulatory review.

---

## 2. Data Pre-processing

The raw data is a synthetic Global IME-style dump: 2,000,000 transactions (19 columns) across 50,000 accounts spanning Jan 2025 to May 2026, of which 400,000 are labeled (7,338 fraud, a 1.83% base rate). Supporting files cover customer profiles, per-transaction geo/IP events, velocity snapshots, device fingerprints, an account transfer graph, and OTP logs.

### Cleaning steps

- **Integrity guards and deduplication.** Every notebook hard-asserts expected row counts on load, then dedupes on `txn_id` (2 duplicate rows dropped, leaving 1,999,998).
- **Logic-aware null handling, not blind imputation.** Each null column is treated by what its absence means: `fx_rate` (98.8% null) is dropped since `is_international` already carries the signal; `terminal_id`, `session_id`, `device_id`, and `notes` become boolean presence flags (`has_terminal`, `has_session`, `device_id_missing`, `has_notes`); `merchant_category_code` nulls become an explicit `UNKNOWN` category. Only residual nulls in the XGBoost pipeline get mode/median fills, and those statistics are fitted on the training portion only.
- **Time-based splits, never random.** XGBoost sorts by timestamp and uses the earlier 80% for training and the later 20% (80,000 rows) for validation. The LSTM splits by calendar month: months 1 to 12 train, 13 to 14 validation, 15 to 17 test. This prevents future information leaking into training.
- **Leakage controls.** Only `txn_id` and `is_fraud` are taken from the label file; post-hoc columns (`fraud_confidence`, `confirmed_by`, `financial_loss_npr`, etc.) are dropped. Rule-engine outputs and the `is_fraud_seed` graph flag are excluded as features. Scalers, encoders, and frequency maps are fitted on training data only.
- **Class imbalance handling.** XGBoost uses ADASYN oversampling on the training split only (320,000 rows rebalanced to 627,194 at roughly 50% fraud), validated against the untouched real distribution. The LSTM uses a weighted loss (`pos_weight = 53.5`, the inverse fraud rate) plus train-only negative undersampling (10 legit windows kept per fraud window). The Isolation Forest is unsupervised and needs no rebalancing.
- **Scaling and encoding.** Continuous features get a `StandardScaler` (fit on train); categoricals are one-hot encoded (`txn_type`, `channel`, `auth_method`, `merchant_category_code`, `response_code`, demographics); ordered categories get ordinal maps (`risk_tier`, `kyc_tier`, income band, `auth_strength`).

### Feature engineering

- **Temporal:** `hour_of_day`, `day_of_week`, `is_weekend`, `is_night` (22:00 to 06:00).
- **Amount:** `amount_log = log1p(amount_npr)`, per-account amount z-score, `amount_vs_profile_ratio` (amount over the customer's monthly average, clipped at 100).
- **Customer context:** `account_age_days`, dormancy, beneficiary counts, linked-wallet flags from the profile join.
- **Device:** device age in days (with a fix for the roughly 50% of rows whose `first_seen`/`last_seen` were reversed), timezone mismatch vs `Asia/Kathmandu`, rooted/VPN/Tor/shared-device flags, accounts-per-device.
- **Velocity:** rolling transaction counts (1m to 7d windows), rolling amounts, unique counterparties, new-counterparty flag, amount z-score.
- **Geo:** domestic-IP flag, VPN/Tor/datacenter flags, distance from home district, distance and time delta from the previous transaction, impossible-travel flag.
- **OTP:** sparse left-join of OTP challenge outcomes; absence itself is the signal (`otp_has_event`).

The final assembled matrix is `datasets_processed/feature_table.csv` (88 columns, label last). One important data finding shaped the serving design: several precomputed columns in `velocity_snapshots` and `geo_events` turned out to be synthetic noise rather than true derivations, so the online Velocity and Geo agents recompute their signals live from raw state in Redis instead of trusting those columns.

---

## 3. Structure of the Codebase

```
.
├── README.md                      # Project abstract
├── PROJECT_OVERVIEW.md            # Earlier design document (partially superseded by the code)
├── TECHNICAL_DOCUMENTATION.md     # This file
├── frontend/                      # Next.js 16 dashboard (banking app + fraud ops console)
│   ├── app/                       # App Router routes: /(banking)/* and /admin/*
│   ├── services/, mock/           # API client layer (currently mock-driven), seeded mock DB
│   └── next.config.ts             # /api/* rewrite proxy to the FastAPI backend on :8000
└── backend/
    ├── app/main.py                # Unified FastAPI app: all agents + /evaluate + /pipeline/submit
    ├── agents/                    # One module per agent
    │   ├── velocity_agent.py      # Redis sliding-window burst/spend signals
    │   ├── geo_agent.py           # Travel feasibility + device novelty
    │   ├── graph_agent.py         # Neo4j account-network signals (also a CLI)
    │   ├── behavior_agent.py      # ML ensemble entry point
    │   └── synthesis_agent.py     # Pure fusion math (two-layer weights, decision)
    ├── behavior_agent/            # Behavior serving package: input builders, scorers,
    │                              #   ensemble blend, calibration, artifact loading, API
    ├── synthesis_agent/           # Synthesis HTTP endpoint, txn_type mapping, audit store
    ├── pipeline/                  # Parallel agent fan-out, explanations, audit writer
    ├── kafka_bus/                 # Async path: event envelope, producer, orchestrator consumer
    ├── feature_engineering/       # Config (feature_config.yaml), Redis store, geo math, fit stats
    ├── shared/                    # Pydantic schemas (risk contracts, weights), SHAP utils, config
    ├── eval/                      # Offline validation, metrics, MLflow champion/challenger
    ├── notebooks/                 # Model training: isolation forest, xgboost, lstm, validation
    ├── models/                    # Trained artifacts (XGBoost, IsoForest, LSTM, calibration grids)
    ├── datasets/                  # Raw source CSVs/JSON (transactions, profiles, geo, graph, ...)
    ├── datasets_processed/        # feature_table.csv and scored holdouts
    ├── scripts/                   # Data loaders (e.g. device fingerprints into Postgres)
    └── tests/                     # Pytest suite, one file per agent plus kafka/shap/eval
```

---

## 4. Training the Models

Machine learning lives entirely inside the Behavior Agent. The Velocity, Geo, and Graph agents are deliberately deterministic (config-driven signals and thresholds) so their decisions are fast, transparent, and auditable; the Behavior Agent carries the learned pattern recognition. Three models are trained offline in notebooks and served as preloaded artifacts.

### XGBoost (supervised gradient-boosted trees), the primary classifier

- **Data:** 400,000 labeled transactions joined to customer profiles, 87 features, time-based 80/20 split, ADASYN on train only.
- **Config:** `max_depth=6`, `learning_rate=0.05`, `subsample=0.8`, `colsample_bytree=0.8`, early stopping on validation PR-AUC (stopped at 301 trees).
- **Metrics (80,000-row later-in-time holdout):** PR-AUC 0.118 (about 6.5x the 1.8% base rate), ROC-AUC 0.550. Recommended operating threshold 0.0115 gives recall 0.95. It is the strongest single model on labeled fraud, which is why it anchors the ensemble.

### Isolation Forest (unsupervised anomaly detection)

- **Data:** all 1,999,998 transactions, no labels, 50 transaction-plus-account features, `StandardScaler` on 8 continuous columns.
- **Config:** 200 trees, `contamination=0.02` (an explicit assumption, not a measured rate).
- **Role and metrics:** it exists for cold-start accounts and novel fraud that labeled training cannot cover. Against known labels it scores PR-AUC 0.020 (a diagnostic only; it is not trained to find labeled fraud, it ranks statistical outliers).

### Two-branch LSTM (sequence model, PyTorch)

- **Architecture:** a sequential branch (LSTM, 79 features per timestep, hidden size 64, packed sequences over left-padded windows of the account's last 30 transactions) concatenated with a static branch (32 profile/graph features through a dense layer), fused by a small head into one fraud logit. 44,449 trainable parameters.
- **Labeling:** each window is labeled by the `is_fraud` flag of its final transaction; the 1.6M unlabeled transactions serve as sequence context only.
- **Metrics (71,207 test windows):** PR-AUC 0.103, AUROC 0.532, versus the rule-engine baseline at AUROC 0.511 on the same window. It only fires for accounts with at least 50 transactions of history (about 6.9% of accounts), where sequence patterns are meaningful.

### Honest context on the numbers

All three models converge to AUROC of roughly 0.53 to 0.55. Analysis in the notebooks shows this is a property of the dataset, not a pipeline defect: about 90% of the synthetic fraud is statistically indistinguishable from legitimate traffic at the single-transaction level. The discriminative signal is concentrated in velocity, geo, and graph patterns, which is exactly why the system is agentic rather than a single model. The score head is still useful: the top 0.1% of XGBoost scores are essentially 100% precision.

### Calibration and validation

Raw model outputs live on incomparable scales (XGBoost probabilities cluster near 0.01, anomaly scores are unbounded, LSTM probabilities are inflated by the weighted loss). `behavior_agent/build_calibration.py` therefore builds a 1001-point percentile grid per model from reference score distributions, so each raw score maps to "how extreme is this transaction for this model" in [0,1]. The ensemble blends these calibrated scores.

`eval/` provides offline validation (PR-AUC as the primary metric, plus AUROC, best F1, and recall at precision >= 20%) against the rule-engine baseline, logged to MLflow with a champion/challenger promotion gate: a retrained model is only promoted if its PR-AUC beats the current champion.

---

## 5. The Agents

Every agent returns two numbers: a `risk_score` in [0,1] and a `confidence` in [0,1] that tells the synthesis layer how much to trust that score (low history means low confidence). An agent whose datastore is down returns an explicit error and is simply omitted from fusion; agents never fabricate scores.

### Velocity Agent (is this happening too fast or too big?)

Runs entirely on Redis in 1 to 2 ms. Each transaction is recorded into per-account sliding windows (2-minute and 1-hour sorted sets), and five signals are computed against a nightly-cached per-account baseline hash: live window counts vs historical means, amount vs the account's average (smooth ratio), the explicit 5x-plus amount spike, balance integrity (stubbed until a balance feed exists; its weight renormalizes away), and transaction-type rarity for that account. The weighted sum of available signals is the risk score. Confidence ramps linearly with the account's observation count up to 50 transactions, so cold-start accounts vote weakly. All windows, weights, and thresholds live in `feature_config.yaml`.

### Geo Agent (does the location and device make sense?)

Two signals blended 50/50, Redis-first with a Postgres fallback. Travel feasibility computes the haversine distance from the account's last known location and the implied speed; speeds approaching the 900 km/h plausibility ceiling score on a gradient (not a hard boolean), and real distance covered in no elapsed time scores 1.0. Device novelty checks the device against the account's known-device set in Redis; unknown devices get a base score pushed higher by fingerprint enrichment from Postgres (rooted/jailbroken, shared device, seen on many accounts). The agent then records the current location and device so the next transaction has fresh state. Confidence ramps over 20 prior geo events.

### Graph Agent (is this account embedded in a fraud network?)

Walks the account graph in Neo4j (50k nodes, 500k sampled SENT edges) with parameterized Cypher and scores seven additive signals: smurfing fan-out (many distinct recipients in one day), collector-level fan-in, structuring (transfers hugging NRB reporting thresholds), layering (within-24h reciprocal transfers), mule shape (high in-degree with near-zero net balance), proximity to the known COMM-042 ring collector (direct or within 2 hops), and the account's own risk tier / fraud-seed identity. Each fired signal adds its configured weight, the sum is clipped to [0,1], and every fired rule is returned as a human-readable reason string. It also ships as a CLI (`score`, `scan`, `scan-in`, `demo`) for investigations.

### Behavior Agent (does this match how this person normally behaves?)

The ML brain. All three trained models are preloaded at startup; a request then does only database reads and inference (about 10 to 30 ms warm). Per transaction it:

1. Builds three separate feature vectors, one per model, each reproducing its training notebook exactly (parity against saved notebook outputs verified to float precision). Inputs come from Postgres: transactions, customer profiles, device fingerprints, velocity snapshots, geo events, OTP logs, and graph node aggregates.
2. Scores XGBoost and Isolation Forest always, and the LSTM only when the account has at least 50 transactions of history (below that it abstains rather than guessing).
3. Maps each raw score through its percentile calibration grid, then blends with history-dependent weights: cold start (under 10 txns) leans on the Isolation Forest (0.35/0.65), medium history leans on XGBoost (0.60/0.40), rich history brings in the LSTM (0.45/0.20/0.35). Weights renormalize over whichever models contributed.
4. Computes confidence as coverage times agreement: how many models fired (0.50/0.75/1.00) scaled down by the spread between their scores.
5. Attaches a SHAP explanation (top 10 signed feature contributions from XGBoost, using a TreeExplainer built once at load time).

A model whose inputs are missing for a transaction is marked as not contributing, with the reason surfaced; if no model can score, the endpoint returns 422 rather than a fake score.

---

## 6. The Synthesis Agent

The synthesis agent is pure math with no datastore reads: it consumes the risk/confidence verdicts from whichever agents reported and produces the final decision. The pipeline:

1. **Classify the fraud pattern** from the shape of the risk scores: three or more elevated and tightly clustered scores mean money laundering; graph or geo loudest means fraud ring; velocity loudest means rapid transfers; otherwise novel pattern.
2. **Select Layer 1 weights** by transaction type (raw dataset types like `ESEWA_P2P` are first mapped to four categories through an explicit, logged mapping table):

   | Transaction type | Velocity | Geo | Graph | Behavior |
   |---|---|---|---|---|
   | p2p_transfer | 0.35 | 0.20 | 0.20 | 0.25 |
   | merchant_payment | 0.25 | 0.25 | 0.25 | 0.25 |
   | atm_withdrawal | 0.30 | 0.30 | 0.25 | 0.15 |
   | bill_payment | 0.20 | 0.25 | 0.20 | 0.35 |

3. **Select Layer 2 weights** by the detected fraud pattern:

   | Fraud pattern | Velocity | Geo | Graph | Behavior |
   |---|---|---|---|---|
   | rapid_transfers | 0.50 | 0.10 | 0.15 | 0.25 |
   | fraud_ring | 0.15 | 0.30 | 0.40 | 0.15 |
   | money_laundering | 0.25 | 0.25 | 0.25 | 0.25 |
   | novel_pattern | 0.25 | 0.25 | 0.20 | 0.30 |

4. **Blend** the two layers equally: `w_i = 0.5 * w1_i + 0.5 * w2_i`.
5. **Fuse** with confidence weighting: `S = sum(w_i * c_i * r_i) / sum(w_i * c_i)`. An agent that is unsure counts for less, and an agent that is absent contributes nothing; its weight mass redistributes automatically through the denominator.
6. **Disagreement check:** if the population variance of the risk scores is at least 0.04 and the score-based verdict was PASS, the decision escalates to OTP. Strong disagreement between agents means a user challenge is the safe move. A confident BLOCK is never downgraded.
7. **Decide:** S below 0.30 is PASS, 0.30 to 0.70 is OTP, above 0.70 is BLOCK.

Before responding, the full record (input verdicts, both weight layers, blended weights, pattern, disagreement, decision, per-agent explanations, and the SHAP block) is written to the Postgres `synthesis_audit` table, giving every decision a reviewable trail.

---

## 7. Tools and Technologies

- **FastAPI + asyncio.** One unified app hosts every agent; the `/evaluate` endpoint fans out to all four agents concurrently with `asyncio.gather`, so total latency is the slowest agent, not the sum. Each agent also has its own endpoint for debugging.
- **Redis.** The hot path for the Velocity and Geo agents: sorted sets implement the sliding transaction windows, hashes cache per-account baselines and last-known locations, sets track known devices. Chosen because these lookups must complete in single-digit milliseconds; failures surface immediately (no retries, short socket timeouts) so a down cache degrades gracefully instead of stalling requests.
- **PostgreSQL.** System of record: raw transactions, reference tables (customer profiles, device fingerprints, OTP logs, graph node aggregates) queried by the Behavior Agent through asyncpg pools, plus the `synthesis_audit` decision trail. Also the Geo Agent's fallback when Redis misses.
- **Neo4j.** Powers the Graph Agent: multi-hop questions like "does this account reach the ring collector within two hops" are single Cypher queries with EXISTS subqueries, which relational SQL handles poorly.
- **Kafka (aiokafka).** The optional async path: `/pipeline/submit` publishes to a single `fraud-events` topic keyed by transaction id (guaranteeing per-transaction event ordering), and a standalone orchestrator consumes, runs the same fan-out plus fusion, and publishes staged events ending in `final_decision`. The synchronous `/evaluate` path works without Kafka.
- **XGBoost, scikit-learn, PyTorch.** The three Behavior models. XGBoost artifacts are loaded before torch is imported anywhere (loading them after torch segfaults on macOS due to duplicate OpenMP runtimes; torch imports are deferred for this reason).
- **imbalanced-learn (ADASYN).** Train-time oversampling for XGBoost against the 1.83% base rate.
- **SHAP.** Per-decision explainability: a TreeExplainer is built once at model load and produces top-10 signed feature contributions per request, stored with the audit record.
- **MLflow.** Offline only, never in the request path: tracks validation runs and gates retrained models behind a PR-AUC champion/challenger comparison.
- **uv + a single pyproject.toml.** One dependency tree for agents, training, and tests keeps environments reproducible.
- **pytest.** Per-agent suites covering signal math, cold-start behavior, parity with training notebooks, failure semantics, and endpoint shapes; fakes for Redis mean most tests run without live infrastructure.
- **Next.js 16 + React 19 (frontend).** A banking app plus fraud ops console (live feed, per-transaction agent verdict drill-down with SHAP, system health). It currently runs on a deterministic mock data layer; a rewrite proxy (`/api/*` to the backend on port 8000) is already in place for wiring real endpoints.

---

## 8. Deployment Guide

This section is written from the adopting organization's point of view — Global IME Bank or any other institution integrating the system. It covers three things: how the already-trained models are loaded and served, what the API expects and returns (the integration contract), and what data and infrastructure the organization must supply. How the models are *trained* is Section 4 and is not repeated here; deployment consumes trained artifacts, it does not produce them.

### 8.1 Runtime shape: Docker containers

The system ships as containers wired together by one `docker-compose.yml` at the repository root: the backend image (FastAPI app, all four agents, model artifacts baked in, built from `backend/Dockerfile`), plus `postgres:16`, `redis:7`, and `neo4j:5` pre-configured to talk to each other — including the `fraud-detection` Neo4j database name the code expects. Kafka with the orchestrator (`--profile kafka`) and the ops dashboard (`--profile dashboard`) are opt-in profiles.

```bash
git clone <repository-url> && cd AGENT-FRAUD-DETECTION
docker compose up -d --build
curl -s http://localhost:8000/health
```

Secrets go in a `.env` next to `docker-compose.yml` (`POSTGRES_PASSWORD`, `NEO4J_PASSWORD`); all service-to-service wiring (`DATABASE_URL`, `FRAUD_DB_DSN`, `FRAUD_REDIS_HOST`/`FRAUD_REDIS_PORT`, `NEO4J_URI`, `NEO4J_DATABASE`, `FRAUD_KAFKA_BOOTSTRAP`) is set in the compose file. For a bank this buys three things: everything runs on-premise inside its own network (transaction data and the audit trail never leave; images also `docker save`/`docker load` into air-gapped environments), the image that passed UAT is byte-for-byte the production image (upgrades and rollbacks are tag changes), and the stateless backend scales horizontally (`--scale backend=4`, or the same images under Kubernetes) while the bundled datastore containers can be swapped for the bank's managed Postgres/Redis/Neo4j by changing environment variables only. Docker is the packaging, not the integration — the actual work of adoption is in 8.2–8.4 below, and every `docker compose exec backend` command there runs identically outside Docker by dropping the prefix and pointing `backend/.env` at your own hosts.

### 8.2 How the trained models are loaded

The Behavior Agent serves three pre-trained models. All artifact paths are declared in `behavior_agent/config.yaml` (never hard-coded), relative to `backend/`, and every file below must exist for the agent to come up:

| Model | Required files | What each is |
|---|---|---|
| XGBoost | `models/xgboost_behavior.json`, `models/model_feature_list.json` | booster; manifest with `feature_columns` and `recommended_threshold` |
| Isolation Forest | `models/isolation_forest_transactions_raw.joblib`, `models/isolation_forest_scaler.joblib`, `models/isolation_forest_feature_list.json` | model; fitted StandardScaler; manifest with feature and scaled-column lists |
| LSTM | `models/lstm/lstm_two_branch.pt`, `models/lstm/manifest.json`, `models/lstm/preprocessors.joblib` | checkpoint (architecture + weights); manifest with sequence features and `seq_len_N`; fitted scalers/encoders |
| Calibration | `models/behavior_score_calibration.json` | 1001-point percentile grid per model (rebuild with `behavior_agent.build_calibration` whenever a model is replaced) |

Note: the git repository carries only the JSON manifests; the binary artifacts (`.joblib`, `.pt`) must be placed into `backend/models/` before building the backend image — either your own retrained ones (Section 4) or the artifacts handed over with the system.

Loading happens **once, at FastAPI startup** (`behavior_agent/artifacts.py: load_bundle`), so a request does only database reads and inference (~10–30 ms warm, 100 ms budget). The loader is deliberately strict:

- Each missing file raises a named `ModelMissingError`, and `/health` reports exactly which artifact is absent — a missing model is distinguishable from a down database.
- Consistency is verified at load: the Isolation Forest scaler must have been fitted on exactly the manifest's scaled columns, the LSTM checkpoint's feature count must match its manifest, and the calibration file must contain a grid for all three models.
- XGBoost is loaded **before** torch is imported (loading it after torch segfaults on macOS from duplicate OpenMP runtimes) — keep this order if you modify startup.
- SHAP `TreeExplainer`s are built once here, not per request.

If artifacts are missing the app still starts: the Behavior endpoint returns 503, the other three agents keep serving, and synthesis renormalizes its weights over the agents that reported. Swapping in a retrained model is: place new artifacts, rerun `build_calibration`, rebuild the backend image, redeploy — a rolling image replacement, never a change to a live process.

### 8.3 API contract: endpoints and schemas

One FastAPI app on port 8000. The organization's payment switch integrates against `/evaluate` (or `/pipeline/submit` for the async path); the per-agent endpoints exist for debugging and investigations.

| Endpoint | Purpose |
|---|---|
| `POST /evaluate` | Full pipeline: parallel agent fan-out + synthesis, one round-trip. **The integration point.** |
| `POST /pipeline/submit` | Same body, published to Kafka; returns 202 immediately (async path) |
| `GET /health` | Liveness + per-agent backing-store probe |
| `POST /velocity/evaluate`, `POST /geo/evaluate`, `POST /graph/evaluate`, `POST /agents/behavior/evaluate`, `POST /agents/synthesis/evaluate` | Individual agents, for debugging |

**`POST /evaluate` request** — what the organization sends per transaction:

| Field | Type | Required | Notes |
|---|---|---|---|
| `txn_id` | string | yes | Unique transaction id |
| `account_id` | string | yes | Account being scored |
| `txn_type` | string | yes | **Your raw code** (e.g. `ESEWA_P2P`, `ATM_WITHDRAWAL`) — mapped internally to the four weight categories via `synthesis_agent/txn_type_mapping.py`; extend that table with your institution's codes |
| `amount` | float ≥ 0 | no (default 0) | Amount in `currency` |
| `currency` | string | no (default `NPR`) | |
| `timestamp` | ISO-8601 datetime | no (defaults to now UTC) | Event time |
| `device_id` | string | no | Needed for the Geo Agent's device-novelty signal |
| `latitude`, `longitude` | float | no | Needed for the Geo Agent's travel-feasibility signal |

Optional fields degrade gracefully: no device/coordinates means the Geo Agent is skipped and its weight redistributes; the decision still returns.

**`POST /evaluate` response:**

```json
{
  "txn_id": "...",
  "decision": "PASS | OTP | BLOCK",
  "final_score": 0.42,
  "fraud_pattern": "rapid_transfers | fraud_ring | money_laundering | novel_pattern",
  "disagreement_score": 0.01,
  "otp_forced_by_disagreement": false,
  "agents_used": ["velocity", "geo", "graph", "behavior"],
  "txn_type_mapped": "atm_withdrawal",
  "weights_applied": {"velocity": 0.4, "geo": 0.2, "graph": 0.2, "behavior": 0.2},
  "agent_outcomes": { "...per-agent risk/confidence or error..." },
  "explanations": { "...human-readable per-agent reasons..." },
  "shap": { "...top-10 signed feature contributions..." },
  "total_latency_ms": 45.1,
  "parallel_agents_ms": 41.9
}
```

Every decision is also written to the Postgres `synthesis_audit` table (created automatically at startup) with both weight layers, all verdicts, and the SHAP block — the regulatory trail.

**Per-agent debug endpoints** take narrower bodies: Velocity takes a `TransactionEvent` (`transaction_id`, `user_id`, `amount`, `timestamp`, optional `txn_type`/`device_id`/`ip_address`/coordinates); Geo takes `txn_id`, `account_id`, `device_id`, `latitude`, `longitude`; Graph takes `account_id` only; Behavior takes `account_id` + `txn_id`; Synthesis takes `txn_id`, `txn_type`, and the collected `{risk_score, confidence}` per agent (velocity and geo required, graph and behavior optional).

**Error semantics the caller must handle:** an agent whose backing store is down returns 503 from its own endpoint, and inside `/evaluate` it is simply omitted from fusion — agents never fabricate scores. 404 means an unknown account (Graph) or a `txn_id` not present in `transactions_raw` (Behavior). 422 from Behavior means no model could score that transaction (it refuses to invent a number). `/pipeline/submit` returns 503 if Kafka is unreachable, 202 `{transaction_id, status: "accepted", topic: "fraud-events"}` otherwise.

### 8.4 What the organization must provide

The agents read the organization's data from three stores. This is the substance of an integration project:

**PostgreSQL** (default database `fraud_detection_global`) — the shapes match the files in `backend/datasets/`; loaders for the reference tables are included:

| Table | Read by | Purpose |
|---|---|---|
| `transactions_raw` | Behavior | Account history + the transaction row being scored. **The transaction must be ingested here before `/evaluate` is called with its `txn_id`, or the Behavior Agent abstains (404)** — feed it from your core-banking / switch stream |
| `customer_profiles` | Behavior | KYC tier, income band, account age, beneficiary counts |
| `device_fingerprints` | Behavior, Geo | Device age, rooted/VPN/Tor flags, accounts-per-device (loader: `scripts/load_device_fingerprints.py`) |
| `geo_events` | Behavior, Geo | Location history; Geo's fallback when Redis misses |
| `velocity_snapshots` | Behavior | Historical velocity features |
| `otp_logs` | Behavior | OTP challenge outcomes (loader: `behavior_agent.load_reference_tables`, which also loads `customer_profiles` and `account_graph_nodes`) |
| `account_graph_nodes` | Behavior | Per-account graph aggregates |
| `synthesis_audit` | written by system | Decision trail; created automatically |

Index the transactional tables on account id and timestamp.

**Neo4j** — a database named `fraud-detection` holding the transfer network: `(:Account {id, risk_tier, degree_in, degree_out, total_received_npr, total_sent_npr, is_fraud_seed})` nodes connected by `[:SENT {txn_id, amount_npr, timestamp, txn_type, is_structuring_amount, within_24h_reciprocal}]` relationships, built from your transfer history. The compose file mounts `backend/datasets/` at `/import` for `LOAD CSV`. Verify with `docker compose exec backend uv run python -m agents.graph_agent demo`.

**Redis** — warm state the organization refreshes on a schedule: per-account baseline hashes (`account_baseline:<account_id>`: count means, amount mean/std, observation counts) and type-distribution hashes, written from your history via the `write_baseline` / `write_type_dist` helpers in `agents/velocity_agent.py`, refreshed nightly. Unbaselined accounts still score — they are treated as cold-start with reduced confidence, so the system works from day one and sharpens as baselines fill in.

**Institution-specific configuration** — two files: `synthesis_agent/txn_type_mapping.py` (map your transaction-type codes to the four weight categories; unmapped codes fall back to defaults but should be explicit) and `feature_engineering/feature_config.yaml` (every threshold, window, and weight — tune to your risk appetite; no detection logic is hard-coded).

### 8.5 Verify

```bash
curl -s http://localhost:8000/health     # every agent should report "ok"
curl -s -X POST http://localhost:8000/evaluate -H 'Content-Type: application/json' -d '{
  "txn_id": "TXN-20260528-32895DA9",
  "account_id": "ACC-1002022",
  "txn_type": "ATM_WITHDRAWAL",
  "amount": 543.61,
  "device_id": "DEV-4C4CFB",
  "latitude": 27.7172,
  "longitude": 85.3240
}'
docker compose exec backend uv run pytest                                  # test suite
docker compose exec backend uv run python -m eval.run_offline_validation   # metrics vs rule baseline
```

Ongoing operation: retrain on your labeled data on a regular cadence (Section 4), let the MLflow champion/challenger gate decide promotion, and roll the new artifacts out as a rebuilt backend image per 8.2.

---

*Authors: Manash Lamichhane, Pratik Joshi, Dikshanta Chapagain, Biplov Gautam, Pawan Acharya. Softwarica College, Kathmandu, Nepal.*
