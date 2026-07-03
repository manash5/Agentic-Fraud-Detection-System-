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

from app.velocity_check import TransactionNotFoundError, evaluate_velocity


logger = logging.getLogger("velocity-agent")
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
    title="Velocity Agent",
    version="0.1.0",
    description="Transaction velocity fraud risk microservice.",
)


class VelocityEvaluateRequest(BaseModel):
    txn_id: str = Field(..., min_length=1)
    account_id: str | None = None


@app.on_event("startup")
def startup_check_db() -> None:
    app.state.db_available = False
    if engine is None:
        logger.error("DATABASE_URL is not configured")
        return

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        app.state.db_available = True
        print("✅ Velocity Agent connected to Postgres")
    except Exception:
        logger.error("Velocity Agent failed to connect to Postgres:\n%s", traceback.format_exc())


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/evaluate")
def evaluate(body: VelocityEvaluateRequest) -> dict:
    if engine is None or not getattr(app.state, "db_available", False):
        raise HTTPException(status_code=503, detail="Database unavailable")

    started = time.perf_counter()
    try:
        with engine.connect() as connection:
            result = evaluate_velocity(body.txn_id, connection)
    except TransactionNotFoundError:
        raise HTTPException(status_code=404, detail="Transaction not found") from None
    except SQLAlchemyError:
        logger.error("Database error while evaluating txn_id=%s:\n%s", body.txn_id, traceback.format_exc())
        app.state.db_available = False
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    except Exception:
        logger.error("Unexpected error while evaluating txn_id=%s:\n%s", body.txn_id, traceback.format_exc())
        raise

    latency_ms = int((time.perf_counter() - started) * 1000)
    response = {**result, "latency_ms": latency_ms}
    logger.info(
        "Evaluated txn_id=%s risk=%s confidence=%s latency_ms=%s",
        body.txn_id,
        response["risk_score"],
        response["confidence"],
        latency_ms,
    )
    return response
