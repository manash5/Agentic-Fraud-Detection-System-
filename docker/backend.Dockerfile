# =============================================================================
# backend.Dockerfile — FastAPI fraud-detection agents (+ Kafka orchestrator)
#
# Single image, two run modes (selected by the compose `command:`):
#   API + state projector :  uvicorn app.main:app --host 0.0.0.0 --port 8000
#   Kafka orchestrator     :  python -m kafka_bus.orchestrator
#
# The whole ML stack (torch, xgboost, lightgbm, shap, numba …) is heavy, so the
# dependency layer is installed first and cached; source is copied last so code
# edits don't reinstall the world. Deps come from the committed uv.lock, so the
# build is reproducible. --host 0.0.0.0 is REQUIRED: binding 127.0.0.1 inside a
# container makes the API unreachable from other containers and the host.
#
# Build context is the repo ROOT (see docker-compose.yml `context: ..`), so
# paths below are `backend/...`.
# =============================================================================

# ---- builder: resolve + install dependencies into a venv ---------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    # Pull the CPU build of torch (and skip the ~4GB of nvidia-cuda-* wheels the
    # default PyPI torch drags in). Containers here have no GPU; the LSTM runs on
    # CPU regardless. This keeps the image ~5GB smaller and avoids exhausting the
    # Docker build disk. Requires re-resolving torch, so we don't use --frozen.
    UV_TORCH_BACKEND=cpu

WORKDIR /app

# Only the lockfiles first → this layer is cached until deps change.
COPY backend/pyproject.toml backend/uv.lock ./
# Install runtime deps only (skip the `dev` group; the `ml`/mlflow group is
# non-default and already excluded). No project install yet (no source copied).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev

# ---- runtime: slim python + the prebuilt venv + source -----------------------
FROM python:3.12-slim-bookworm AS runtime

# libgomp1: OpenMP runtime for xgboost/lightgbm. curl: container healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Non-secret defaults; compose overrides the connection targets with the
    # docker service names (postgres / redis / kafka / neo4j).
    TXN_LOG_PATH=/app/transactions_logs.json

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
# Source + trained model artifacts. Heavy data dirs are excluded by
# backend/.dockerignore (datasets, datasets_processed, mlruns, notebooks, .venv);
# reference data is loaded at runtime, not baked into the image.
COPY backend/ /app/

# Ensure the live transaction log is writable.
RUN touch /app/transactions_logs.json

EXPOSE 8000

# Default command = the API (compose overrides it for the orchestrator service).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
