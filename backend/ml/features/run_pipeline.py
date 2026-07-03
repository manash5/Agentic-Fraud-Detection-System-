"""Entrypoint: clean the raw 2M-row datasets and write processed outputs.

Runs the full cleaning + feature-engineering pipeline in bounded memory by
streaming the large transaction/geo tables in chunks. Progress is logged with
timestamps and resident-set-size (RSS) so long runs are observable.

Usage
-----
    cd backend
    python -m ml.features.run_pipeline          # clean + features + EDA
    python -m ml.features.run_pipeline --no-eda  # skip EDA plots
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd
import psutil

from ml.features.build_features import DEFAULT_CHUNKSIZE, build_feature_table
from ml.features.clean_transactions import clean_geo_events, clean_otp_logs

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = BACKEND_ROOT / "datasets"
DEFAULT_OUTPUT_DIR = BACKEND_ROOT / "datasets_processed"

# Files copied through unchanged (already clean / not part of the flat table).
COPY_AS_IS: tuple[tuple[str, str], ...] = (
    ("customer_profiles.csv", "customer_profiles_cleaned.csv"),
    ("velocity_snapshots.csv", "velocity_snapshots_cleaned.csv"),
    ("fraud_labels_train.csv", "fraud_labels_train_cleaned.csv"),
    ("account_graph_nodes.csv", "account_graph_nodes.csv"),
    ("account_graph_edges.csv", "account_graph_edges.csv"),
)

_PROC = psutil.Process(os.getpid())


def _mem_gb() -> float:
    return _PROC.memory_info().rss / 1024**3


def _log(step: str, msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {step} {msg}  (RSS {_mem_gb():.2f} GB)", flush=True)


def run_pipeline(
    data_dir: Path | None = None,
    output_dir: Path | None = None,
    *,
    chunksize: int = DEFAULT_CHUNKSIZE,
    run_eda: bool = True,
) -> None:
    """Execute the full cleaning + feature-engineering (+ EDA) pipeline."""
    src = data_dir or DEFAULT_DATA_DIR
    out = output_dir or DEFAULT_OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    _log("[1/5]", "Cleaning + saving side tables (geo, otp, customers, velocity, graph)...")
    _clean_geo_events(src, out, chunksize)
    _save_otp(src, out)
    for source_name, dest_name in COPY_AS_IS:
        shutil.copy2(src / source_name, out / dest_name)
        print(f"       copied {dest_name}", flush=True)
    gc.collect()

    _log("[2/5]", "Building feature table (streaming transactions, joining 6 sources)...")
    txn_type_encoding, summary = build_feature_table(
        data_dir=src,
        output_path=out / "feature_table.csv",
        labeled_output_path=out / "feature_table_labeled.csv",
        cleaned_txn_path=out / "transactions_raw_cleaned.csv",
        chunksize=chunksize,
    )

    _log("[3/5]", "Saving encoding map + metadata...")
    (out / "txn_type_encoding.json").write_text(
        json.dumps(txn_type_encoding, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"       saved txn_type_encoding.json ({len(txn_type_encoding)} types)")

    if run_eda:
        _log("[4/5]", "Running EDA analysis (plots + report)...")
        try:
            from ml.features import eda

            eda.generate_eda(processed_dir=out)
        except Exception as exc:  # pragma: no cover - EDA is best-effort
            print(f"       WARNING: EDA step failed ({exc!r}); continuing.")
    else:
        _log("[4/5]", "Skipping EDA (--no-eda).")

    _log("[5/5]", "Done!")
    print(
        f"\nProcessed outputs written to {out}\n"
        f"  feature_table.csv:          {summary['total_rows']:,} rows\n"
        f"  feature_table_labeled.csv:  {summary['labeled_rows']:,} rows\n"
        f"  features:                   {summary['num_features']} columns"
    )


def _clean_geo_events(src: Path, out: Path, chunksize: int) -> None:
    """Stream-clean geo_events (2M rows) into geo_events_cleaned.csv."""
    dest = out / "geo_events_cleaned.csv"
    if dest.exists():
        dest.unlink()
    first = True
    for chunk in pd.read_csv(src / "geo_events.csv", chunksize=chunksize):
        cleaned = clean_geo_events(chunk)
        cleaned.to_csv(dest, mode="a", header=first, index=False)
        first = False
        del chunk, cleaned
    gc.collect()
    print("       saved geo_events_cleaned.csv", flush=True)


def _save_otp(src: Path, out: Path) -> None:
    otp = clean_otp_logs(pd.read_csv(src / "otp_logs.csv"))
    otp.to_csv(out / "otp_logs_cleaned.csv", index=False)
    print(f"       saved otp_logs_cleaned.csv ({len(otp):,} rows)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the feature pipeline.")
    parser.add_argument("--no-eda", action="store_true", help="Skip EDA plot generation.")
    parser.add_argument(
        "--chunksize", type=int, default=DEFAULT_CHUNKSIZE, help="Rows per chunk."
    )
    args = parser.parse_args()
    run_pipeline(run_eda=not args.no_eda, chunksize=args.chunksize)


if __name__ == "__main__":
    main()
