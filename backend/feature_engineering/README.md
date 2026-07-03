# Feature Engineering Layer — Transaction, Velocity & Geo Agents

Fit/transform feature engineering over `fraud_detection_global`
(`transactions_raw`, `velocity_snapshots`, `geo_events`), with Redis as the
real-time hot path and Postgres as the batch/historical source of truth.

Three engineers, each writing its own table (all keyed on `txn_id`, so the
tables join cleanly for the Synthesis agent):

- `TransactionFeatureEngineer` — core per-transaction features (calendar, log
  amount, categorical encodings, success/notes/currency flags). No history, no
  Redis; the natural join point the other two build on.
- `VelocityFeatureEngineer` — sliding-window counts/amounts + §6 velocity
  derivations. Redis hot path, Postgres batch/fallback.
- `GeoFeatureEngineer` — recomputed travel deltas/speed, risk composite,
  distance z-score, ISP encoding.

```
                  ┌──────────────── hot path (per txn, <800ms P95) ───────────────┐
 new txn ──► TransactionFeatureEngineer.transform_one ──► (own columns only, no I/O)
             VelocityFeatureEngineer.transform_one     ──► Redis ZADD/ZCOUNT (windows)
             GeoFeatureEngineer.transform_one              Redis HGETALL (30d/90d baseline)
                  │ Redis down? WARN + fall back to indexed Postgres queries
                  ▼
   transaction_features_engineered / velocity_features_engineered / geo_features_engineered

                  ┌──────────────── cold path (batch) ──────────────┐
 nightly_baseline_job ──► account_baseline_daily ──► Redis account_baseline:{acc} (26h TTL)
 run_batch_pipeline   ──► SQL window functions over transactions_raw/geo_events
```

## Files

| File | Purpose |
|---|---|
| `feature_config.yaml` | ALL thresholds, windows, TTLs, weights, split dates — no magic numbers in code |
| `transaction_features.py` | `TransactionFeatureEngineer` (calendar, amount_log, encodings, flag cross-check) |
| `velocity_features.py` | `VelocityFeatureEngineer` (fit/transform, Redis hot path, PG fallback) |
| `geo_features.py` | `GeoFeatureEngineer` (haversine recomputation, risk composite, encodings) |
| `redis_client.py` | `VelocityStateStore` — sorted-set counters + baseline cache, `RedisUnavailable` on failure |
| `nightly_baseline_job.py` | 30d/90d baselines → `account_baseline_daily` → Redis refresh |
| `run_batch_pipeline.py` | Orchestrator: fit on train window, chunked transform of everything |
| `validate_features.py` | §10 checks: correlation flags, latency benchmark, 20-row sample dump |
| `monitoring.py` | fit-time vs transform-time feature stats + drift warnings |
| `artifacts/` | fitted params (JSON), correlation flags, sample dump |

Run (from `backend/`):

```bash
uv run python -m feature_engineering.run_batch_pipeline      # full batch build
uv run python -m feature_engineering.nightly_baseline_job    # nightly cron
uv run python -m feature_engineering.validate_features       # pre-handoff checks
uv run pytest tests/test_transaction_features.py tests/test_velocity_features.py \
              tests/test_geo_features.py tests/test_redis_client.py \
              tests/test_feature_monitoring.py
```

## Why the source snapshot columns are recomputed, not trusted

Empirical verification (2026-07) against the loaded data found the
pre-computed columns to be synthetic noise, not derivations:

- `velocity_snapshots` window counts are **non-monotone** in a huge share of
  rows (e.g. `txn_count_15m > txn_count_1h` in 287k of 2.0M rows; even
  `txn_count_24h > txn_count_7d` in 387k rows) — impossible for real nested
  sliding windows.
- `z_score_amount` disagrees with `(amount − avg_30d)/std_30d` computed from
  the same row's own columns in ~98% of sampled rows.
- `geo_events.prev_txn_time_delta_min` matches the delta to the account's
  actual previous transaction in **0 of 7,864** sampled rows (median error
  ≈ 8 days).
- `impossible_travel` is False on rows whose coordinates imply speeds above
  300,000 km/h.
- The source CSVs each contained 2 duplicate `txn_id`s (dropped at load,
  keeping first occurrence).

Consequently every window count, baseline, z-score, travel distance/speed and
flag is **recomputed from `transactions_raw` / `geo_events` coordinates using
only strictly-prior data**. The shipped `impossible_travel` is retained as
`impossible_travel_reported` purely for the `travel_flag_mismatch` audit
column. This also changes the documented Redis-outage fallback: it queries
`transactions_raw` (correct, indexed on `(account_id, timestamp)`) instead of
`velocity_snapshots` (empirically wrong), which honors the intent — degrade,
don't fail — without inheriting corrupt counts.

