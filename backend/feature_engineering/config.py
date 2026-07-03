"""Load feature_config.yaml and expose it with a stable version hash.

All thresholds, windows, TTLs and split dates for the velocity/geo feature
layer live in the YAML file next to this module — code must never hard-code
them. ``config_version()`` hashes the file so every table write in
``feature_pipeline_runs`` is traceable to the exact config that produced it.
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "feature_config.yaml"
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"


@lru_cache(maxsize=1)
def load_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    """Parse the YAML config once per process.

    Environment overrides (deploy-time knobs only, not thresholds):
    ``FRAUD_DB_DSN``, ``FRAUD_REDIS_HOST``, ``FRAUD_REDIS_PORT``.
    """
    with open(path) as f:
        cfg: dict[str, Any] = yaml.safe_load(f)
    cfg["database"]["dsn"] = os.environ.get("FRAUD_DB_DSN", cfg["database"]["dsn"])
    cfg["redis"]["host"] = os.environ.get("FRAUD_REDIS_HOST", cfg["redis"]["host"])
    cfg["redis"]["port"] = int(os.environ.get("FRAUD_REDIS_PORT", cfg["redis"]["port"]))
    return cfg


@lru_cache(maxsize=1)
def config_version(path: str | Path = CONFIG_PATH) -> str:
    """First 12 hex chars of the sha256 of the raw config file."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
