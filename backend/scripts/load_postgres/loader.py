"""Load cleaned fraud detection data into Neon Postgres.

Run from the backend directory:

    python -m scripts.load_postgres.loader
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from psycopg2.extensions import connection as PostgresConnection

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = BACKEND_ROOT / ".env"
PROCESSED_DATASET_DIR = BACKEND_ROOT / "datasets_processed"
BATCH_SIZE = 100


@dataclass(frozen=True)
class TableSpec:
    """Definition for one source file and destination Postgres table."""

    name: str
    source_file: str
    columns: tuple[str, ...]
    ddl_columns: tuple[str, ...]
    bool_columns: frozenset[str] = frozenset()
    optional_defaults: dict[str, Any] | None = None
    is_json: bool = False


TABLES: tuple[TableSpec, ...] = (
    TableSpec(
        name="transactions",
        source_file="transactions_raw_cleaned.csv",
        columns=(
            "txn_id",
            "timestamp",
            "account_id",
            "counterparty_id",
            "txn_type",
            "amount_npr",
            "currency",
            "channel",
            "device_id",
            "ip_address",
            "merchant_category_code",
            "terminal_id",
            "session_id",
            "auth_method",
            "response_code",
            "processing_time_ms",
            "is_international",
            "fx_rate",
            "notes",
        ),
        ddl_columns=(
            "txn_id TEXT",
            '"timestamp" TIMESTAMPTZ',
            "account_id TEXT",
            "counterparty_id TEXT",
            "txn_type TEXT",
            "amount_npr NUMERIC",
            "currency TEXT",
            "channel TEXT",
            "device_id TEXT",
            "ip_address INET",
            "merchant_category_code TEXT",
            "terminal_id TEXT",
            "session_id TEXT",
            "auth_method TEXT",
            "response_code TEXT",
            "processing_time_ms INTEGER",
            "is_international BOOLEAN",
            "fx_rate NUMERIC",
            "notes TEXT",
        ),
        bool_columns=frozenset({"is_international"}),
        optional_defaults={"notes": None},
    ),
    TableSpec(
        name="customers",
        source_file="customer_profiles.csv",
        columns=(
            "account_id",
            "customer_since",
            "age_group",
            "district",
            "province",
            "occupation_category",
            "monthly_income_band_npr",
            "kyc_tier",
            "risk_tier",
            "avg_monthly_txn_count",
            "avg_monthly_txn_value_npr",
            "primary_channel",
            "international_txn_history",
            "has_linked_esewa",
            "has_linked_khalti",
            "num_beneficiaries_registered",
            "last_profile_update",
            "is_dormant",
            "churn_risk_score",
        ),
        ddl_columns=(
            "account_id TEXT",
            "customer_since DATE",
            "age_group TEXT",
            "district TEXT",
            "province TEXT",
            "occupation_category TEXT",
            "monthly_income_band_npr TEXT",
            "kyc_tier TEXT",
            "risk_tier TEXT",
            "avg_monthly_txn_count NUMERIC",
            "avg_monthly_txn_value_npr NUMERIC",
            "primary_channel TEXT",
            "international_txn_history BOOLEAN",
            "has_linked_esewa BOOLEAN",
            "has_linked_khalti BOOLEAN",
            "num_beneficiaries_registered INTEGER",
            "last_profile_update TIMESTAMPTZ",
            "is_dormant BOOLEAN",
            "churn_risk_score NUMERIC",
        ),
        bool_columns=frozenset(
            {
                "international_txn_history",
                "has_linked_esewa",
                "has_linked_khalti",
                "is_dormant",
            }
        ),
    ),
    TableSpec(
        name="geo_events",
        source_file="geo_events.csv",
        columns=(
            "txn_id",
            "account_id",
            "timestamp",
            "ip_address",
            "ip_country",
            "ip_city",
            "ip_isp",
            "ip_asn",
            "latitude",
            "longitude",
            "accuracy_km",
            "is_vpn",
            "is_tor",
            "is_datacenter",
            "velocity_flag",
            "km_from_home_district",
            "prev_txn_km",
            "prev_txn_time_delta_min",
            "impossible_travel",
        ),
        ddl_columns=(
            "txn_id TEXT",
            "account_id TEXT",
            '"timestamp" TIMESTAMPTZ',
            "ip_address INET",
            "ip_country TEXT",
            "ip_city TEXT",
            "ip_isp TEXT",
            "ip_asn TEXT",
            "latitude DOUBLE PRECISION",
            "longitude DOUBLE PRECISION",
            "accuracy_km NUMERIC",
            "is_vpn BOOLEAN",
            "is_tor BOOLEAN",
            "is_datacenter BOOLEAN",
            "velocity_flag BOOLEAN",
            "km_from_home_district NUMERIC",
            "prev_txn_km NUMERIC",
            "prev_txn_time_delta_min NUMERIC",
            "impossible_travel BOOLEAN",
        ),
        bool_columns=frozenset(
            {"is_vpn", "is_tor", "is_datacenter", "velocity_flag", "impossible_travel"}
        ),
    ),
    TableSpec(
        name="velocity_snapshots",
        source_file="velocity_snapshots.csv",
        columns=(
            "txn_id",
            "account_id",
            "snapshot_time",
            "txn_count_1m",
            "txn_count_5m",
            "txn_count_15m",
            "txn_count_1h",
            "txn_count_24h",
            "txn_count_7d",
            "total_amount_1h_npr",
            "total_amount_24h_npr",
            "unique_counterparties_1h",
            "unique_counterparties_24h",
            "new_counterparty_flag",
            "max_single_txn_24h_npr",
            "avg_txn_amount_30d_npr",
            "std_txn_amount_30d_npr",
            "z_score_amount",
            "dormancy_break",
            "weekend_flag",
            "night_flag",
        ),
        ddl_columns=(
            "txn_id TEXT",
            "account_id TEXT",
            "snapshot_time TIMESTAMPTZ",
            "txn_count_1m INTEGER",
            "txn_count_5m INTEGER",
            "txn_count_15m INTEGER",
            "txn_count_1h INTEGER",
            "txn_count_24h INTEGER",
            "txn_count_7d INTEGER",
            "total_amount_1h_npr NUMERIC",
            "total_amount_24h_npr NUMERIC",
            "unique_counterparties_1h INTEGER",
            "unique_counterparties_24h INTEGER",
            "new_counterparty_flag BOOLEAN",
            "max_single_txn_24h_npr NUMERIC",
            "avg_txn_amount_30d_npr NUMERIC",
            "std_txn_amount_30d_npr NUMERIC",
            "z_score_amount NUMERIC",
            "dormancy_break BOOLEAN",
            "weekend_flag BOOLEAN",
            "night_flag BOOLEAN",
        ),
        bool_columns=frozenset(
            {"new_counterparty_flag", "dormancy_break", "weekend_flag", "night_flag"}
        ),
    ),
    TableSpec(
        name="fraud_labels",
        source_file="fraud_labels_train.csv",
        columns=(
            "txn_id",
            "is_fraud",
            "fraud_type",
            "fraud_confidence",
            "confirmed_by",
            "fraud_date_confirmed",
            "financial_loss_npr",
            "recovery_status",
        ),
        ddl_columns=(
            "txn_id TEXT",
            "is_fraud BOOLEAN",
            "fraud_type TEXT",
            "fraud_confidence NUMERIC",
            "confirmed_by TEXT",
            "fraud_date_confirmed TIMESTAMPTZ",
            "financial_loss_npr NUMERIC",
            "recovery_status TEXT",
        ),
        bool_columns=frozenset({"is_fraud"}),
    ),
    TableSpec(
        name="device_fingerprints",
        source_file="device_fingerprints.json",
        columns=(
            "device_id",
            "first_seen",
            "last_seen",
            "device_type",
            "os",
            "app_version",
            "locale",
            "timezone",
            "is_rooted_or_jailbroken",
            "vpn_detected",
            "tor_exit_node",
            "biometric_enrolled",
            "num_accounts_seen_on_device",
            "is_shared_device",
            "risk_signals",
        ),
        ddl_columns=(
            "device_id TEXT",
            "first_seen TIMESTAMPTZ",
            "last_seen TIMESTAMPTZ",
            "device_type TEXT",
            "os TEXT",
            "app_version TEXT",
            "locale TEXT",
            "timezone TEXT",
            "is_rooted_or_jailbroken BOOLEAN",
            "vpn_detected BOOLEAN",
            "tor_exit_node BOOLEAN",
            "biometric_enrolled BOOLEAN",
            "num_accounts_seen_on_device INTEGER",
            "is_shared_device BOOLEAN",
            "risk_signals TEXT",
        ),
        bool_columns=frozenset(
            {
                "is_rooted_or_jailbroken",
                "vpn_detected",
                "tor_exit_node",
                "biometric_enrolled",
                "is_shared_device",
            }
        ),
        is_json=True,
    ),
    TableSpec(
        name="otp_logs",
        source_file="otp_logs.csv",
        columns=(
            "otp_event_id",
            "txn_id",
            "account_id",
            "trigger_reason",
            "otp_channel_1",
            "otp_channel_2",
            "channel_1_sent_at",
            "channel_2_sent_at",
            "channel_1_verified_at",
            "channel_2_verified_at",
            "channel_1_status",
            "channel_2_status",
            "final_decision",
            "resolution_time_ms",
            "attempt_count_ch1",
            "attempt_count_ch2",
            "sim_swap_suspected",
        ),
        ddl_columns=(
            "otp_event_id TEXT",
            "txn_id TEXT",
            "account_id TEXT",
            "trigger_reason TEXT",
            "otp_channel_1 TEXT",
            "otp_channel_2 TEXT",
            "channel_1_sent_at TIMESTAMPTZ",
            "channel_2_sent_at TIMESTAMPTZ",
            "channel_1_verified_at TIMESTAMPTZ",
            "channel_2_verified_at TIMESTAMPTZ",
            "channel_1_status TEXT",
            "channel_2_status TEXT",
            "final_decision TEXT",
            "resolution_time_ms INTEGER",
            "attempt_count_ch1 INTEGER",
            "attempt_count_ch2 INTEGER",
            "sim_swap_suspected BOOLEAN",
        ),
        bool_columns=frozenset({"sim_swap_suspected"}),
    ),
)

INDEX_STATEMENTS: tuple[str, ...] = (
    "CREATE INDEX idx_transactions_txn_id ON transactions(txn_id)",
    "CREATE INDEX idx_transactions_account_id ON transactions(account_id)",
    "CREATE INDEX idx_customers_account_id ON customers(account_id)",
    "CREATE INDEX idx_geo_events_txn_id ON geo_events(txn_id)",
    "CREATE INDEX idx_velocity_snapshots_txn_id ON velocity_snapshots(txn_id)",
)


def require_env_var(name: str) -> str:
    """Return a required environment variable or raise a clear error."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable {name} in {ENV_PATH}")
    return value


