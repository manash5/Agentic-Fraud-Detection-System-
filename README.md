# Agentic Multi-Model Framework for Real-Time Fraud Detection

## Overview
This project proposes a real-time fraud detection framework designed for Nepal's digital payment ecosystem (eSewa, Khalti, ConnectIPS). The system uses three parallel AI agents (Velocity, Geo, and Behavior) to evaluate every transaction and produce a verdict (PASS, OTP, or BLOCK) in under 800 milliseconds.

## How It Works
- **Velocity Agent** tracks transaction frequency, amount spikes, and balance integrity using Redis counters
- **Geo Agent** checks travel feasibility, device fingerprints, and detects fraud rings via Neo4j graph database
- **Behavior Agent** runs XGBoost, Isolation Forest, and LSTM models to detect anomalous patterns

A Synthesis Agent combines all three risk scores with context-aware weights and confidence scoring. Suspicious transactions trigger a dual-path OTP verification via both SMS (Sparrow) and email to prevent SIM-swap attacks.

## Key Features
- Sub-800ms verdict latency (estimated ~300ms in practice)
- SHAP explainability for every decision (regulatory compliance)
- Cold-start handling for new users with limited transaction history
- Graph-based fraud ring detection using Neo4j
- Weekly retraining via MLflow with challenger-champion deployment

## Technology Stack
Apache Kafka (event streaming), Redis (counters), PostgreSQL (ledger), Neo4j (graph), XGBoost, Isolation Forest, LSTM, FastAPI, Docker, MLflow

## Status
This paper presents the system architecture and design. Production validation on live Nepal payment data is the next phase.

## Authors
Manash Lamichhane, Pratik Joshi, Dikshanta Chapagain, Biplov Gautam, Pawan Acharya – Softwarica College, Kathmandu, Nepal
