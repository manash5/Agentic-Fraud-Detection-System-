# Velocity Agent

The Velocity Agent is the FastAPI microservice responsible for the paper's §IV-C-1 velocity-risk evaluation. It scores a transaction by comparing short-window activity, recipient changes, dormancy signals, and amount anomaly statistics from the `velocity_snapshots` table against each account's historical customer profile.

The service reads:

- `velocity_snapshots` for precomputed transaction velocity features.
- `transactions` for the transaction record associated with the snapshot.
- `customers` for account-level monthly transaction history used in confidence scoring and hourly spike checks.

## Velocity Risk Rules

The risk score is the sum of these rule contributions, capped at `1.0`:

- Z-score anomaly: `z_score_amount > 3.5` adds `0.30`; `z_score_amount > 5.0` adds `0.40`.
- Transaction count spike: `txn_count_1m >= 3` adds `0.25`; `txn_count_1h > avg_monthly_txn_count / 24` adds `0.15`; this bucket is capped at `0.30`.
- New counterparty: `new_counterparty_flag = true` adds `0.20`.
- Dormancy break: `dormancy_break = true` and `z_score_amount > 3` adds `0.25`.
- Weekend night activity: `weekend_flag = true` and `night_flag = true` adds `0.10`.
- Rapid unique recipients: `unique_counterparties_1h >= 3` adds `0.15`.

## Confidence Scoring

- High confidence `0.95`: account has `50+` average monthly transactions.
- Medium confidence `0.75`: account has `20-50` average monthly transactions.
- Low confidence `0.50`: account has fewer than `20` average monthly transactions, is dormant, or is missing from `customers`.
- If multiple velocity snapshot fields are null, confidence is reduced by `0.10`.

## Run Locally

From `backend/services/velocity-agent`:

```bash
uvicorn app.main:app --reload --port 8001
```

The service loads `DATABASE_URL` from `backend/.env`.

From `backend`, the shared Docker Compose stack runs this service on port `8001`:

```bash
docker compose up --build velocity-agent
```

## Endpoints

Health check:

```bash
curl http://localhost:8001/health
```

Evaluate transaction velocity risk:

```bash
curl -X POST http://localhost:8001/evaluate \
  -H "Content-Type: application/json" \
  -d '{"txn_id": "TXN-20260101-00000001", "account_id": "ACC-0000001"}'
```

Example response:

```json
{
  "txn_id": "TXN-20260101-00000001",
  "risk_score": 0.45,
  "confidence": 0.85,
  "breakdown": {
    "z_score_risk": 0.30,
    "txn_count_risk": 0.15,
    "new_counterparty_risk": 0.0,
    "dormancy_break_risk": 0.0,
    "weekend_night_risk": 0.0,
    "unique_recipients_risk": 0.0
  },
  "latency_ms": 42
}
```

## Tests

From the repository root:

```bash
pytest backend/services/velocity-agent/tests/
```