def database_url_with_sslmode(database_url: str) -> str:
    """Add sslmode=require when the connection string does not already set it."""
    parsed = urlparse(database_url)
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query_keys = {key.lower() for key, _ in query_pairs}
    if "sslmode" in query_keys:
        return database_url

    query_pairs.append(("sslmode", "require"))
    return urlunparse(parsed._replace(query=urlencode(query_pairs)))


def load_database_url() -> str:
    """Load the Neon connection string from backend/.env."""
    if not ENV_PATH.exists():
        raise FileNotFoundError(f"Could not find environment file: {ENV_PATH}")

    load_dotenv(ENV_PATH)
    return database_url_with_sslmode(require_env_var("DATABASE_URL"))


def connect(database_url: str) -> PostgresConnection:
    """Connect to Postgres and verify the connection."""
    try:
        conn = psycopg2.connect(database_url)
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except psycopg2.Error as exc:
        raise ConnectionError(f"Could not connect to Neon Postgres: {exc}") from exc

    print("✅ Connected to Neon Postgres")
    return conn


def quote_identifier(identifier: str) -> str:
    """Quote a trusted Postgres identifier."""
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def create_tables(conn: PostgresConnection) -> None:
    """Drop and recreate all loader-owned tables."""
    with conn.cursor() as cursor:
        for table in reversed(TABLES):
            cursor.execute(f"DROP TABLE IF EXISTS {quote_identifier(table.name)} CASCADE")

        for table in TABLES:
            ddl = ",\n                ".join(table.ddl_columns)
            cursor.execute(
                f"""
                CREATE TABLE {quote_identifier(table.name)} (
                {ddl}
                )
                """
            )
            print(f"Created table {table.name}")
    conn.commit()


