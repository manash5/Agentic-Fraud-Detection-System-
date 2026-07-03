from __future__ import annotations

import logging
import os
import traceback
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from redis.exceptions import RedisError, TimeoutError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import QueuePool

from app.agents.redis_cache import create_redis_client, load_velocity_snapshots_to_redis
from app.agents.velocity_agent import (
    TransactionVelocityNotFoundError,
    evaluate_velocity,
)


logger = logging.getLogger("fraud-detection-pipeline")
logging.basicConfig(level=logging.INFO)

BACKEND_DIR = Path(__file__).resolve().parents[3]
load_dotenv(BACKEND_DIR / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL")
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

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
    title="Unified Fraud Detection Pipeline",
    version="0.1.0",
    description="Unified fraud-detection-pipeline service with Redis-backed velocity agent.",
)


class VelocityEvaluateRequest(BaseModel):
    txn_id: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)


@app.on_event("startup")
def startup_load_velocity_agent() -> None:
    app.state.db_available = False
    app.state.redis_available = False
    app.state.redis_conn = None

    if engine is None:
        logger.error("DATABASE_URL is not configured")
        return

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            app.state.db_available = True

            try:
                redis_conn = create_redis_client(REDIS_HOST, REDIS_PORT)
                redis_conn.ping()
                load_velocity_snapshots_to_redis(connection, redis_conn)
                app.state.redis_conn = redis_conn
                app.state.redis_available = True
                print("✅ Velocity Agent initialized: Redis loaded with velocity_snapshots")
            except (RedisError, TimeoutError):
                logger.warning(
                    "WARN: Redis connection failed at startup; Velocity Agent will use Postgres-only mode"
                )
    except Exception:
        logger.error("Fraud detection pipeline failed to connect to Postgres:\n%s", traceback.format_exc())


@app.on_event("shutdown")
def shutdown_connections() -> None:
    redis_conn = getattr(app.state, "redis_conn", None)
    if redis_conn is not None:
        redis_conn.close()

    if engine is not None:
        engine.dispose()


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "fraud-detection-pipeline"}


@app.post("/evaluate/velocity")
def evaluate_velocity_risk(body: VelocityEvaluateRequest) -> dict:
    if engine is None or not getattr(app.state, "db_available", False):
        raise HTTPException(status_code=503, detail="Database unavailable")

    redis_conn = getattr(app.state, "redis_conn", None)
    if not getattr(app.state, "redis_available", False):
        redis_conn = None

    try:
        with engine.connect() as connection:
            return evaluate_velocity(body.txn_id, body.account_id, redis_conn, connection)
    except TransactionVelocityNotFoundError:
        raise HTTPException(status_code=404, detail="Transaction velocity data not found") from None
    except SQLAlchemyError:
        logger.error("Database error while evaluating txn_id=%s:\n%s", body.txn_id, traceback.format_exc())
        app.state.db_available = False
        raise HTTPException(status_code=503, detail="Database unavailable") from None