## Leakage prevention

- **Batch**: all history features use SQL window frames ending at the current
  row (`RANGE ... PRECEDING AND CURRENT ROW`); the 30d baseline additionally
  `EXCLUDE CURRENT ROW`; geo history uses `LAG`/expanding frames ending at
  `1 PRECEDING`. Nothing after a transaction's timestamp can reach its row —
  adding future rows cannot change existing features.
- **Real-time**: Redis windows contain only already-seen events plus the
  current one; the baseline hash is computed nightly from windows ending at
  the *previous* midnight.
- **No label contact**: `is_fraud` is never read, directly or indirectly.
  This is why `isp_risk_encoding` is a **frequency** encoding, not a target
  encoding — target encoding would require labels and is forbidden by policy.
- **Time-based split** (`split:` in config): fit uses 2025-01-01 → 2026-01-31
  only; 2026-02-01 → 2026-05-31 is validation. Never random shuffle — account
  behavior and fraud patterns drift.
- Window-count semantics: every count **includes the current transaction**
  (minimum 1) identically in the Redis and SQL paths.

## Redis design (§3)

| Key | Type | Contents | TTL |
|---|---|---|---|
| `velocity:{account_id}` | ZSET | score = txn epoch-ms, member = `txn_id` | 8d |
| `velocity_amt:{account_id}` | ZSET | score = epoch-ms, member = `"{amount:.2f}:{txn_id}"` | 8d |
| `account_baseline:{account_id}` | HASH | `avg_txn_amount_30d_npr`, `std_txn_amount_30d_npr`, `n_txn_30d`, `avg_km_from_home_90d`, `std_km_from_home_90d`, `n_geo_90d`, `baseline_date` | 26h |

Per transaction (one pipelined round trip): `ZADD` both keys →
`ZREMRANGEBYSCORE` older than 7d → `EXPIRE` → `ZCOUNT` per window →
`ZRANGEBYSCORE` for the two amount windows. 30-day stats are **never**
computed on the hot path; the live `amount_npr` is compared against the
cached nightly baseline (the hybrid pattern).

Eviction: run Redis with `maxmemory-policy volatile-ttl` — every key this
layer writes carries a TTL, and everything is reconstructible from Postgres
(Redis is a derived, ephemeral cache; the nightly job and organic traffic
self-heal it). `nightly_baseline_job` logs a warning if the live policy
differs.

Failure mode: any Redis error raises `RedisUnavailable`; `transform_one`
logs a **warning** and computes the same windows from `transactions_raw`
(single indexed per-account query, ~ms) — the transaction is always scored.

## Auto-created output tables (§5)

All created idempotently (`CREATE TABLE IF NOT EXISTS`) by the code that
writes them; writes are `COPY`-into-temp + `ON CONFLICT (pk) DO UPDATE`
upserts, so reruns are safe. All five are populated by `run_batch_pipeline`.

1. **`transaction_features_engineered`** — one row per txn (PK/FK
   `txn_id → transactions_raw`). Core §3 features: calendar (`txn_hour`,
   `txn_day_of_week`, `txn_is_weekend`), `amount_log`, `response_code_is_success`,
   `has_notes`, `is_international`, `currency_is_foreign`, and the one-hot
   `channel_*` / `auth_method_*` encodings. This table owns categorical
   encoding; velocity/geo do not duplicate it.
2. **`velocity_features_engineered`** — one row per txn (PK/FK). Window counts +
   amount windows pulled from Redis/SQL at scoring time, recomputed 30d
   baseline, and ONLY the §6 derived velocity features (below).
3. **`geo_features_engineered`** — one row per txn (PK/FK). Recomputed
   travel deltas, speed, flags, composite, distance z, ISP encoding.
4. **`account_baseline_daily`** — PK `(account_id, baseline_date)`. The
   nightly 30d/90d rolling stats that feed the Redis baseline cache. Windows
   end at the baseline date's midnight (no same-day lookahead).
5. **`feature_pipeline_runs`** — audit: `run_id`, `table_written`,
   `row_count`, `started_at`, `finished_at`, `feature_config_version`
   (sha256-prefix of `feature_config.yaml`), `notes`.

## Feature dictionary (final engineered set)

### transaction_features_engineered

