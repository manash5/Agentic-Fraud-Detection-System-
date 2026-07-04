#!/usr/bin/env python3
"""CLI: run offline validation and log results to MLflow.

    uv run python -m eval.run_offline_validation
    uv run python -m eval.run_offline_validation --no-mlflow
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from eval.offline_validation import run_all  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline fraud-model validation")
    parser.add_argument("--no-mlflow", action="store_true", help="skip MLflow logging")
    args = parser.parse_args()
    report = run_all(log_mlflow=not args.no_mlflow)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
