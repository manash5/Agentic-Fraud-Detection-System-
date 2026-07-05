# Agentic Fraud Detection System: Technical Documentation

Real-time, multi-agent fraud detection for digital payments in Nepal, built for Global IME Bank. A transaction comes in, four specialized risk agents score it in parallel, a synthesis agent fuses their verdicts, and the system returns PASS, OTP, or BLOCK with a full explanation and audit record. A Next.js banking app + fraud-operations console sits in front of it, wired to the live pipeline.

---

## Table of Contents

1. [Understanding the Problem](#1-understanding-the-problem)
2. [Architecture & System](#2-architecture--system)
3. [Data Pre-processing](#3-data-pre-processing)
4. [Structure of the Codebase](#4-structure-of-the-codebase)
5. [Training the Models](#5-training-the-models)
6. [The Agents](#6-the-agents)
7. [The Synthesis Agent](#7-the-synthesis-agent)
8. [Model Metrics & Key Parameters](#8-model-metrics--key-parameters)
9. [Tools and Technologies](#9-tools-and-technologies)
10. [API & Reference](#10-api--reference)
11. [Deployment & Quick Start](#11-deployment--quick-start)

---

## 1. Understanding the Problem

Digital payment fraud in Nepal (wallet transfers, QR payments, card, ATM, remittance) rarely announces itself in a single transaction. A fraudulent transfer usually looks normal in isolation; the evidence is spread across different dimensions: how fast the account is transacting, where and from what device, how money flows through the account network, and how the behavior compares to the customer's own history. Legacy rule engines look at one transaction at a time and perform poorly (the baseline rule engine in our data scores AUROC 0.51 with 2% precision). The core problem we solve is combining these scattered signals into one fast, explainable decision: for every transaction, produce a PASS / OTP / BLOCK verdict in real time, with per-agent reasoning and an audit trail suitable for regulatory review.

---

## 2. Architecture & System

### System Design

The system is a **unified FastAPI application** that hosts every agent in one process, fronted by a **Next.js** app. Four datastores back the agents, and an optional Kafka event bus provides an asynchronous processing path.

```
┌──────────────┐        /api/* (server-side proxy)        ┌───────────────────────────┐
│  Next.js app │ ───────────────────────────────────────▶ │  FastAPI (app.main:app)    │
│  :3000       │ ◀─── JSON (Transaction, FraudResult) ──── │  · agents (in-process)     │
│  banking +   │                                           │  · auth / banking / txn    │
│  ops console │                                           │  · OTP · admin · verdicts  │
└──────────────┘                                           │  · state projector (task)  │
                                                           └───────────┬────────────────┘
                            in-process fan-out (asyncio.gather)         │
              ┌───────────────┬───────────────┬───────────────┐        │ publish/consume
              ▼               ▼               ▼               ▼        ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐  ┌─────────┐
        │ Velocity │   │   Geo    │   │  Graph   │   │ Behavior │  │  Kafka  │
        │  (Redis) │   │(Redis+PG)│   │ (Neo4j)  │   │(PG + ML) │  │fraud-evt│
        └──────────┘   └──────────┘   └──────────┘   └──────────┘  └────┬────┘
              └───────────────┴──── Synthesis (pure math) ──────┘        │
                                     │                                   │
                             ┌───────▼────────┐                 ┌────────▼─────────┐
                             │ synthesis_audit │                 │  orchestrator    │
                             │   (Postgres)    │                 │ (separate proc.) │
                             └─────────────────┘                 └──────────────────┘
```

Two ways a transaction gets scored:

- **Synchronous** — `POST /evaluate` fans out to all four agents concurrently with `asyncio.gather`, fuses, writes the audit row, and returns the decision in one HTTP round-trip. Total latency is the slowest agent, not the sum.
- **Asynchronous** — `POST /transfer` (or `POST /pipeline/submit`) persists the transaction and publishes a `transaction_received` event to Kafka. A **standalone orchestrator** process consumes it, runs the same fan-out + fusion, and publishes staged events (`velocity_completed` … `final_decision`). A **state projector** running inside the API process consumes those events and projects live progress into Redis, which the frontend polls.

Agents are **independent and degradable**: each returns a `risk_score` and a `confidence`, and an agent whose datastore is down returns an explicit non-`ok` status and is simply omitted from fusion — scores are never fabricated. The synthesis weights renormalize over whoever reported.

### Data Flow

The end-to-end lifecycle of a customer transfer (the live pipeline):

1. **Login** — the user authenticates; the backend issues an opaque session token stored in Redis (`session:{token}`), and caches the customer profile.
2. **Submit** — the frontend `POST /transfer`. The backend validates ownership + balance, then persists the transaction to `transactions_raw` (flagged `source='live'`) plus companion `velocity_snapshots` and `geo_events` rows so the agents have the data they read, and creates a pending `app_transactions` ledger row.
3. **Publish** — it writes an initial workflow state to Redis (`txn:state:{txn_id}`) and publishes `transaction_received` to the Kafka `fraud-events` topic (keyed by `txn_id` for per-transaction ordering).
4. **Score** — the orchestrator consumes the event, runs Velocity + Geo + Graph + Behavior in parallel, publishes a `*_completed` event per agent, fuses via the synthesis agent, publishes `synthesis_completed` then `final_decision`, and writes the `synthesis_audit` row.
5. **Project** — the in-process state projector consumes each event and updates `txn:state:{txn_id}`, so the frontend (polling `GET /transfers/{id}/status` every second) shows agent-by-agent progress live.
6. **Decide** — on `final_decision`: **PASS** → debit + complete; **OTP** → generate a 6-digit code, store it hashed in Redis (`otp:{txn_id}`, 180s TTL), send it via EasySendSMS, mark the txn `otp_required`; **BLOCK** → mark blocked.
7. **Complete** — for OTP, the user submits the code (`POST /otp/verify`); on success the transaction completes (debit) exactly like the PASS path.
8. **Log** — every evaluated transaction is appended to `backend/transactions_logs.json` (agent scores, flags, weights, decision, baseline comparison).

### Design Decisions

- **Agentic, not a single model.** ~90% of the synthetic fraud is statistically indistinguishable from legitimate traffic at the single-transaction level (all three ML models converge near AUROC 0.53). The discriminative signal lives in velocity, geo, and graph *patterns*, so the system splits the problem across specialized agents rather than betting on one classifier.
- **Deterministic agents + one learned agent.** Velocity, Geo, and Graph are config-driven signal functions (fast, transparent, auditable). Only the Behavior Agent carries learned pattern recognition (XGBoost + Isolation Forest + LSTM). This keeps most of the decision explainable by construction.
- **Confidence-weighted fusion with graceful degradation.** Every agent reports a confidence; the synthesis fuses `S = Σ(wᵢ·cᵢ·rᵢ)/Σ(wᵢ·cᵢ)`, so a cold-start or absent agent contributes proportionally less (or nothing) without breaking the decision.
- **Recompute live signals, don't trust precomputed columns.** Several columns in `velocity_snapshots`/`geo_events` turned out to be synthetic noise, so the online Velocity and Geo agents recompute their signals from raw Redis state instead of reading those columns.
- **Two processing paths.** The synchronous `/evaluate` is the simple request/response; the Kafka path decouples ingestion from scoring and gives the frontend a live, staged progress feed. The orchestrator is a *separate consumer group* from the state projector, so scoring and UI-projection never contend.
- **Load order matters.** XGBoost artifacts are imported before torch anywhere (loading them after torch segfaults on macOS due to duplicate OpenMP runtimes), so torch imports are deferred throughout.
- **Backend is the single source of truth.** All business logic (auth, OTP lifecycle, decisions, thresholds) lives in the backend; the frontend only renders. Decision thresholds are runtime-tunable from the admin console and applied live in both the API and orchestrator processes.

### System Diagram

*(Reserved — the team will insert the detailed architecture / sequence diagram here.)*

```
[ insert flow diagram ]
```

---

## 3. Data Pre-processing

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

## 4. Structure of the Codebase

```
.
├── README.md                      # This file (technical documentation)
├── PROJECT_OVERVIEW.md            # Earlier design document (partially superseded by the code)
├── docker/                        # Containerized full stack (compose, Dockerfiles, init.sql)
├── frontend/                      # Next.js 16 dashboard (banking app + fraud ops console)
│   ├── app/                       # App Router routes: /(banking)/* and /admin/*
│   ├── features/, components/     # Domain views + shared UI
│   ├── services/                  # Real HTTP client layer (fetch → /api proxy → backend)
│   ├── hooks/, lib/               # React Query hooks, auth store, mappers, constants
│   └── next.config.ts             # /api/* rewrite proxy to the FastAPI backend
└── backend/
    ├── app/
    │   ├── main.py                # Unified FastAPI app: agents + /evaluate + /pipeline/submit
    │   ├── deps.py                # .env load, asyncpg pool, Redis client, session dependency
    │   ├── db_schema.py           # App-table DDL (app_customers/accounts/transactions/...)
    │   ├── state_projector.py     # Kafka consumer → Redis txn state (in-process task)
    │   ├── demo_profiles.py       # 3 fixed demo login profiles (ALLOW/OTP/BLOCK)
    │   ├── routers/               # auth, banking, transfers, otp, admin, verdicts
    │   └── services/              # mappers, otp_service (EasySendSMS), txn_logger
    ├── agents/                    # One module per agent
    │   ├── velocity_agent.py      # Redis sliding-window burst/spend signals
    │   ├── geo_agent.py           # Travel feasibility + device novelty
    │   ├── graph_agent.py         # Neo4j account-network signals (also a CLI)
    │   ├── behavior_agent.py      # ML ensemble entry point
    │   └── synthesis_agent.py     # Pure fusion math (two-layer weights, decision)
    ├── behavior_agent/            # Behavior serving: input builders, scorers, ensemble, calibration
    ├── synthesis_agent/           # Synthesis HTTP endpoint, txn_type mapping, audit store
    ├── pipeline/                  # Parallel agent fan-out, decision_settings, explanations, audit
    ├── kafka_bus/                 # Async path: event envelope, producer, orchestrator consumer
    ├── feature_engineering/       # Config (feature_config.yaml), Redis store, geo math, fit stats
    ├── shared/                    # Pydantic schemas (risk contracts, weights), SHAP utils, config
    ├── eval/                      # Offline validation, metrics, MLflow champion/challenger
    ├── notebooks/, models/        # Model training + trained artifacts
    ├── datasets/, datasets_processed/  # Raw source CSVs/JSON + feature_table.csv
    ├── scripts/                   # Data loaders + seed_app_data / seed_demo_profiles / probe_decision
    ├── transactions_logs.json     # Append-only per-transaction verdict log
    └── tests/                     # Pytest suite, one file per agent plus kafka/shap/eval
```

---

## 5. Training the Models

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

## 6. The Agents

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

## 7. The Synthesis Agent

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

## 8. Model Metrics & Key Parameters

A consolidated reference for the numbers that drive the system. Model metrics come from the offline validation in `eval/`; agent parameters and thresholds live in `feature_engineering/feature_config.yaml`, `behavior_agent/config.yaml`, and `shared/schemas/risk.py`.

### Model metrics (held-out, later-in-time)

| Model | PR-AUC | AUROC | Notes |
|---|---|---|---|
| XGBoost | 0.118 | 0.550 | Primary classifier; threshold 0.0115 → recall 0.95; 301 trees |
| Two-branch LSTM | 0.103 | 0.532 | Fires only at ≥50 txns history (~6.9% of accounts) |
| Isolation Forest | 0.020 | — | Unsupervised outlier ranker; cold-start / novel fraud |
| Rule-engine baseline | — | 0.511 | The legacy comparison the system must beat |

Dataset base fraud rate: **1.83%** (7,338 fraud / 400,000 labeled). PR-AUC is the primary metric because it reflects performance on the rare positive class; the top 0.1% of XGBoost scores are ~100% precision.

### Decision thresholds (synthesis)

| Constant | Value | Meaning |
|---|---|---|
| `TAU_LOW` | 0.30 | Fused score below this → **PASS** |
| `TAU_HIGH` | 0.70 | Fused score above this → **BLOCK**; between the two → **OTP** |
| `DISAGREEMENT_VARIANCE_THRESHOLD` | 0.04 | Score variance ≥ this forces a PASS up to OTP |
| `ELEVATED_THRESHOLD` | 0.50 | A risk score at/above this counts as "elevated" for pattern detection |
| `MONEY_LAUNDERING_MAX_SPREAD` | 0.20 | Max min–max spread for the "clustered" money-laundering test |

These are overridable at runtime: the admin console `PUT /admin/settings` persists `{otpThreshold, blockThreshold, disagreementThreshold}` to Postgres + Redis, and `pipeline/decision_settings.py` feeds them into both the API and orchestrator within seconds.

### Agent parameters

| Agent | Parameter | Value |
|---|---|---|
| Velocity | sliding windows | 2 min, 1 hr (Redis sorted sets) |
| Velocity | `saturation_ratio` | 10.0 (count deviation) |
| Velocity | amount `ratio_saturation` | 10.0 · spike `multiplier` 5×, full at 15× |
| Velocity | `type_mismatch.common_share` | 0.20 |
| Velocity | confidence `observation_threshold` | 50 txns |
| Geo | plausibility speed ceiling | 900 km/h |
| Geo | signal blend | 50 / 50 (travel · device novelty) |
| Geo | confidence `observation_threshold` | 20 geo events |
| Behavior | blend — cold start (<10 txns) | iso 0.65 / xgb 0.35 |
| Behavior | blend — medium history | xgb 0.60 / iso 0.40 |
| Behavior | blend — rich history (≥50 txns) | xgb 0.45 / iso 0.20 / lstm 0.35 |
| Behavior | SHAP | top-10 signed contributions (XGBoost TreeExplainer) |

### Redis keys, prefixes & TTLs

| Key pattern | TTL | Purpose |
|---|---|---|
| `session:{token}` | 86,400 s (sliding) | User session (opaque token) |
| `otp:{txn_id}` | 180 s | Hashed OTP + attempt counter (max 3 attempts, 2 resends) |
| `txn:state:{txn_id}` | 3,600 s | Live transaction workflow state (polled by the UI) |
| `cache:customer:{id}` | 300 s | Cached customer profile |
| `config:thresholds` | — | Live decision thresholds (admin-tunable) |
| `user:{acct}:*`, `account_baseline:{acct}`, `geo:last:{acct}`, `devices:known:{acct}` | window + slack | Velocity/Geo agent hot-path state |

---

## 9. Tools and Technologies

- **FastAPI + asyncio.** One unified app hosts every agent; the `/evaluate` endpoint fans out to all four agents concurrently with `asyncio.gather`, so total latency is the slowest agent, not the sum. Each agent also has its own endpoint for debugging.
- **Redis.** The hot path for the Velocity and Geo agents (sorted sets for sliding windows, hashes for baselines/last-location, sets for known devices) and the application layer (sessions, OTP challenges, live transaction state, profile cache). Failures surface immediately (no retries, short socket timeouts) so a down cache degrades gracefully instead of stalling requests.
- **PostgreSQL.** System of record: raw transactions, reference tables (customer profiles, device fingerprints, OTP logs, graph node aggregates) queried by the Behavior Agent through asyncpg pools, the application tables (`app_customers`/`app_accounts`/`app_transactions`/…), and the `synthesis_audit` decision trail. Also the Geo Agent's fallback when Redis misses.
- **Neo4j.** Powers the Graph Agent: multi-hop questions like "does this account reach the ring collector within two hops" are single Cypher queries with EXISTS subqueries, which relational SQL handles poorly.
- **Kafka (aiokafka).** The async path: `/transfer` and `/pipeline/submit` publish to a single `fraud-events` topic keyed by transaction id (guaranteeing per-transaction event ordering), a standalone orchestrator consumes and runs fan-out + fusion, and an in-process state projector turns the staged events into live UI progress. The synchronous `/evaluate` path works without Kafka.
- **XGBoost, scikit-learn, PyTorch.** The three Behavior models. XGBoost artifacts are loaded before torch is imported anywhere (loading them after torch segfaults on macOS due to duplicate OpenMP runtimes; torch imports are deferred for this reason).
- **imbalanced-learn (ADASYN).** Train-time oversampling for XGBoost against the 1.83% base rate.
- **SHAP.** Per-decision explainability: a TreeExplainer is built once at model load and produces top-10 signed feature contributions per request, stored with the audit record.
- **MLflow.** Offline only, never in the request path: tracks validation runs and gates retrained models behind a PR-AUC champion/challenger comparison.
- **EasySendSMS.** OTP delivery: the backend generates and validates codes; SMS goes out via the EasySendSMS REST API (with a dev-mode fallback that returns the code in the status payload).
- **uv + a single pyproject.toml.** One dependency tree for agents, training, and tests keeps environments reproducible.
- **pytest.** Per-agent suites covering signal math, cold-start behavior, parity with training notebooks, failure semantics, and endpoint shapes; fakes for Redis mean most tests run without live infrastructure.
- **Next.js 16 + React 19 (frontend).** A banking app plus fraud ops console (live feed, per-transaction agent verdict drill-down with SHAP, system health), wired to the live backend through the `/api/*` server-side proxy.
- **Docker Compose.** The whole stack containerized with exactly two host-exposed ports (see [Deployment](#11-deployment--quick-start)).

---

## 10. API & Reference

Base URL `http://localhost:8000`. The frontend reaches the backend through a same-origin `/api/*` proxy, so browser calls look like `/api/health`. Request/response bodies are JSON.

### Core classes & functions

| Symbol | Location | Role |
|---|---|---|
| `PipelineTxn` | `pipeline/agent_runner.py` | Normalized transaction every agent slices from (`txn_id`, `account_id`, `txn_type`, `amount`, `device_id`, `latitude`, `longitude`, …). |
| `AgentOutcome` | `pipeline/agent_runner.py` | Uniform agent result: `status` (`ok`/`unavailable`/`not_found`/`skipped`/`error`), `risk_score`, `confidence`, `explanation`, `latency_ms`, `detail`. |
| `run_velocity/run_geo/run_graph/run_behavior` | `pipeline/agent_runner.py` | Wrap each agent into an `AgentOutcome`; translate every failure into a non-`ok` status (never a fabricated score). |
| `fuse(outcomes, txn_type_raw)` | `pipeline/agent_runner.py` | Build verdicts + run synthesis; returns `(SynthesisResult, TransactionType, verdicts)`. Uses live thresholds from `decision_settings.current_config()`. |
| `synthesise(verdicts, txn_type, *, cfg)` | `agents/synthesis_agent.py` | The whole §7 pipeline: `classify_pattern` → `layer1_weights` → `layer2_weights` → `blend_weights` → `fuse` → `decide`. |
| `SynthesisConfig` | `agents/synthesis_agent.py` | Overridable copy of the decision constants (`tau_low`, `tau_high`, `disagreement_variance_threshold`, …). |
| `VelocityAgent.evaluate(event)` | `agents/velocity_agent.py` | → `(risk, confidence)`, Redis-only. |
| `GeoAgent.evaluate(...)` | `agents/geo_agent.py` | → `(risk, confidence, signals)`, Redis + Postgres. |
| `graph_agent.evaluate(account_id, session)` | `agents/graph_agent.py` | → dict `{graph_score, flag, decision, reasons[], signals}`. |
| `BehaviorAgent.evaluate_timed(account_id, txn_id)` | `agents/behavior_agent.py` | → `(BehaviorVerdict, latency_ms)`; raises `TxnNotFoundError`/`ModelMissingError`/`AllModelsFailedError`/`PostgresUnavailableError`. |
| `run_state_projector()` | `app/state_projector.py` | In-process Kafka consumer (group `fraud-state-projector`) → Redis txn state + terminal side effects. |
| `otp_service.initiate/verify/resend/complete_transaction` | `app/services/otp_service.py` | OTP lifecycle (generate, SMS via EasySendSMS, hash-store, validate, complete). |
| `DecisionAction`, `FraudPattern`, `TransactionType`, `AgentVerdict`, `SynthesisResult`, `Layer1Weights`, `Layer2Weights`, `SHAPExplanation` | `shared/schemas/risk.py` | The decision domain contracts + weight tables (single source of truth). |

### Endpoints & requests

**Pipeline / agents**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Per-agent connectivity (`velocity/geo/graph/behavior/synthesis/kafka`). |
| `POST` | `/evaluate` | Synchronous full pipeline → `PipelineResponse` (decision, score, pattern, per-agent outcomes, weights, SHAP). |
| `POST` | `/pipeline/submit` | Async: publish to Kafka, `202 Accepted`. |
| `POST` | `/velocity/evaluate` · `/geo/evaluate` · `/graph/evaluate` · `/agents/behavior/evaluate` · `/agents/synthesis/evaluate` | Individual agent endpoints (debugging). |

`POST /evaluate` request:

```json
{ "txn_id": "TXN-20260528-32895DA9", "account_id": "ACC-1002022",
  "txn_type": "ATM_WITHDRAWAL", "amount": 543.61,
  "device_id": "DEV-4C4CFB", "latitude": 27.7172, "longitude": 85.3240 }
```

**Auth & sessions** (`/auth/*`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/auth/login-mpin` | Mobile + 4-digit mPIN → `{token, user}` (session in Redis). |
| `POST` | `/auth/login-profile` | One-click demo profile login → `{token, user, profile}` (with prefill). |
| `POST` | `/auth/login-biometric` · `/auth/verify-mpin` · `/auth/logout` | Biometric unlock, transfer-time re-auth, logout. |
| `GET` | `/auth/customer-preview` · `/auth/demo-profiles` | Login-screen name preview; the 3 demo profiles. |

**Banking reads** (session required)

| Method | Path |
|---|---|
| `GET` | `/customers/{id}` · `/accounts?customerId=` · `/cards?customerId=` |
| `GET` | `/transactions` (filters) · `/transactions/{id}` · `/recipients/resolve` |

**Transfers & OTP**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/transfer` | Submit a transfer → `202 {txnId, reference}`; triggers the Kafka pipeline. |
| `GET` | `/transfers/{txn_id}/status` | Poll live pipeline state: agents, synthesis, decision, otp, txn. |
| `POST` | `/otp/verify` · `/otp/resend` | Validate / re-issue the SMS OTP. |

**Admin & verdicts** (analyst console; session-free by design)

| Method | Path |
|---|---|
| `GET` | `/admin/stats` · `/admin/trends` · `/admin/risk-locations` · `/admin/live-transactions` · `/admin/flagged` |
| `GET` | `/admin/customers` · `/admin/customers/{id}` · `/admin/accounts` · `/admin/transactions/{id}` |
| `GET` | `/admin/system-health` · `/admin/otp-sessions` · `/admin/otp-events` · `/admin/network-graph` · `/admin/baseline-comparison` |
| `GET`/`PUT` | `/admin/settings` (live decision thresholds) |
| `GET` | `/admin/reports/{key}` (CSV export) · `/verdicts/{txn_id}` (model verdict) |

### Error handling

The backend uses conventional HTTP status codes; error bodies are `{"detail": "<message>"}` which the frontend surfaces directly.

| Status | When |
|---|---|
| `400` | Invalid input, insufficient balance, wrong OTP (message includes attempts left). |
| `401` | Not authenticated / session expired or invalid (missing/expired Bearer token). |
| `403` | Acting on an account or transaction that isn't yours. |
| `404` | Customer / transaction / verdict / profile not found. |
| `410` | OTP expired (past the 180 s TTL). |
| `422` | Behavior agent: all models failed to produce a score (no fabricated score returned). |
| `429` | OTP locked (3 failed attempts) or resend limit reached. |
| `503` | A backing store is down (Redis / Postgres / Neo4j / Kafka) — the affected agent abstains and, for the sync agent endpoints, returns 503. |

**Degradation contract:** in the full pipeline (`/evaluate`, `/transfer`), an agent whose datastore is unavailable returns a non-`ok` `AgentOutcome` and is *omitted from fusion* rather than failing the whole request; the synthesis weights renormalize over the agents that reported. The decision still returns as long as at least one agent produced a verdict.

---

## 11. Deployment & Quick Start

Two ways to run the system: **Docker** (recommended — one command, everything containerized) or **local** (run each service yourself).

### Option A — Docker (quick start)

Requires Docker Desktop / Docker Engine with Compose. Only two ports are exposed to your machine: the frontend (`3000`) and the backend (`8000`); Postgres, Redis, Neo4j, and Kafka stay on an internal network.

```bash
# 1. Clone
git clone <repository-url>
cd Agentic-Fraud-Detection-System-

# 2. Configure secrets (copy the template, then edit the two passwords)
cp docker/.env.example docker/.env
#    set POSTGRES_PASSWORD and NEO4J_PASSWORD in docker/.env

# 3. Build and start the whole stack
docker compose -f docker/docker-compose.yml up --build
```

Then open:

- **Frontend:** <http://localhost:3000>
- **Backend health:** <http://localhost:8000/health>

The schema is created automatically on first boot (`docker/postgres/init.sql`). To load the reference data + demo login profiles (needed for real decisions), see `docker/README.md` → "Loading reference data". Stop with `docker compose -f docker/docker-compose.yml down`; reset all data (drop volumes) with `... down -v`. Full details, port rationale, and env overrides are in **[`docker/README.md`](docker/README.md)**.

### Option B — Local (run each service yourself)

**Prerequisites:** Python 3.12/3.13 + [uv](https://docs.astral.sh/uv/); PostgreSQL 15+, Redis 7+, Neo4j 5+; Kafka (for the async/transfer path); Node.js 20+ (for the dashboard).

```bash
# 1. Backend deps
cd Agentic-Fraud-Detection-System-/backend
uv sync                       # add --group ml to also install MLflow

# 2. Configure backend/.env
cat > .env <<'EOF'
FRAUD_DB_DSN=postgresql://<user>:<pass>@localhost:5432/fraud_detection_global
FRAUD_REDIS_HOST=localhost
FRAUD_REDIS_PORT=6379
FRAUD_KAFKA_BOOTSTRAP=localhost:9092
NEO4J_URI=neo4j://127.0.0.1:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<password>
NEO4J_DATABASE=neo4j
OTP_DEV_MODE=1                 # log the OTP instead of sending SMS
CORS_ORIGINS=http://localhost:3000
EOF

# 3. Create the database + schema, then load reference data
createdb fraud_detection_global
psql -d fraud_detection_global -f ../docker/postgres/init.sql     # 21-table schema
uv run python -m scripts.load_device_fingerprints                 # + your dataset loaders
# load the Neo4j graph (account_graph_nodes.csv / _edges.csv) as (:Account)-[:SENT]->(:Account)

# 4. Seed the application + demo profiles (from the loaded reference data)
uv run python -m scripts.seed_app_data
uv run python -m scripts.seed_demo_profiles

# 5. Start the services (three terminals)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000    # A: API + state projector
uv run python -m kafka_bus.orchestrator                    # B: Kafka agent orchestrator
cd ../frontend && npm install && npm run dev               # C: dashboard on :3000
```

### Verify

```bash
curl -s http://localhost:8000/health          # expect all agents "ok"
```

Log in at <http://localhost:3000> with a demo profile (or mobile `9801234567` / mPIN `1234` after seeding), and submit a transfer — you'll watch the pipeline run and return PASS / OTP / BLOCK live. Score a transaction directly:

```bash
curl -s -X POST http://localhost:8000/evaluate -H 'Content-Type: application/json' -d '{
  "txn_id": "TXN-20260528-32895DA9", "account_id": "ACC-1002022",
  "txn_type": "ATM_WITHDRAWAL", "amount": 543.61,
  "device_id": "DEV-4C4CFB", "latitude": 27.7172, "longitude": 85.3240 }'
```

### Operate

```bash
uv run pytest                                  # test suite
uv run python -m eval.run_offline_validation   # metrics vs rule baseline, logged to MLflow
uv run mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db --port 5000
```

For production adaptation: map your institution's transaction-type codes in `synthesis_agent/txn_type_mapping.py`, tune thresholds and weights from the admin console (`PUT /admin/settings`) or `feature_engineering/feature_config.yaml`, retrain on your labeled data on a regular cadence, and let the MLflow champion/challenger gate decide promotion.

---

*Authors: Manash Lamichhane, Pratik Joshi, Dikshanta Chapagain, Biplov Gautam, Pawan Acharya. Softwarica College, Kathmandu, Nepal.*