| Feature | dtype | Valid range | Null handling | Meaning |
|---|---|---|---|---|
| `txn_hour` | int | [0, 23] | never null (from timestamp) | hour of day |
| `txn_day_of_week` | int | [0, 6] | never null | Monday=0 … Sunday=6 |
| `txn_is_weekend` | int | {0,1} | never null | Sat/Sun (matches dataset `weekend_flag` 100%) |
| `amount_log` | float | ≥ 0 | amount null→0 → log1p(0)=0 | `log1p(amount_npr)` — tames the right skew |
| `response_code_is_success` | int | {0,1} | non-'00' ⇒ 0 | 1 iff ISO-8583 approval code `00` |
| `has_notes` | int | {0,1} | null notes ⇒ 0 | whether free-text `notes` is present (~40% null) |
| `is_international` | int | {0,1} | null ⇒ 0 | passed through from source boolean |
| `currency_is_foreign` | int | {0,1} | — | 1 iff `currency != 'NPR'` |
| `channel_*` (4) | int | {0,1} | unseen level ⇒ all zeros | one-hot channel (ATM/BRANCH/MOBILE_APP/WEB) |
| `auth_method_*` (5) | int | {0,1} | unseen level ⇒ all zeros | one-hot auth (BIOMETRIC/CARD_PIN/MPIN/OTP_EMAIL/OTP_SMS) |

Data-quality cross-check (§3): in batch, `txn_is_weekend` and the derived night
flag are compared to `velocity_snapshots.weekend_flag` / `night_flag`; a
disagreement rate above the configured tolerance logs one warning. Measured:
weekend 100% agree; night ~4% disagree (timestamp/timezone noise) — this
intentionally trips the warning at the 2% night tolerance.

### velocity_features_engineered

| Feature | dtype | Valid range | Null handling | Meaning |
|---|---|---|---|---|
| `txn_count_1m/5m/15m/1h/24h/7d` | int | ≥ 1 | never null (includes current txn) | events in trailing window for this account |
| `total_amount_1h_npr`, `total_amount_24h_npr` | float | ≥ amount_npr | never null | NPR moved in trailing window (incl. current) |
| `avg_txn_amount_30d_npr` | float | > 0 or null | null ⇒ `is_cold_start=1` | trailing-30d mean amount, **excluding** current txn |
| `std_txn_amount_30d_npr` | float | ≥ 0 or null | null/–<1 ⇒ cold start | trailing-30d std, excluding current txn |
| `n_txn_30d_prior` | int | ≥ 0 | 0 for new accounts | prior txns inside the 30d window |
| `z_score_amount` | float | [−10, 10] | 0 when cold start | winsorized amount vs own 30d baseline |
| `velocity_acceleration` | float | [0, ~86400] | counts null→0 first | `c_1h / max(c_24h/24, ε)` — burst vs steady elevation |
| `amount_deviation_ratio` | float | [0, 50] | 1.0 (neutral) when cold start | amount ÷ own 30d average |
| `structuring_proximity` | float | [0, 50000] | amount null→0 → distance to 9999 | min NPR distance to 9999/49999/99999 (continuous gradient) |
| `night_flag` | int | {0,1} | never null (from timestamp) | 22:00–05:59 local |
| `night_burst_interaction` | float | ≥ 0 | 0 | `night_flag × txn_count_1m` (explicit interaction) |
| `is_cold_start` | int | {0,1} | — | <5 prior 30d txns or degenerate baseline; gates z/ratio |

(Categorical `channel` / `auth_method` encodings live in
`transaction_features_engineered`, not here — join on `txn_id`.)

### geo_features_engineered

| Feature | dtype | Valid range | Null handling | Meaning |
|---|---|---|---|---|
| `prev_txn_km_recomputed` | float | [0, 20000] | 0 when no prior event / null coords | haversine to account's previous geo event |
| `prev_txn_time_delta_min_recomputed` | float | ≥ 0 | 0 when no prior event | minutes since previous geo event |
| `implied_travel_speed_kmh` | float | [0, 3000] | 0 if no prev / Δt < 0.5min | km ÷ hours, clipped for stability |
| `impossible_travel_recomputed` | int | {0,1} | 0 when unknowable | UNclipped speed > 900 km/h AND hop > 50 km |
| `travel_flag_mismatch` | int | {0,1} | — | recomputed flag ≠ shipped flag (data-quality audit) |
| `geo_risk_composite` | float | [0, 1] | flags null→0 | 0.40·tor + 0.30·impossible + 0.15·datacenter + 0.15·vpn |
| `distance_z_score` | float | [−10, 10] | 0 if distance null | `km_from_home` vs account's OWN prior distribution; global train fallback when <5 prior events |
| `isp_risk_encoding` | float | [0, 1] | unseen ISP ⇒ 0.0 | training-frequency of `ip_isp` (low = rare = riskier) |
| `is_foreign_ip` | int | {0,1} | null country ⇒ 1 (not "Nepal") | IP country differs from home country |

## Encoding choices (§7)

