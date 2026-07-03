from __future__ import annotations

import logging
import os
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import QueuePool

from app.geo_check import TransactionNotFoundError, evaluate_geo


logger = logging.getLogger("geo-agent")
logging.basicConfig(level=logging.INFO)

BACKEND_DIR = Path(__file__).resolve().parents[3]
load_dotenv(BACKEND_DIR / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL")
NEO4J_URI = os.environ.get("NEO4J_URI")
NEO4J_USERNAME = os.environ.get("NEO4J_USERNAME")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE") or None

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

neo4j_driver = (
    GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    if NEO4J_URI and NEO4J_USERNAME and NEO4J_PASSWORD
    else None
)

app = FastAPI(
    title="Geo Agent",
    version="0.1.0",
    description="Geographic, device, and graph-context fraud risk microservice.",
)


class GeoEvaluateRequest(BaseModel):
    txn_id: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)


@app.on_event("startup")
def startup_check_connections() -> None:
    app.state.db_available = False
    app.state.neo4j_available = False

    if engine is None:
        logger.error("DATABASE_URL is not configured")
    else:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            app.state.db_available = True
        except Exception:
            logger.error("Geo Agent failed to connect to Postgres:\n%s", traceback.format_exc())

    if neo4j_driver is None:
        logger.error("Neo4j credentials are not fully configured")
    else:
        try:
            with neo4j_driver.session(database=NEO4J_DATABASE) as session:
                session.run("RETURN 1").consume()
            app.state.neo4j_available = True
        except Exception:
            logger.error("Geo Agent failed to connect to Neo4j:\n%s", traceback.format_exc())

    if app.state.db_available and app.state.neo4j_available:
        print("✅ Geo Agent connected to Postgres and Neo4j")


@app.on_event("shutdown")
def shutdown_connections() -> None:
    if neo4j_driver is not None:
        neo4j_driver.close()


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/evaluate")
def evaluate(body: GeoEvaluateRequest) -> dict:
    if engine is None or not getattr(app.state, "db_available", False):
        raise HTTPException(status_code=503, detail="Database unavailable")

    active_neo4j_driver = neo4j_driver if getattr(app.state, "neo4j_available", False) else None
    started = time.perf_counter()
    try:
        with engine.connect() as connection:
            result = evaluate_geo(
                body.txn_id,
                body.account_id,
                connection,
                active_neo4j_driver,
            )
    except TransactionNotFoundError:
        raise HTTPException(status_code=404, detail="Transaction not found") from None
    except SQLAlchemyError:
        logger.error("Database error while evaluating txn_id=%s:\n%s", body.txn_id, traceback.format_exc())
        app.state.db_available = False
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    except Neo4jError:
        logger.error("Neo4j error while evaluating txn_id=%s:\n%s", body.txn_id, traceback.format_exc())
        app.state.neo4j_available = False
        with engine.connect() as connection:
            result = evaluate_geo(body.txn_id, body.account_id, connection, None)
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