def validate_file(path: Path) -> None:
    """Ensure an input file exists before parsing."""
    if not path.exists():
        raise FileNotFoundError(f"Required input file not found: {path}")


def validate_columns(frame: pd.DataFrame, table: TableSpec) -> None:
    """Ensure a source frame has all required destination columns."""
    optional_columns = set((table.optional_defaults or {}).keys())
    missing_columns = [
        column
        for column in table.columns
        if column not in frame.columns and column not in optional_columns
    ]
    if missing_columns:
        raise ValueError(f"Missing column '{missing_columns[0]}' in {table.source_file}")


def apply_optional_defaults(frame: pd.DataFrame, table: TableSpec) -> pd.DataFrame:
    """Fill destination columns that are allowed to be absent in the source file."""
    for column, default_value in (table.optional_defaults or {}).items():
        if column not in frame.columns:
            frame[column] = default_value
    return frame


def read_csv(table: TableSpec) -> pd.DataFrame:
    """Read and validate a CSV source file."""
    path = PROCESSED_DATASET_DIR / table.source_file
    validate_file(path)
    frame = pd.read_csv(path)
    validate_columns(frame, table)
    frame = apply_optional_defaults(frame, table)
    return frame.loc[:, table.columns]


def flatten_risk_signals(value: Any) -> str | None:
    """Convert JSON risk signals into a comma-separated string."""
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return ", ".join(f"{key}={item}" for key, item in value.items())
    return str(value)