- `channel` (4 levels), `auth_method` (5): **one-hot**, in
  `transaction_features_engineered`. Cardinality is tiny, levels are unordered
  (ordinal would invent a fake ranking), and tree/linear models both consume
  one-hots cleanly. Unseen level at inference → all-zeros row, never an error.
- `response_code`: collapsed to boolean `response_code_is_success` (code `00`
  = approval; `05`/`51`/`57` are declines). The raw code is categorical but
  success/failure is the signal downstream logic needs.
- `currency` (2 levels): collapsed to `currency_is_foreign` — with `NPR`/`USD`
  a full encoding is redundant with the boolean. (Perfectly collinear with
  `is_international` in this dataset — see validation.)
- `ip_isp`: **frequency encoding** learned at `fit()` on the training window
  only. In this dataset it has just 5 levels, but frequency encoding is kept
  (rather than one-hot) so the schema survives ISP churn in production
  without new columns; unseen → 0.0 ("never seen" reads as maximally rare).
  Target encoding rejected — forbidden by the no-label-contact rule.
- `ip_country` (2 levels): collapsed to `is_foreign_ip` — with two observed
  values a full encoding is redundant with the boolean.

## Missing data & outliers (§8) — verified empirically, not from the dictionary

Measured null rates: `velocity_snapshots` and `geo_events` have **zero**
nulls; `transactions_raw` nulls are `fx_rate` 98.8% (foreign txns only),
`terminal_id` 65.0% (card-present only), `notes` **40.0%** (dictionary said
~78% — empirical differs, so `has_notes` is a flag not an imputation),
`session_id` 15.0%, `merchant_category_code` 4.2%. `amount_npr`, `currency`,
`channel`, `auth_method`, `response_code`, `is_international` are **0% null**.
Columns the engineers don't consume are simply never read. Inputs that can be
null at inference are handled per-feature (tables above): `notes`→`has_notes=0`,
`amount_npr`→0 (so `log1p`=0), and velocity/geo history nulls mean "new account"
and are encoded as `is_cold_start` / neutral values rather than imputed medians,
because the *absence* of history is itself the signal.

Winsorization (caps learned on training data at fit time):
`amount_npr` at p99.5 (≈4.58M NPR of a 5M max — tames the heavy tail feeding
z/ratio without erasing it; `structuring_proximity` uses the RAW amount since
its thresholds sit far below the cap). `km_from_home_district` at p99.9
(legit mass ends ≈124 km; the extreme rows are exactly what
`distance_z_score`'s ±10 clip and the travel flags preserve). Ratios/z-scores
carry their own clips (`[0,50]`, `±10`) per config.

## Drift monitoring (§9)

`fit()` logs and stores mean/std/null-rate/min/max per engineered feature;
every `transform()` recomputes them and warns when a mean moves > 3 fit-stds
or a null rate shifts > 0.05 (`drift:` in config).

## Validation results (§10)

Run `validate_features.py` to regenerate these artifacts. The most recent
run produced:

- `correlation_flags.csv` — pairs with |r| > 0.9 across the joined
  transaction + velocity + geo feature set:
  - `night_flag` ↔ `night_burst_interaction`: r = 1.0. Expected by construction
    (`night_burst_interaction = night_flag * txn_count_1m`) and because the
    sample is dominated by rows where `txn_count_1m = 1`. Both retained: the
    flag captures time-of-day risk, the interaction the rare night burst.
  - `is_international` ↔ `currency_is_foreign`: r = 1.0. In this dataset every
    international txn is USD and vice versa, so the two are collinear. Kept both
    deliberately (per §3) since other agents may want them as distinct signals;
    a downstream model should drop one if it prefers.
  - `txn_count_24h` ↔ `velocity_acceleration`: r = -0.97. Expected by construction
    (`velocity_acceleration = txn_count_1h / max(txn_count_24h/24, ε)`). Different
    concepts — a sudden 1h burst vs. steady elevated 24h activity — both kept.
  - `total_amount_1h_npr` ↔ `total_amount_24h_npr`: r = 0.961. Expected because
    the 1h amount window is nested inside the 24h window.
  - `avg_txn_amount_30d_npr` ↔ `std_txn_amount_30d_npr`: r = 0.964. Expected
    for an account's own historical distribution: mean and spread scale together.
- Latency benchmark: most recent run (n=200, live Postgres + Redis, all three
  engineers per txn) reported **p50=13.5ms, p95=33.7ms, p99=52.8ms** → **PASS**
  against the 800ms P95 budget. The hot path is one pipelined Redis round-trip
  (`record_and_count`) plus a single `HGETALL`; the Postgres fallback is a
  single indexed per-account query.
- `sample_features.csv`: 20 transactions with the full transaction + velocity +
  geo feature vector for manual sanity-check before wiring into the Synthesis
  agent.
