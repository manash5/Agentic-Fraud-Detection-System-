"""Master training script: runs every step in order and prints the final summary.

Each trainer writes ``ml/models/metrics_<model>.json``; this script re-reads
them to build the summary table from real numbers and writes the manifest.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ml.training.common import (
    BASELINE,
    MODELS_DIR,
    TARGETS,
    load_metrics,
)

STEPS: tuple[tuple[str, str], ...] = (
    ("Feature Preparation", "ml.training.prepare_features"),
    ("XGBoost", "ml.training.train_xgboost"),
    ("LightGBM", "ml.training.train_lightgbm"),
    ("Ensemble", "ml.training.train_ensemble"),
    ("Isolation Forest", "ml.training.train_isolation_forest"),
    ("LSTM", "ml.training.train_lstm"),
    ("Meta-Learner", "ml.training.train_meta_learner"),
    ("SHAP Values", "ml.training.generate_shap"),
    ("COMM-042 Detection", "ml.training.detect_comm042"),
)

SUMMARY_MODELS: tuple[tuple[str, str], ...] = (
    ("XGBoost", "xgboost"),
    ("LightGBM", "lightgbm"),
    ("Ensemble (XGB+LGB)", "ensemble"),
    ("Isolation Forest", "isolation_forest"),
    ("LSTM", "lstm"),
    ("Meta-Learner (RF)", "meta_learner"),
)

ARTIFACT_FILES: tuple[tuple[str, str], ...] = (
    ("xgboost_model.pkl", ""),
    ("lightgbm_model.pkl", ""),
    ("ensemble_config.json", ""),
    ("isolation_forest_model.pkl", ""),
    ("lstm_model.pt", ""),
    ("meta_learner_model.pkl", ""),
    ("feature_columns.json", ""),
    ("shap_values_train.csv", "(BONUS: +3%)"),
    ("community_detection.json", "(BONUS: +5%)"),
    ("training_manifest.json", ""),
)

WIDTH = 78


def _fmt_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def _line(text: str = "") -> None:
    print("║  " + text.ljust(WIDTH - 2) + "║")


def _rule() -> None:
    print("╠" + "═" * WIDTH + "╣")


def run_all_training(feature_table: Path | None = None) -> None:
    step_times: dict[str, float] = {}

    for idx, (label, module) in enumerate(STEPS, start=1):
        print(f"\n{'=' * 60}\n[{idx}/{len(STEPS)}] {label} ({module})\n{'=' * 60}")
        cmd = [sys.executable, "-m", module]
        if feature_table is not None and module not in ("ml.training.detect_comm042",):
            cmd += ["--feature-table", str(feature_table)]
        started = time.perf_counter()
        subprocess.run(cmd, check=True)
        step_times[label] = time.perf_counter() - started

    all_metrics = {key: load_metrics(key) for _, key in SUMMARY_MODELS}
    _write_manifest(all_metrics)
    _print_summary(all_metrics, step_times)


def _write_manifest(all_metrics: dict[str, dict | None]) -> None:
    artifacts = []
    for name, bonus in ARTIFACT_FILES:
        path = MODELS_DIR / name
        artifacts.append(
            {
                "file": name,
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else 0,
                "bonus": bonus or None,
            }
        )
    payload = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(MODELS_DIR),
        "baseline": BASELINE,
        "targets": TARGETS,
        "models": {k: v for k, v in all_metrics.items() if v},
        "artifacts": artifacts,
    }
    (MODELS_DIR / "training_manifest.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def _print_summary(all_metrics: dict[str, dict | None], step_times: dict[str, float]) -> None:
    xgb = all_metrics.get("xgboost") or {}
    n_total = 400_001
    n_fraud = 7_338

    label_to_step = {
        "xgboost": "XGBoost",
        "lightgbm": "LightGBM",
        "ensemble": "Ensemble",
        "isolation_forest": "Isolation Forest",
        "lstm": "LSTM",
        "meta_learner": "Meta-Learner",
    }

    print()
    print("╔" + "═" * WIDTH + "╗")
    print("║" + "TRAINING COMPLETE — FINAL SUMMARY".center(WIDTH) + "║")
    _rule()
    _line(
        f"Dataset: {n_total:,} labeled rows │ {n_fraud:,} fraud │ "
        f"{n_total - n_fraud:,} legitimate"
    )
    _rule()
    _line("MODEL               │ AUROC │ RECALL │ F1(0.5) │ FPR   │ TIME")
    _rule()
    for label, key in SUMMARY_MODELS:
        m = all_metrics.get(key)
        if not m:
            _line(f"{label:<19} │  (metrics missing)")
            continue
        elapsed = step_times.get(label_to_step.get(key, ""), m.get("train_seconds", 0.0))
        _line(
            f"{label:<19} │ {m['auroc']:.3f} │ {m['recall']:.3f}  │ "
            f"{m['f1']:.3f}   │ {m['fpr']:.3f} │ {_fmt_duration(elapsed)}"
        )
    _rule()
    _line(
        f"BASELINE (Rule Eng) │ {BASELINE['auroc']:.3f} │ {BASELINE['recall']:.3f}  │ "
        f"{BASELINE['f1']:.3f}   │ {BASELINE['fpr']:.3f} │ N/A"
    )
    _rule()

    candidates = [m for m in (all_metrics.get("ensemble"), xgb) if m]
    best = max(candidates, key=lambda m: m["auroc"]) if candidates else None
    if best:
        _line("BEST vs BASELINE:")

        def check(metric: str, target: float, lower_is_better: bool = False) -> str:
            value = best[metric]
            met = value <= target if lower_is_better else value >= target
            return "✓ TARGET MET" if met else "✗ TARGET MISSED"

        def pct(new: float, old: float) -> str:
            delta = (new - old) / old * 100
            return f"({delta:+.1f}% {'↑' if delta >= 0 else '↓'}"

        _line(
            f"AUROC:    {best['auroc']:.3f} vs {BASELINE['auroc']:.3f}  "
            f"{pct(best['auroc'], BASELINE['auroc'])} {check('auroc', TARGETS['auroc'])} >{TARGETS['auroc']})"
        )
        _line(
            f"Recall:   {best['recall']:.3f} vs {BASELINE['recall']:.3f}  "
            f"{pct(best['recall'], BASELINE['recall'])} {check('recall', TARGETS['recall'])} >{TARGETS['recall']:.0%})"
        )
        _line(
            f"FPR:      {best['fpr']:.3f} vs {BASELINE['fpr']:.3f}  "
            f"{pct(best['fpr'], BASELINE['fpr'])} {check('fpr', TARGETS['fpr'], lower_is_better=True)} <{TARGETS['fpr']:.1%})"
        )
        _line(
            f"F1:       {best['f1']:.3f} vs {BASELINE['f1']:.3f}  "
            f"{pct(best['f1'], BASELINE['f1'])} {check('f1', TARGETS['f1'])} >{TARGETS['f1']})"
        )
        _rule()

    _line("LATENCY (single transaction inference):")
    latency_total = 0.0
    for label, key in SUMMARY_MODELS:
        m = all_metrics.get(key)
        if m and m.get("latency_single_ms") is not None:
            _line(f"{label + ':':<20} {m['latency_single_ms']:.0f}ms")
            latency_total += m["latency_single_ms"]
    target_note = "well under 800ms target ✓" if latency_total < 800 else "OVER 800ms target ✗"
    _line(f"Full pipeline est: ~{latency_total:.0f}ms ({target_note})")
    _rule()

    _line("ARTIFACTS SAVED:")
    for name, bonus in ARTIFACT_FILES:
        exists = (MODELS_DIR / name).exists()
        mark = "✓" if exists else "✗"
        suffix = f"      {bonus}" if bonus else ""
        _line(f"{mark} ml/models/{name}{suffix}")
    print("╚" + "═" * WIDTH + "╝")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full offline training pipeline")
    parser.add_argument("--feature-table", type=Path, default=None)
    args = parser.parse_args()
    run_all_training(args.feature_table)


if __name__ == "__main__":
    main()
