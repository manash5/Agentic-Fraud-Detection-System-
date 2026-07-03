# Exploratory Data Analysis — Fraud Detection Dataset

## Overview
- Labeled transactions analysed: **400,001**
- Confirmed fraud: **7,338** (1.83%)

## Key Insights
- 25.2% of fraud occurs between 01:00-04:00 NPT
- 1.5x fraud lift at ~9999/49999/99999 amounts
- 44x fraud lift for MERCH-8812/9041/7712
- 6.0% of fraud flagged with impossible travel
- VPN present in 6.0% of fraud vs 0.0% of legit
- Top fraud type: SIM_SWAP at 10.3% of fraud

## Baseline Comparison
- Rule-engine baseline AUROC: **0.71** (target to beat).
- Structuring, fraud-merchant, night-window, impossible-travel and device-root signals above show clear separation and should drive a large AUROC lift.

## Feature Engineering Recommendations
- Keep `is_structuring_amount`, `is_fraud_merchant`, and `dev_locale_mismatch` as high-signal binary features (very large fraud lift).
- Weight `vel_z_score_amount` and `geo_impossible_travel` heavily in the velocity/geo agents.
- Use `hour_of_day`/`is_night` interactions — night window concentrates account-takeover fraud.
- Risk-tier progression is monotonic; `cust_risk_tier` one-hots are useful priors.

## Plots
### Amount Distribution
![Amount Distribution](./eda_plots/amount_distribution.png)

### Correlation Heatmap
![Correlation Heatmap](./eda_plots/correlation_heatmap.png)

### Device Risk
![Device Risk](./eda_plots/device_risk.png)

### Fraud By Channel
![Fraud By Channel](./eda_plots/fraud_by_channel.png)

### Fraud By Day Of Week
![Fraud By Day Of Week](./eda_plots/fraud_by_day_of_week.png)

### Fraud By Hour
![Fraud By Hour](./eda_plots/fraud_by_hour.png)

### Fraud By Txn Type
![Fraud By Txn Type](./eda_plots/fraud_by_txn_type.png)

### Fraud Rate Overview
![Fraud Rate Overview](./eda_plots/fraud_rate_overview.png)

### Fraud Type Distribution
![Fraud Type Distribution](./eda_plots/fraud_type_distribution.png)

### Geo Risk Flags
![Geo Risk Flags](./eda_plots/geo_risk_flags.png)

### Otp Analysis
![Otp Analysis](./eda_plots/otp_analysis.png)

### Risk Tier Fraud Rate
![Risk Tier Fraud Rate](./eda_plots/risk_tier_fraud_rate.png)

### Structuring Pattern
![Structuring Pattern](./eda_plots/structuring_pattern.png)

### Velocity Heatmap
![Velocity Heatmap](./eda_plots/velocity_heatmap.png)

### Z Score Distribution
![Z Score Distribution](./eda_plots/z_score_distribution.png)
