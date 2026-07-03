from __future__ import annotations

import logging
import os
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import QueuePool

from app.behavior_check import (
    ModelNotConfiguredError,
    TransactionNotFoundError,
    configure_models,
    evaluate_behavior,
)
from app.model_loader import (
    DEFAULT_FEATURE_TABLE,
    DEFAULT_MODELS_DIR,
    load_feature_table_index,
    load_models,
)
from app.routers.evaluate import router as evaluate_router
from shared.constants.service_names import BEHAVIOR_AGENT


logger = logging.getLogger("behavior-agent")
logging.basicConfig(level=logging.INFO)

BACKEND_DIR = Path(__file__).resolve().parents[3]
load_dotenv(BACKEND_DIR / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL")
engine = (
    create_engine(
        DATABASE_URL,
        poolclass=QueuePool,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )
    if DATABASE_URL
    else None
)

app = FastAPI(
    title="Behavior Agent",
    version="0.2.0",
    description="ML behavior fraud risk microservice with SHAP explainability.",
)


class BehaviorEvaluateRequest(BaseModel):
    txn_id: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)


@app.on_event("startup")
def startup_load_dependencies() -> None:
    app.state.db_available = False
    models_dir = Path(os.environ.get("MODELS_DIR", str(DEFAULT_MODELS_DIR)))
    feature_table = Path(os.environ.get("FEATURE_TABLE_PATH", str(DEFAULT_FEATURE_TABLE)))

    try:
        models = load_models(models_dir)
        missing = []
        if models.xgboost is None:
            missing.append("xgboost_model.pkl")
        if models.isolation_forest is None:
            missing.append("isolation_forest_model.pkl")
        if models.lstm_model is None:
            missing.append("lstm_model.pt")
        if not models.feature_columns:
            missing.append("feature_columns.json")
        if missing:
            raise RuntimeError(f"Missing Behavior Agent model artifacts: {', '.join(missing)}")
    except Exception as exc:
        logger.error("Behavior Agent model loading failed:\n%s", traceback.format_exc())
        raise RuntimeError("Behavior Agent startup failed while loading ML models") from exc

    app.state.models = models
    app.state.feature_index = load_feature_table_index(feature_table)
    configure_models(models, models.lstm_model)

    if engine is None:
        logger.error("DATABASE_URL is not configured")
        return

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        app.state.db_available = True
    except Exception:
        logger.error("Behavior Agent failed to connect to Postgres:\n%s", traceback.format_exc())
        return

    print("✅ Behavior Agent loaded XGBoost, Isolation Forest, LSTM models")


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": BEHAVIOR_AGENT}


@app.post("/evaluate")
def evaluate(body: BehaviorEvaluateRequest) -> dict:
    if engine is None or not getattr(app.state, "db_available", False):
        raise HTTPException(status_code=503, detail="Database unavailable")

    started = time.perf_counter()
    try:
        with engine.connect() as connection:
            result = evaluate_behavior(body.txn_id, body.account_id, connection)
    except TransactionNotFoundError:
        raise HTTPException(status_code=404, detail="Transaction not found") from None
    except SQLAlchemyError:
        logger.error("Database error while evaluating txn_id=%s:\n%s", body.txn_id, traceback.format_exc())
        app.state.db_available = False
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    except ModelNotConfiguredError as exc:
        logger.error("Model configuration error while evaluating txn_id=%s:\n%s", body.txn_id, traceback.format_exc())
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except Exception:
        logger.error("Unexpected error while evaluating txn_id=%s:\n%s", body.txn_id, traceback.format_exc())
        raise

    latency_ms = int((time.perf_counter() - started) * 1000)
    response = {**result, "latency_ms": latency_ms}
    logger.info(
        "Evaluated txn_id=%s risk=%s confidence=%s models=%s latency_ms=%s",
        body.txn_id,
        response["risk_score"],
        response["confidence"],
        response["models_used"],
        latency_ms,
    )
    return response

app.include_router(evaluate_router)
