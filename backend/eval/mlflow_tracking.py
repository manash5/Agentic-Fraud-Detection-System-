"""MLflow lifecycle helpers — offline path only, never on the request hot path."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from eval.paths import MLRUNS_DIR

EXPERIMENT_NAME = "fraud-detection-offline-validation"
REGISTRY_NAME = "fraud-behavior-models"


def configure_mlflow(tracking_uri: str | None = None) -> str:
    import mlflow

    MLRUNS_DIR.mkdir(parents=True, exist_ok=True)
    default_uri = f"sqlite:///{(MLRUNS_DIR / 'mlflow.db').resolve()}"
    uri = tracking_uri or default_uri
    mlflow.set_tracking_uri(uri)
    mlflow.set_experiment(EXPERIMENT_NAME)
    return uri


@contextmanager
def start_validation_run(
    model_name: str,
    *,
    run_name: str | None = None,
    tags: dict[str, str] | None = None,
) -> Iterator[Any]:
    """Context manager: one MLflow run per model validation."""
    import mlflow

    configure_mlflow()
    merged_tags = {"model_name": model_name, "stage": "offline_validation"}
    if tags:
        merged_tags.update(tags)
    with mlflow.start_run(run_name=run_name or f"{model_name}-offline"):
        mlflow.set_tags(merged_tags)
        yield mlflow.active_run()


def log_metrics(metrics: dict[str, Any], prefix: str = "") -> None:
    import mlflow

    for key, val in metrics.items():
        if isinstance(val, (int, float)) and key not in ("n",):
            mlflow.log_metric(f"{prefix}{key}" if prefix else key, float(val))


def log_json_artifact(data: dict[str, Any], filename: str) -> None:
    import mlflow
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f, indent=2)
        path = f.name
    mlflow.log_artifact(path, artifact_path="reports")
    Path(path).unlink(missing_ok=True)


def champion_challenger_decision(
    challenger: dict[str, float],
    champion: dict[str, float] | None,
    *,
    primary_metric: str = "pr_auc",
    min_improvement: float = 0.0,
) -> dict[str, Any]:
    """Shadow-eval gate: promote challenger only if primary metric improves."""
    if champion is None or primary_metric not in champion:
        return {
            "decision": "promote",
            "reason": "no prior champion registered",
            "primary_metric": primary_metric,
            "challenger_value": challenger.get(primary_metric),
        }
    c_val = float(challenger.get(primary_metric, 0.0))
    p_val = float(champion[primary_metric])
    delta = c_val - p_val
    promote = delta >= min_improvement
    return {
        "decision": "promote" if promote else "reject",
        "reason": (
            f"{primary_metric} improved by {delta:+.4f}" if promote
            else f"{primary_metric} regressed by {delta:+.4f} — keep champion"
        ),
        "primary_metric": primary_metric,
        "champion_value": p_val,
        "challenger_value": c_val,
        "delta": delta,
    }


def load_champion_metrics(model_name: str) -> dict[str, float] | None:
    """Best prior offline-validation run for this model (by pr_auc)."""
    import mlflow
    from mlflow.tracking import MlflowClient

    configure_mlflow()
    client = MlflowClient()
    exp = client.get_experiment_by_name(EXPERIMENT_NAME)
    if exp is None:
        return None
    runs = client.search_runs(
        experiment_ids=[exp.experiment_id],
        filter_string=f"tags.model_name = '{model_name}' AND metrics.pr_auc > 0",
        order_by=["metrics.pr_auc DESC"],
        max_results=1,
    )
    if not runs:
        return None
    return {k: float(v) for k, v in runs[0].data.metrics.items()}
