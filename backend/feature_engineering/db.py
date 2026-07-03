"""Postgres helpers for the feature-engineering layer.

Responsibilities:
- idempotent creation of the four output tables (``CREATE TABLE IF NOT EXISTS``)
- COPY-speed bulk upserts (temp table + ``ON CONFLICT ... DO UPDATE``), never
  row-by-row INSERTs
- audit rows in ``feature_pipeline_runs`` so every engineered table is
  traceable to the code+config that produced it

Postgres stays the source of truth; Redis only ever holds data that these
tables (or the source tables) can reconstruct.
"""

from __future__ import annotations

import io
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

import pandas as pd
import psycopg2
import psycopg2.extensions

from feature_engineering.config import config_version, load_config

logger = logging.getLogger(__name__)

RUNS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS feature_pipeline_runs (
    run_id UUID PRIMARY KEY,
    table_written TEXT NOT NULL,
    row_count BIGINT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    feature_config_version TEXT NOT NULL,
    notes TEXT
)
"""

BASELINE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS account_baseline_daily (
    account_id TEXT NOT NULL,
    baseline_date DATE NOT NULL,
    avg_txn_amount_30d_npr DOUBLE PRECISION,
    std_txn_amount_30d_npr DOUBLE PRECISION,
    n_txn_30d INTEGER,
    avg_km_from_home_90d DOUBLE PRECISION,
    std_km_from_home_90d DOUBLE PRECISION,
    n_geo_90d INTEGER,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, baseline_date)
)
"""


@contextmanager
def get_conn(dsn: str | None = None) -> Iterator[psycopg2.extensions.connection]:
    """Yield a connection to the feature database; commits on clean exit."""
    conn = psycopg2.connect(dsn or load_config()["database"]["dsn"])
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_table(conn: psycopg2.extensions.connection, ddl: str) -> None:
    """Run an idempotent CREATE TABLE IF NOT EXISTS statement."""
    with conn.cursor() as cur:
        cur.execute(ddl)


def bulk_upsert(
    conn: psycopg2.extensions.connection,
    table: str,
    df: pd.DataFrame,
    conflict_cols: tuple[str, ...],
) -> int:
    """Upsert ``df`` into ``table`` at COPY speed.

    Loads into a TEMP table with COPY, then a single
    ``INSERT ... ON CONFLICT (pk) DO UPDATE`` so reruns are safe.
    Returns the number of rows shipped.
    """
    if df.empty:
        return 0
    cols = list(df.columns)
    quoted = ", ".join(f'"{c}"' for c in cols)
    tmp = f"_upsert_{uuid.uuid4().hex[:8]}"
    updates = ", ".join(
        f'"{c}" = EXCLUDED."{c}"' for c in cols if c not in conflict_cols
    )
    conflict = ", ".join(f'"{c}"' for c in conflict_cols)

    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False, na_rep="\\N")
    buf.seek(0)

    with conn.cursor() as cur:
        cur.execute(
            f'CREATE TEMP TABLE "{tmp}" AS SELECT {quoted} FROM "{table}" WITH NO DATA'
        )
        cur.copy_expert(
            f'COPY "{tmp}" ({quoted}) FROM STDIN WITH (FORMAT csv, NULL \'\\N\')', buf
        )
        cur.execute(
            f'INSERT INTO "{table}" ({quoted}) SELECT {quoted} FROM "{tmp}" '
            f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
        )
        cur.execute(f'DROP TABLE "{tmp}"')
    return len(df)


def record_run(
    conn: psycopg2.extensions.connection,
    table_written: str,
    row_count: int,
    started_at: datetime,
    notes: str | None = None,
) -> str:
    """Insert an audit row into feature_pipeline_runs; returns the run_id."""
    ensure_table(conn, RUNS_TABLE_DDL)
    run_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO feature_pipeline_runs
               (run_id, table_written, row_count, started_at, finished_at,
                feature_config_version, notes)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                run_id,
                table_written,
                row_count,
                started_at,
                datetime.now(timezone.utc),
                config_version(),
                notes,
            ),
        )
    logger.info(
        "feature_pipeline_runs: %s rows -> %s (run_id=%s, config=%s)",
        row_count,
        table_written,
        run_id,
        config_version(),
    )
    return run_id
