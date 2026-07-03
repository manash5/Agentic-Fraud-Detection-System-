# Behavior Agent

The Behavior Agent implements the paper's §IV-C-3 behavioral fraud layer. It loads trained ML models from `backend/ml/models/`, builds the same feature vector used during training from Postgres, scores the transaction, and returns SHAP explanations for the strongest XGBoost drivers.

## Models

- **XGBoost**: primary supervised fraud classifier. Always used when `xgboost_model.pkl` is loaded.
- **Isolation Forest**: unsupervised anomaly detector. Always used with XGBoost and helps cold-start accounts.
- **LSTM**: per-account transaction sequence model. Used only when the account has at least 50 historical transactions.

Startup expects these artifacts:

```text
backend/ml/models/xgboost_model.pkl
backend/ml/models/isolation_forest_model.pkl
backend/ml/models/lstm_model.pt
backend/ml/models/feature_columns.json
```

If an artifact is missing, startup fails with a clear model-loading error.

## Blend Formula

Warm users with 50+ transactions:

```text
risk_score = 0.4 * xgb_score + 0.3 * isoforest_score + 0.3 * lstm_score
```

Cold-start users:

```text
risk_score = 0.6 * xgb_score + 0.4 * isoforest_score
```

The final score is capped at `1.0`.

## SHAP Explainability

The service calls `shared.explainability.shap_utils.compute_shap_values()` for XGBoost, ranks features by absolute SHAP value, and returns the top 5:

```json
[
  {
    "feature": "vel_z_score_amount",
    "shap_value": 0.28,
    "direction": "increases_fraud",
    "feature_value": 4.2
  }
]
```

If SHAP fails or takes more than 2 seconds, the service logs a warning and returns an empty `shap_explanation` array.

## Run

The service reads `DATABASE_URL` from `backend/.env`.

```bash
cd backend/services/behavior-agent
uvicorn app.main:app --reload --port 8004
```

Health check:

```bash
curl http://localhost:8004/health
```

Evaluate behavior risk:

```bash
curl -X POST http://localhost:8004/evaluate \
  -H "Content-Type: application/json" \
  -d '{"txn_id": "TXN-20260101-00000001", "account_id": "ACC-0000001"}'
```

Example response:

```json
{
  "txn_id": "TXN-20260101-00000001",
  "risk_score": 0.72,
  "confidence": 0.88,
  "model_scores": {
    "xgboost": 0.75,
    "isolation_forest": 0.68,
    "lstm": 0.71
  },
  "models_used": ["xgboost", "isolation_forest", "lstm"],
  "shap_explanation": [],
  "user_profile": {
    "account_has_50plus_transactions": true,
    "is_dormant": false,
    "transaction_count": 87
  },
  "latency_ms": 95
}
```

## Testing

Run the unit tests:

```bash
pytest backend/services/behavior-agent/tests/
```

To test with real data, pick one known fraudulent `txn_id` from `fraud_labels_train.csv` after loading it into Postgres. The trained XGBoost model should produce a high score when fraud-driving features such as `vel_z_score_amount`, `is_fraud_merchant`, `geo_impossible_travel`, or structuring amounts are present.

## Docker

This service-specific Dockerfile expects the build context to be `backend` so it can copy both `shared/` and `ml/models/`:

```bash
cd backend
docker build -f services/behavior-agent/Dockerfile -t behavior-agent .
docker run --env-file .env -p 8004:8004 behavior-agent
```
