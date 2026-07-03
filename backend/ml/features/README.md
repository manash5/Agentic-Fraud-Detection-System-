# Feature Engineering Pipeline

Cleans the raw fraud-detection datasets and assembles a single model-ready
feature table. Built to handle the **real dataset scale**:

| Source | Rows |
| --- | --- |
| `transactions_raw.csv` | 2,000,000 |
| `customer_profiles.csv` | 50,000 |
| `geo_events.csv` | 2,000,000 |
| `velocity_snapshots.csv` | 2,000,000 |
| `device_fingerprints.json` | 200,000 devices |
| `fraud_labels_train.csv` | 400,001 (1.83% fraud) |
| `otp_logs.csv` | 43,334 |
| `account_graph_{nodes,edges}.csv` | 50k / 500k |
| `rule_engine_baseline_predictions.csv` | 2,000,000 |

The transaction stream is processed in **chunks (default 100k rows)** and feature
chunks are written to disk incrementally, so peak memory stays ~1 GB regardless
of total row count. A full run takes ~2 minutes and produces a 107-column table.

## Modules

- **`clean_transactions.py`** — chunk-safe data-quality rules for
  `transactions_raw` (+ helpers `clean_geo_events`, `clean_otp_logs`). Handles
  documented issues: mixed timestamp precision, amount normalisation to 2dp,
  malformed/private IP flagging, channel-dependent nulls, near-duplicate bursts,
  structuring amounts, and known fraud merchants. Rows are **flagged, never
  dropped** (including approved-but-fraud `response_code=00`).
- **`build_features.py`** — streams transactions in chunks, joins the six side
  tables, engineers temporal/behavioural/geo/device/OTP features, one-hot encodes
  low-cardinality categoricals with **fixed category lists** (stable columns
  across chunks), and writes `feature_table.csv` + `feature_table_labeled.csv`.
- **`run_pipeline.py`** — orchestrator: cleans + saves all side tables, builds
  the feature table, saves the encoding map, and runs EDA. Logs each step with a
  timestamp and current RSS (via `psutil`).
- **`eda.py`** — generates 15 PNG plots + `EDA_REPORT.md` under `backend/eval/`.

## Feature table (107 columns)

**Raw / cleaned transaction fields:** `txn_id`, `timestamp`, `account_id`,
`counterparty_id`, `amount_npr`, `device_id`, `ip_address`,
`merchant_category_code`, `terminal_id`, `session_id`, `response_code`,
`processing_time_ms`, `is_international`, `fx_rate`.

**Cleaning flags:** `has_device_id`, `has_terminal_id`, `has_session_id`,
`has_fx_rate`, `has_notes`, `is_malformed_ip`, `is_invalid_amount`,
`is_possible_duplicate`, `is_structuring_amount`, `is_fraud_merchant`.

**Temporal:** `hour_of_day`, `day_of_week`, `is_weekend`, `is_night`, `month`.

**Transaction/customer:** `type_encoded`, `amount_ratio`,
`cust_avg_monthly_txn_count`, `cust_avg_monthly_txn_value_npr`,
`cust_is_dormant`, `cust_churn_risk_score`.

**Geo (`geo_*`):** `ip_country`, `latitude`, `longitude`, `is_vpn`, `is_tor`,
`is_datacenter`, `velocity_flag`, `km_from_home_district`, `prev_txn_km`,
`prev_txn_time_delta_min`, `impossible_travel`, plus derived
`geo_high_risk_country`.

**Velocity (`vel_*`):** `txn_count_1m/5m/15m/1h/24h/7d`,
`total_amount_1h_npr`, `total_amount_24h_npr`, `unique_counterparties_1h/24h`,
`new_counterparty_flag`, `z_score_amount`, `dormancy_break`, `night_flag`.

**Device (`dev_*`):** `is_rooted`, `vpn_detected`, `tor_exit_node`,
`biometric_enrolled`, `num_accounts_on_device`, `is_shared`, `locale`,
`risk_signal_count`, plus derived `dev_locale_mismatch`.

**OTP:** `otp_trigger_reason`, `otp_final_decision`, `otp_sim_swap_suspected`,
`has_otp_log`, `otp_failed`.

**Rule-engine baseline:** `rule_baseline_decision`, `rule_triggered`,
`rule_confidence`.

**One-hot groups:** `txn_type_*` (8), `currency_*` (2), `channel_*` (4),
`auth_method_*` (5), `cust_kyc_tier_*` (3), `cust_risk_tier_*` (4).

**Labels:** `fraud_type`, `fraud_confidence`, and `is_fraud` (last column; NaN
for the 80% of rows outside the training split).

## Outputs (`backend/datasets_processed/`)

```
feature_table.csv               2,000,000 rows × 107 cols (~1.2 GB)
feature_table_labeled.csv         400,001 labeled rows (training set)
transactions_raw_cleaned.csv    2,000,000 cleaned rows
customer_profiles_cleaned.csv      50,000
geo_events_cleaned.csv          2,000,000 (+ is_malformed_ip)
velocity_snapshots_cleaned.csv  2,000,000 (copied as-is)
fraud_labels_train_cleaned.csv    400,001
otp_logs_cleaned.csv               43,334
account_graph_nodes.csv / account_graph_edges.csv (copied unchanged)
txn_type_encoding.json          integer encoding map
```

EDA artefacts under `backend/eval/`: `eda_plots/*.png` (15 charts) and
`EDA_REPORT.md`.

## Running

```bash
cd backend

# Clean + feature engineer + EDA (chunked, handles 2M rows, ~2 min)
python -m ml.features.run_pipeline

# Skip plots / tune chunk size
python -m ml.features.run_pipeline --no-eda
python -m ml.features.run_pipeline --chunksize 200000

# Regenerate EDA only (feature table must already exist)
python -m ml.features.eda
```

## Notes / TODOs

- ~0.9% of ATM records may be logged in UTC rather than NPT; timestamps are
  parsed as-is (see the TODO in `clean_transactions.py`).
- `response_code=00` (approved) rows that are later confirmed fraud are kept.
- Near-duplicate detection is scoped per chunk; exact cross-chunk boundary
  duplicates are rare and are only ever flagged, never dropped.
