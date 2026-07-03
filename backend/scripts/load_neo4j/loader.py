"""Load fraud detection account graph data into a local Neo4j instance.

Run from the backend directory:

    python -m scripts.load_neo4j.loader
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = BACKEND_ROOT / ".env"
PROCESSED_DATASET_DIR = BACKEND_ROOT / "datasets_processed"
REFERENCE_DATASET_DIR = BACKEND_ROOT / "datasets"
NODES_CSV = PROCESSED_DATASET_DIR / "account_graph_nodes.csv"
EDGES_CSV = PROCESSED_DATASET_DIR / "account_graph_edges.csv"
COMM042_JSON = REFERENCE_DATASET_DIR / "comm042_ring_members.json"
BATCH_SIZE = 100

NODE_COLUMNS = {
    "id",
    "type",
    "risk_tier",
    "kyc_tier",
    "degree_in",
    "degree_out",
    "total_received_npr",
    "total_sent_npr",
    "is_fraud_seed",
}
EDGE_COLUMNS = {
    "source",
    "target",
    "txn_id",
    "amount_npr",
    "timestamp",
    "txn_type",
    "edge_weight",
    "is_first_transfer_to_target",
    "within_24h_reciprocal",
}


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description="Load processed fraud detection account graph data into Neo4j."
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete existing Account nodes and their relationships before loading.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt when used with --clear.",
    )
    parser.add_argument(
        "--database",
        help="Neo4j database name. Defaults to NEO4J_DATABASE when set, otherwise the DBMS default.",
    )
    return parser.parse_args()


def require_env_var(name: str) -> str:
    """Return a required environment variable or raise a clear error."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable {name} in {ENV_PATH}")
    return value


def load_neo4j_config() -> tuple[str, str, str, str | None]:
    """Load Neo4j connection settings from backend/.env."""
    if not ENV_PATH.exists():
        raise FileNotFoundError(f"Could not find environment file: {ENV_PATH}")

    load_dotenv(ENV_PATH)
    return (
        require_env_var("NEO4J_URI"),
        require_env_var("NEO4J_USERNAME"),
        require_env_var("NEO4J_PASSWORD"),
        os.getenv("NEO4J_DATABASE") or None,
    )


def connect(uri: str, username: str, password: str, database: str | None) -> Driver:
    """Create a Neo4j driver and verify the connection."""
    driver = GraphDatabase.driver(uri, auth=(username, password))
    try:
        with driver.session(database=database) as session:
            session.run("RETURN 1 AS ok").consume()
    except (ServiceUnavailable, Neo4jError, OSError) as exc:
        driver.close()
        database_hint = f" database '{database}'" if database else ""
        raise ConnectionError(f"Could not connect to Neo4j at {uri}{database_hint}: {exc}") from exc

    database_message = f" database '{database}'" if database else " default database"
    print(f"Connected to Neo4j at {uri} using{database_message}.")
    return driver


def confirm_clear(skip_confirmation: bool) -> None:
    """Ask for confirmation before deleting existing graph data."""
    if skip_confirmation:
        return

    prompt = (
        "This will delete all Account nodes and their relationships in Neo4j. "
        "Type CLEAR to continue: "
    )
    confirmation = input(prompt)
    if confirmation != "CLEAR":
        raise RuntimeError("Clear cancelled; no data was deleted.")


def clear_existing_data(driver: Driver, database: str | None, skip_confirmation: bool) -> None:
    """Delete existing Account nodes and relationships."""
    confirm_clear(skip_confirmation)
    query = "MATCH (a:Account) DETACH DELETE a"
    with driver.session(database=database) as session:
        session.run(query).consume()
    print("Cleared existing Account nodes and relationships.")


def ensure_account_constraint(driver: Driver, database: str | None) -> None:
    """Create a uniqueness constraint for Account.id when it does not already exist."""
    query = """
    CREATE CONSTRAINT account_id_unique IF NOT EXISTS
    FOR (a:Account) REQUIRE a.id IS UNIQUE
    """
    with driver.session(database=database) as session:
        session.run(query).consume()
    print("Ensured uniqueness constraint on Account.id.")


def validate_file(path: Path) -> None:
    """Ensure an input file exists before parsing."""
    if not path.exists():
        raise FileNotFoundError(f"Required input file not found: {path}")