def records_from_device_json(payload: Any) -> list[dict[str, Any]]:
    """Support common JSON layouts for device fingerprint records."""
    if isinstance(payload, list):
        return [record for record in payload if isinstance(record, dict)]

    if not isinstance(payload, dict):
        raise ValueError("device_fingerprints.json must contain a list or object payload")

    for key in ("devices", "device_fingerprints", "data", "records"):
        value = payload.get(key)
        if isinstance(value, list):
            return [record for record in value if isinstance(record, dict)]

    if all(isinstance(value, dict) for value in payload.values()):
        records = []
        for device_id, record in payload.items():
            records.append({"device_id": device_id, **record})
        return records

    return [payload]


def read_device_json(table: TableSpec) -> pd.DataFrame:
    """Read and flatten device_fingerprints.json."""
    path = PROCESSED_DATASET_DIR / table.source_file
    validate_file(path)
    with path.open(encoding="utf-8") as file:
        payload = json.load(file)

    records = records_from_device_json(payload)
    for record in records:
        record["risk_signals"] = flatten_risk_signals(record.get("risk_signals"))

    frame = pd.DataFrame.from_records(records)
    validate_columns(frame, table)
    frame = apply_optional_defaults(frame, table)
    return frame.loc[:, table.columns]


def parse_bool(value: Any) -> bool | None:
    """Convert common CSV/JSON boolean representations into bool."""
    if value is None or pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    normalized = str(value).strip().lower()
    if normalized in {"", "nan", "none", "null"}:
        return None
    if normalized in {"1", "true", "t", "yes", "y"}:
        return True
    if normalized in {"0", "false", "f", "no", "n"}:
        return False
    raise ValueError(f"Cannot parse boolean value: {value!r}")


def normalize_value(value: Any, column: str, bool_columns: frozenset[str]) -> Any:
    """Convert pandas/JSON values into psycopg2-friendly Python values."""
    if column in bool_columns:
        return parse_bool(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def frame_to_rows(frame: pd.DataFrame, table: TableSpec) -> list[tuple[Any, ...]]:
    """Convert a source frame into ordered insert rows."""
    rows: list[tuple[Any, ...]] = []
    for record in frame.to_dict(orient="records"):
        rows.append(
            tuple(normalize_value(record[column], column, table.bool_columns) for column in table.columns)
        )
    return rows


def chunked(rows: Sequence[tuple[Any, ...]], size: int) -> Iterable[list[tuple[Any, ...]]]:
    """Yield rows in fixed-size batches."""
    for start in range(0, len(rows), size):
        yield list(rows[start : start + size])


def load_table(conn: PostgresConnection, table: TableSpec) -> int:
    """Load one source file into its destination table."""
    frame = read_device_json(table) if table.is_json else read_csv(table)
    rows = frame_to_rows(frame, table)
    columns_sql = ", ".join(quote_identifier(column) for column in table.columns)
    placeholders = ", ".join(["%s"] * len(table.columns))
    insert_sql = f"INSERT INTO {quote_identifier(table.name)} ({columns_sql}) VALUES ({placeholders})"

    total = len(rows)
    loaded = 0
    with conn.cursor() as cursor:
        for batch in chunked(rows, BATCH_SIZE):
            cursor.executemany(insert_sql, batch)
            loaded += len(batch)
            if loaded % 100 == 0 or loaded == total:
                print(f"Loaded {loaded}/{total} {table.name}")
    conn.commit()
    return loaded


def create_indexes(conn: PostgresConnection) -> None:
    """Create query indexes after bulk loading finishes."""
    with conn.cursor() as cursor:
        for statement in INDEX_STATEMENTS:
            cursor.execute(statement)
    conn.commit()
    print("✅ Indexes created")


def get_row_counts(conn: PostgresConnection) -> dict[str, int]:
    """Return row counts for all loaded tables."""
    counts: dict[str, int] = {}
    with conn.cursor() as cursor:
        for table in TABLES:
            cursor.execute(f"SELECT COUNT(*) FROM {quote_identifier(table.name)}")
            counts[table.name] = int(cursor.fetchone()[0])
    return counts


def print_summary(counts: dict[str, int]) -> None:
    """Print load summary statistics."""
    print("\nPostgres load summary")
    print("---------------------")
    for table in TABLES:
        print(f"{table.name}: {counts[table.name]} rows")


def main() -> None:
    """Load all processed fraud detection datasets into Neon Postgres."""
    conn: PostgresConnection | None = None
    try:
        database_url = load_database_url()
        conn = connect(database_url)
        create_tables(conn)
        for table in TABLES:
            load_table(conn, table)
        create_indexes(conn)
        print_summary(get_row_counts(conn))
    except (ConnectionError, FileNotFoundError, RuntimeError, ValueError, psycopg2.Error) as exc:
        if conn is not None:
            conn.rollback()
        raise SystemExit(f"❌ Postgres load failed: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
