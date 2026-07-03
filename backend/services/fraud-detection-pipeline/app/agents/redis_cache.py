from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any, Iterator

import redis  # type: ignore[reportMissingImports]
from redis.exceptions import RedisError, TimeoutError  # type: ignore[reportMissingImports]
from sqlalchemy import text


logger = logging.getLogger("fraud-detection-pipeline.velocity")

redis_pool = redis.ConnectionPool(host="localhost", port=6379, db=0)

VELOCITY_KEY_TEMPLATE = "velocity:{account_id}:{txn_id}"
COUNTER_FIELDS = (
    "txn_count_1m",
    "txn_count_5m",
    "txn_count_1h",
    "txn_count_24h",
)


def create_redis_client(
    host: str = "localhost",
    port: int = 6379,
    db: int = 0,
    socket_timeout: float = 0.005,
) -> redis.Redis:
    pool = redis.ConnectionPool(
        host=host,
        port=port,
        db=db,
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_timeout,
        decode_responses=True,
    )
    return redis.Redis(connection_pool=pool)


def load_velocity_snapshots_to_redis(db_connection, redis_conn) -> int:
    rows = _fetch_all_velocity_snapshots(db_connection)
    loaded = 0

    for row in rows:
        snapshot = _row_to_dict(row)
        if not snapshot:
            continue

        txn_id = snapshot.get("txn_id")
        account_id = snapshot.get("account_id")
        if txn_id is None or account_id is None:
            continue

        try:
            _cache_velocity_snapshot(snapshot, redis_conn)
            loaded += 1
        except (RedisError, TimeoutError):
            logger.warning(
                "WARN: Redis write failed while loading velocity:%s:%s",
                account_id,
                txn_id,
            )

    print(f"Loaded {loaded} velocity records into Redis")
    logger.info("INFO: Velocity Agent loaded %s velocity records into Redis", loaded)
    return loaded


def get_velocity_data(account_id: str, txn_id: str, redis_conn, db_connection) -> dict[str, Any] | None:
    key = _velocity_key(account_id, txn_id)

    if redis_conn is not None:
        try:
            cached = redis_conn.get(key)
            if cached:
                snapshot = json.loads(cached)
                snapshot["_source"] = "redis"
                return snapshot
            logger.warning("WARN: Redis miss, using Postgres fallback for %s", key)
        except (RedisError, TimeoutError, json.JSONDecodeError):
            logger.warning("WARN: Redis unavailable for %s, using Postgres fallback", key)

    snapshot = _fetch_velocity_snapshot(account_id, txn_id, db_connection)
    if snapshot is None:
        return None

    snapshot["_source"] = "postgres_fallback"
    if redis_conn is not None:
        try:
            _cache_velocity_snapshot(snapshot, redis_conn)
        except (RedisError, TimeoutError):
            logger.warning("WARN: Redis write-through failed for %s", key)

    return snapshot


def _fetch_all_velocity_snapshots(db_connection) -> list[dict[str, Any]]:
    with _connection(db_connection) as connection:
        result = connection.execute(text("SELECT * FROM velocity_snapshots"))
        return [_row_to_dict(row) for row in result.fetchall()]


def _fetch_velocity_snapshot(
    account_id: str,
    txn_id: str,
    db_connection,
) -> dict[str, Any] | None:
    with _connection(db_connection) as connection:
        result = connection.execute(
            text(
                """
                SELECT *
                FROM velocity_snapshots
                WHERE account_id = :account_id AND txn_id = :txn_id
                """
            ),
            {"account_id": account_id, "txn_id": txn_id},
        )
        return _row_to_dict(result.fetchone())


def _cache_velocity_snapshot(snapshot: dict[str, Any], redis_conn) -> None:
    clean_snapshot = {key: value for key, value in snapshot.items() if not key.startswith("_")}
    account_id = clean_snapshot["account_id"]
    txn_id = clean_snapshot["txn_id"]
    redis_conn.set(_velocity_key(account_id, txn_id), json.dumps(clean_snapshot, default=str))

    for field in COUNTER_FIELDS:
        if field in clean_snapshot and clean_snapshot[field] is not None:
            suffix = field.replace("txn_", "")
            redis_conn.set(f"user:{account_id}:{suffix}", clean_snapshot[field])


def _velocity_key(account_id: str, txn_id: str) -> str:
    return VELOCITY_KEY_TEMPLATE.format(account_id=account_id, txn_id=txn_id)


@contextmanager
def _connection(db_connection) -> Iterator[Any]:
    if hasattr(db_connection, "connect"):
        with db_connection.connect() as connection:
            yield connection
    else:
        yield db_connection


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    return dict(row)
