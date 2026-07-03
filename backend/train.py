#!/usr/bin/env python3
"""End-to-end: build features (if needed) then train all models into ml/models/."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))

from ml.features.run_pipeline import run_pipeline
from ml.training.data_utils import DEFAULT_FEATURE_TABLE
from ml.training.run_all_training import run_all_training


def main() -> None:
    if not DEFAULT_FEATURE_TABLE.exists():
        print("Building feature table …")
        run_pipeline()
    else:
        print(f"Using existing feature table: {DEFAULT_FEATURE_TABLE}")

    print("\nTraining models → ml/models/\n")
    run_all_training()


if __name__ == "__main__":
    main()
