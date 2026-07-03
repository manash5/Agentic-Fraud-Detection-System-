# Scripts

Operational loaders live here when implemented. For now, use:

- **Feature engineering:** `python -m ml.features.run_pipeline`
- **Model training:** `python -m ml.training.run_all_training`
- **Offline eval:** `python eval/offline_validation.py`

Postgres/Neo4j loaders will be added here when wiring sample data into Docker volumes.