def read_csv(path: Path, required_columns: set[str]) -> pd.DataFrame:
    """Read and validate a CSV input file."""
    validate_file(path)
    frame = pd.read_csv(path)
    missing_columns = sorted(required_columns.difference(frame.columns))
    if missing_columns:
        missing = missing_columns[0]
        raise ValueError(f"Missing column '{missing}' in {path.name}")
    return frame


def parse_bool(value: Any) -> bool:
    """Convert common CSV boolean representations into bool."""
    if pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def parse_int(value: Any) -> int:
    """Convert a CSV value into int, treating blanks as zero."""
    if pd.isna(value) or value == "":
        return 0
    return int(value)


def parse_float(value: Any) -> float:
    """Convert a CSV value into float, treating blanks as zero."""
    if pd.isna(value) or value == "":
        return 0.0
    return float(value)


def parse_string(value: Any) -> str:
    """Convert a CSV value into a stripped string."""
    if pd.isna(value):
        return ""
    return str(value).strip()


def chunked(rows: Sequence[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    """Yield rows in fixed-size batches."""
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def prepare_node_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert node CSV rows into Neo4j-ready dictionaries."""
    rows: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        account_id = parse_string(row["id"])
        if not account_id:
            raise ValueError("Encountered account row with blank id.")
        rows.append(
            {
                "id": account_id,
                "type": parse_string(row["type"]),
                "risk_tier": parse_string(row["risk_tier"]),
                "kyc_tier": parse_string(row["kyc_tier"]),
                "degree_in": parse_int(row["degree_in"]),
                "degree_out": parse_int(row["degree_out"]),
                "total_received_npr": parse_float(row["total_received_npr"]),
                "total_sent_npr": parse_float(row["total_sent_npr"]),
                "is_fraud_seed": parse_bool(row["is_fraud_seed"]),
            }
        )
    return rows


def prepare_edge_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert edge CSV rows into Neo4j-ready dictionaries."""
    rows: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        source = parse_string(row["source"])
        target = parse_string(row["target"])
        txn_id = parse_string(row["txn_id"])
        if not source or not target or not txn_id:
            raise ValueError("Encountered transfer row with blank source, target, or txn_id.")
        rows.append(
            {
                "source": source,
                "target": target,
                "txn_id": txn_id,
                "amount_npr": parse_float(row["amount_npr"]),
                "timestamp": parse_string(row["timestamp"]),
                "txn_type": parse_string(row["txn_type"]),
                "edge_weight": parse_float(row["edge_weight"]),
                "is_first_transfer_to_target": parse_bool(row["is_first_transfer_to_target"]),
                "within_24h_reciprocal": parse_bool(row["within_24h_reciprocal"]),
            }
        )
    return rows


def load_nodes(driver: Driver, database: str | None) -> int:
    """Load Account nodes from account_graph_nodes.csv."""
    frame = read_csv(NODES_CSV, NODE_COLUMNS)
    rows = prepare_node_rows(frame)
    query = """
    UNWIND $rows AS row
    MERGE (a:Account {id: row.id})
    SET a.type = row.type,
        a.risk_tier = row.risk_tier,
        a.kyc_tier = row.kyc_tier,
        a.degree_in = row.degree_in,
        a.degree_out = row.degree_out,
        a.total_received_npr = row.total_received_npr,
        a.total_sent_npr = row.total_sent_npr,
        a.is_fraud_seed = row.is_fraud_seed
    """

    total = len(rows)
    loaded = 0
    with driver.session(database=database) as session:
        for batch in chunked(rows, BATCH_SIZE):
            session.run(query, rows=batch).consume()
            loaded += len(batch)
            if loaded % 100 == 0 or loaded == total:
                print(f"Loaded {loaded}/{total} account nodes")
    return loaded


def load_edges(driver: Driver, database: str | None) -> int:
    """Load TRANSFER relationships from account_graph_edges.csv."""
    frame = read_csv(EDGES_CSV, EDGE_COLUMNS)
    rows = prepare_edge_rows(frame)
    query = """
    UNWIND $rows AS row
    MATCH (source:Account {id: row.source})
    MATCH (target:Account {id: row.target})
    MERGE (source)-[r:TRANSFER {txn_id: row.txn_id}]->(target)
    SET r.amount_npr = row.amount_npr,
        r.timestamp = row.timestamp,
        r.txn_type = row.txn_type,
        r.edge_weight = row.edge_weight,
        r.is_first_transfer_to_target = row.is_first_transfer_to_target,
        r.within_24h_reciprocal = row.within_24h_reciprocal
    """

    total = len(rows)
    loaded = 0
    with driver.session(database=database) as session:
        for batch in chunked(rows, BATCH_SIZE):
            session.run(query, rows=batch).consume()
            loaded += len(batch)
            if loaded % 100 == 0 or loaded == total:
                print(f"Loaded {loaded}/{total} transfer relationships")
    return loaded


def load_comm042_ring() -> tuple[str, list[str]]:
    """Load COMM-042 ring member metadata from JSON."""
    validate_file(COMM042_JSON)
    with COMM042_JSON.open(encoding="utf-8") as file:
        payload = json.load(file)

    collector = parse_string(payload.get("collector_account"))
    ring_members = [parse_string(account) for account in payload.get("ring_members", [])]
    ring_members = [account for account in ring_members if account]
    if not collector:
        raise ValueError(f"Missing collector_account in {COMM042_JSON}")
    return collector, ring_members


def mark_comm042_ring_members(driver: Driver, database: str | None) -> int:
    """Mark COMM-042 ring members and collector as fraud seed accounts."""
    collector, ring_members = load_comm042_ring()
    accounts = sorted({collector, *ring_members})
    query = """
    MATCH (a:Account)
    WHERE a.id IN $accounts
    SET a.is_fraud_seed = true,
        a.community_id = "COMM-042"
    RETURN count(a) AS marked_count
    """
    with driver.session(database=database) as session:
        result = session.run(query, accounts=accounts).single()

    marked_count = int(result["marked_count"]) if result else 0
    print(f"Marked {len(ring_members)} ring members + 1 collector as fraud seeds")
    if marked_count != len(accounts):
        print(f"Warning: marked {marked_count}/{len(accounts)} COMM-042 accounts found in Neo4j.")
    return marked_count


def get_summary_statistics(driver: Driver, database: str | None) -> dict[str, Any]:
    """Return graph summary statistics after loading."""
    query = """
    MATCH (a:Account)
    OPTIONAL MATCH ()-[r:TRANSFER]->()
    WITH count(DISTINCT a) AS account_count,
         count(DISTINCT r) AS transfer_count,
         count(DISTINCT CASE WHEN a.is_fraud_seed = true THEN a END) AS fraud_seed_count,
         avg(a.degree_in) AS avg_degree_in,
         avg(a.degree_out) AS avg_degree_out
    RETURN account_count,
           transfer_count,
           fraud_seed_count,
           coalesce(avg_degree_in, 0.0) AS avg_degree_in,
           coalesce(avg_degree_out, 0.0) AS avg_degree_out
    """
    with driver.session(database=database) as session:
        result = session.run(query).single()

    if not result:
        return {
            "account_count": 0,
            "transfer_count": 0,
            "fraud_seed_count": 0,
            "avg_degree_in": 0.0,
            "avg_degree_out": 0.0,
        }
    return dict(result)


def print_summary(stats: dict[str, Any]) -> None:
    """Print load summary statistics."""
    print("\nNeo4j load summary")
    print("------------------")
    print(f"Total Account nodes created/updated: {stats['account_count']}")
    print(f"Total TRANSFER relationships created/updated: {stats['transfer_count']}")
    print(f"Fraud seed nodes count: {stats['fraud_seed_count']}")
    print(f"Average degree_in: {float(stats['avg_degree_in']):.2f}")
    print(f"Average degree_out: {float(stats['avg_degree_out']):.2f}")


def main() -> None:
    """Load processed graph CSVs into Neo4j."""
    args = parse_args()
    uri, username, password, env_database = load_neo4j_config()
    database = args.database or env_database
    driver = connect(uri, username, password, database)

    try:
        ensure_account_constraint(driver, database)
        if args.clear:
            clear_existing_data(driver, database, skip_confirmation=args.yes)
        else:
            print("No --clear flag provided; loader will overwrite matching nodes/edges via MERGE.")

        load_nodes(driver, database)
        load_edges(driver, database)
        mark_comm042_ring_members(driver, database)
        print_summary(get_summary_statistics(driver, database))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
