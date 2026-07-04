"""Load behavior_agent/config.yaml once per process.

Environment overrides (deploy-time knobs only, never thresholds):
``FRAUD_DB_DSN``, ``FRAUD_REDIS_HOST``, ``FRAUD_REDIS_PORT``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
BACKEND_DIR = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def load_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    with open(path) as f:
        cfg: dict[str, Any] = yaml.safe_load(f)
    cfg["database"]["dsn"] = os.environ.get("FRAUD_DB_DSN", cfg["database"]["dsn"])
    cfg["redis"]["host"] = os.environ.get("FRAUD_REDIS_HOST", cfg["redis"]["host"])
    cfg["redis"]["port"] = int(os.environ.get("FRAUD_REDIS_PORT", cfg["redis"]["port"]))
    return cfg


def model_path(cfg: dict[str, Any], *keys: str) -> Path:
    """Resolve a models.* config path relative to backend/."""
    node: Any = cfg["models"]
    for k in keys:
        node = node[k]
    return BACKEND_DIR / node


def pg_connect_kwargs(dsn: str) -> dict[str, Any]:
    """Translate a libpq-style DSN into asyncpg kwargs (URIs pass through)."""
    if dsn.startswith(("postgres://", "postgresql://")):
        return {"dsn": dsn}
    key_map = {"dbname": "database", "host": "host", "port": "port",
               "user": "user", "password": "password"}
    kwargs: dict[str, Any] = {}
    for token in dsn.split():
        key, _, value = token.partition("=")
        if key in key_map:
            kwargs[key_map[key]] = value
    return kwargs
