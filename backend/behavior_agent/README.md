# Behavior Agent — ensemble blend + FastAPI endpoint

Combines the three already-trained behavior models — **XGBoost** (supervised),
**Isolation Forest** (unsupervised/cold-start), **two-branch LSTM** (sequence) —
into ONE consolidated `risk_score` plus a `confidence` score reflecting which
models contributed (paper §IV-C-3). All models are preloaded at startup;
warm-request latency is ~10–30ms against the paper's 100ms budget.

## Layout

| File | Role |
|---|---|
| `agents/behavior_agent.py` | Agent entry point (`BehaviorAgent.connect/evaluate`), same shape as the velocity/geo/graph agents |
| `behavior_agent/input_builders.py` | One builder per model, each to its OWN manifest |
| `behavior_agent/scorers.py` | Per-model scorers + percentile calibration |
| `behavior_agent/ensemble.py` | Blend + confidence formulas |
| `behavior_agent/api.py` | Standalone FastAPI app (`POST /agents/behavior/evaluate`) |
| `behavior_agent/config.yaml` | Weights, thresholds, history cutoffs, model paths |
| `behavior_agent/artifacts.py` | One-time preload of every model/scaler/preprocessor |
| `behavior_agent/lstm_arch.py` | TwoBranchLSTM module (torch import deferred — see below) |
| `behavior_agent/load_reference_tables.py` | One-off loader for the 3 sources missing from Postgres |
| `behavior_agent/build_calibration.py` | One-off builder of the percentile grids |

The endpoint is also mounted in the unified app (`app/main.py`), so
`uv run uvicorn app.main:app --port 8000` serves it alongside the other agents.

## Step-0 reconciliation (what each model actually needs)

| | XGBoost | Isolation Forest | LSTM (two-branch) |
|---|---|---|---|
| Artifacts | `models/xgboost_behavior.json`, `model_feature_list.json` | `isolation_forest_transactions_raw.joblib`, `_feature_list.json`, `_scaler.joblib` | `models/lstm/lstm_two_branch.pt`, `manifest.json`, `preprocessors.joblib` |
| Features | 87 (txn + customer-profile join: ordinals, profile aggregates, demographic one-hots) | 50 (transactions_raw only + ALL-TIME account amount aggregates) | seq 79 × N=30 (txn/device/velocity/geo/OTP per timestep) + static 32 (profile OHE + graph degrees + district_freq) |
| Source tables | transactions_raw, **customer_profiles** | transactions_raw (+ per-account SQL aggregate) | transactions_raw, device_fingerprints, velocity_snapshots, geo_events, **otp_logs**, **customer_profiles**, **account_graph_nodes** |
| Scaler/encoder | none (trees) — encodings inline | StandardScaler over 8 continuous cols | seq StandardScaler(79), static StandardScaler(9), OneHotEncoder(5 cats), saved district_freq map |
| Output | `predict_proba` ∈ [0,1] | `-score_samples` (unbounded) | sigmoid(logit) ∈ [0,1] |
| Saved threshold | 0.01154 (val PR-AUC 0.118) | none (contamination=0.02 assumed) | none (test PR-AUC 0.103) |

**Format bridges** (Postgres stores raw strings; training read CSVs via pandas):
`merchant_category_code '4814'` → trained column `..._4814.0` (float-read);
`response_code '00'` → `response_code_0` / `rc_0` (int-read); device
`first_seen/last_seen` are timestamptz, compared UTC-naive as at train;
`min(first_seen, last_seen)` fixes the ~50% reversed device timestamps; the
LSTM's `currency` seq feature was string-coerced to a constant 0 at train and
is reproduced as such.

**Verified parity** (the reason to trust the builders): sampled txns scored via
each builder against the notebooks' own saved outputs —
XGBoost vs `val_scored_xgboost.csv` max |Δproba| = 2.7e-9; Isolation Forest vs
`transactions_scored_isoforest.csv` max |Δscore| = 5.6e-17; LSTM per-timestep
features vs `feature_table.csv` max relative Δ = 4.3e-8 (float32 rounding).

## Step 2 — materialization decision

