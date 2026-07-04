# Fraud-detection backend: FastAPI app hosting all agents + trained model artifacts.
FROM python:3.12-slim

# uv manages the environment exactly as in development (single pyproject.toml).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# libgomp is required at runtime by XGBoost / LightGBM / scikit-learn.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Install dependencies first so code changes don't invalidate this layer.
COPY pyproject.toml ./
RUN uv sync --no-dev

# Application code + trained model artifacts (models/ is baked into the image;
# datasets are excluded via .dockerignore and mounted only for load scripts).
COPY . .

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
