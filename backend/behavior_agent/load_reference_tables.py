"""One-off loader: reference tables the Behavior Agent needs that were never
loaded into Postgres.

Step-0 of the Behavior Agent build found three sources that exist only as
files under ``datasets/`` but are required at inference time:

    customer_profiles.csv    -> customer_profiles      (XGBoost + LSTM static)
    otp_logs.csv             -> otp_logs               (LSTM sequential branch)
    account_graph_nodes.csv  -> account_graph_nodes    (LSTM static branch)

They are small (50k / 43k / 50k rows) account- or txn-level lookups, so the
right materialization is Postgres tables with indexes — not a 2M-row joined
CSV that no 100ms endpoint could read anyway.

Idempotent: each table is dropped and rebuilt from its file. Run from
``backend/``::

    uv run python -m behavior_agent.load_reference_tables
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import asyncpg
import pandas as pd

from behavior_agent.config import load_config, pg_connect_kwargs

BACKEND = Path(__file__).resolve().parents[1]
RAW_DIR = BACKEND / "datasets"

DDL = {
    "customer_profiles": """
        CREATE TABLE customer_profiles (
            account_id                   text PRIMARY KEY,
            customer_since               date,
            age_group                    text,
            district                     text,
            province                     text,
            occupation_category          text,
            monthly_income_band_npr      text,
            kyc_tier                     text,
            risk_tier                    text,
            avg_monthly_txn_count        double precision,
            avg_monthly_txn_value_npr    double precision,
            primary_channel              text,
            international_txn_history    boolean,
            has_linked_esewa             boolean,
            has_linked_khalti            boolean,
            num_beneficiaries_registered integer,
            last_profile_update          date,
            is_dormant                   boolean,
            churn_risk_score             double precision
        )
    """,
    "otp_logs": """
        CREATE TABLE otp_logs (
            otp_event_id         text,
            txn_id               text PRIMARY KEY,
            account_id           text,
            trigger_reason       text,
            otp_channel_1        text,
            otp_channel_2        text,
            channel_1_sent_at    timestamp,
            channel_2_sent_at    timestamp,
            channel_1_verified_at timestamp,
            channel_2_verified_at timestamp,
            channel_1_status     text,
            channel_2_status     text,
            final_decision       text,
            resolution_time_ms   double precision,
            attempt_count_ch1    integer,
            attempt_count_ch2    integer,
            sim_swap_suspected   boolean
        )
    """,
    "account_graph_nodes": """
        CREATE TABLE account_graph_nodes (
            id                 text PRIMARY KEY,
            type               text,
            risk_tier          text,
            kyc_tier           text,
            degree_in          integer,
            degree_out         integer,
            total_received_npr double precision,
            total_sent_npr     double precision,
            is_fraud_seed      boolean
        )
    """,
}

INDEXES = [
    "CREATE INDEX idx_otp_account ON otp_logs (account_id)",
]

FILES = {
    "customer_profiles": RAW_DIR / "customer_profiles.csv",
    "otp_logs": RAW_DIR / "otp_logs.csv",
    "account_graph_nodes": RAW_DIR / "account_graph_nodes.csv",
}

# The LSTM/XGBoost notebooks dedupe on these keys before use; mirror that here
# so the tables carry the same rows the models were trained against. otp_logs
# is deduped on txn_id (keep first in file order) exactly like the LSTM
# notebook's Stage 0 — it also lets the sequence query join it directly.
DEDUPE_KEY = {
    "customer_profiles": "account_id",
    "otp_logs": "txn_id",
    "account_graph_nodes": "id",
}


async def load_table(conn: asyncpg.Connection, table: str) -> int:
    df = pd.read_csv(FILES[table], low_memory=False)
    n_raw = len(df)
    df = df.drop_duplicates(DEDUPE_KEY[table], keep="first")
    if n_raw != len(df):
        print(f"  [{table}] dropped {n_raw - len(df)} duplicate rows on {DEDUPE_KEY[table]}")

    await conn.execute(f"DROP TABLE IF EXISTS {table}")
    await conn.execute(DDL[table])

    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False)
    buf.seek(0)
    await conn.copy_to_table(
        table, source=io.BytesIO(buf.getvalue().encode()), format="csv", null=""
    )
    n_db = await conn.fetchval(f"SELECT count(*) FROM {table}")
    assert n_db == len(df), f"{table}: wrote {len(df)} rows but table has {n_db} — aborting"
    print(f"  [{table}] loaded {n_db:,} rows")
    return n_db


async def main() -> None:
    cfg = load_config()
    conn = await asyncpg.connect(**pg_connect_kwargs(cfg["database"]["dsn"]))
    try:
        for table in DDL:
            await load_table(conn, table)
        for idx in INDEXES:
            await conn.execute(idx)
        print("  indexes created")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