Postgres was missing three sources entirely: `customer_profiles` (50k rows),
`otp_logs` (43.3k, deduped per txn_id exactly like the LSTM notebook),
`account_graph_nodes` (50k). Instead of materializing a 2M-row
`behavior_transactions.csv` (which would duplicate already-queryable data and
be useless inside a 100ms request), these three small reference tables were
loaded into Postgres with indexes by `load_reference_tables.py`. Every builder
now works off one queryable source through the asyncpg pool. The big per-txn
tables (transactions_raw, velocity_snapshots, geo_events, device_fingerprints)
were already loaded and indexed.

Data caveat (inherited, documented): several `velocity_snapshots` /
`geo_events` columns are synthetic noise rather than true derivations. The
LSTM was *trained* on those columns, so inference must feed the same columns —
parity with training beats recomputing them here.

## Score calibration

Raw outputs live on incomparable scales (XGBoost probas cluster near its 0.0115
threshold; the anomaly score is unbounded; LSTM probas are inflated by
pos_weight=53.5). `build_calibration.py` therefore saves a 1001-point quantile
grid per model (`models/behavior_score_calibration.json`):

- isolation_forest: quantiles of the 2M training anomaly scores
- xgboost: quantiles of the 80k validation probabilities
- lstm: quantiles of 1000 windows sampled from eligible (≥50-txn) accounts,
  scored through the real builder — no retraining involved

`calibrated_score = P(reference score < raw score)` ∈ [0,1] — "how extreme is
this transaction for this model". The blend uses calibrated scores
(`ensemble.blend_on: calibrated`); raw scores are always reported alongside.

## Blend + confidence formulas

```
risk_score = Σ w_i · s_i / Σ w_i        over models that contributed
```

`w_i` comes from the history-dependent profile (config.yaml `ensemble.weights`),
where history = the account's txn count up to and including the evaluated txn:

| profile | condition | xgboost | isolation_forest | lstm | rationale |
|---|---|---|---|---|---|
| cold_start | history < 10 | 0.35 | 0.65 | — | profile-join features weak, LSTM abstains |
| medium | 10 ≤ h < 50 | 0.60 | 0.40 | — | supervised model dominates |
| rich | h ≥ 50 | 0.45 | 0.20 | 0.35 | LSTM fires; IF drops to support |

The LSTM only fires at ≥50 txns of history (`history.lstm_min_history`; below
that it wasn't trained to be reliable). Only ~6.9% of accounts ever reach 50,
so **LSTM abstention is the common case** and the blend renormalizes weights
over whichever models contributed.

```
confidence = coverage(n_contributing) · agreement
coverage   = {1: 0.50, 2: 0.75, 3: 1.00}                  (config)
agreement  = 1 − clip(stddev(contributing scores) / 0.5, 0, 1)
```

For two models `agreement = 1 − |s1 − s2|`; a single contributor has no
dispersion, so `confidence = coverage(1) = 0.5`. This is the confidence the
Synthesis Agent consumes: high when several models fired and agree, low on
cold-start or sharp disagreement.

Failure semantics: a model whose *input rows* are missing for a txn is marked
`contributed: false` with the error in the breakdown (never zero-filled); if no
model can score, the endpoint returns 422. Missing model artifacts and Postgres
being down return **distinct 503 details**.

## Running

```bash
# one-off setup (already run):
uv run python -m behavior_agent.load_reference_tables   # 3 missing tables -> Postgres
uv run python -m behavior_agent.build_calibration       # percentile grids

# standalone:
uv run uvicorn behavior_agent.api:app --port 8001
# or as part of the unified app:
uv run uvicorn app.main:app --port 8000

curl -X POST localhost:8001/agents/behavior/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"ACC-1002022","txn_id":"TXN-20260528-32895DA9"}'
```

Response: `{risk_score, confidence, weights_profile, history_count,
model_breakdown: {xgboost|isolation_forest|lstm: {score, raw_score,
calibrated_score, contributed, effective_weight, ...}}, latency_ms}`.
A warning is logged whenever a request exceeds the 100ms budget.

Tests: `uv run pytest tests/test_behavior_agent.py` (builder parity on real
txn_ids, cold-start vs rich-history, model-missing/Postgres-down failure
paths, endpoint shape + warm latency under budget).

## Gotcha: macOS OpenMP segfault

Calling `xgboost.load_model` after `import torch` segfaults (duplicate OpenMP
runtimes). `artifacts.load_bundle` loads the XGBoost model FIRST and imports
torch only afterwards (`lstm_arch.py` is imported lazily). Never add a
top-level `import torch` anywhere in this package's import chain.
