from __future__ import annotations

import logging
import os
import time
import traceback
from typing import Any

from sqlalchemy import text

try:
    from neo4j import Query
    from neo4j.exceptions import Neo4jError
except ImportError:  # pragma: no cover - keeps unit tests runnable before dependencies are installed.
    class Neo4jError(Exception):
        pass

    class Query:
        def __init__(self, text: str, timeout: float | None = None):
            self.text = text
            self.timeout = timeout

        def __str__(self) -> str:
            return self.text


logger = logging.getLogger("geo-agent")


class TransactionNotFoundError(Exception):
    """Raised when no geo event exists for a transaction."""


class Neo4jUnavailableError(Exception):
    """Raised when a graph check cannot reach Neo4j."""


class Neo4jQueryTimeoutError(Exception):
    """Raised when a graph check exceeds the configured timeout."""


GEO_EVENT_FIELDS = (
    "ip_country",
    "device_id",
    "impossible_travel",
    "is_vpn",
    "is_tor",
    "is_datacenter",
    "km_from_home_district",
    "prev_txn_km",
    "prev_txn_time_delta_min",
)

SHARED_IP_QUERY = """
MATCH (a:Account {id: $account_id})-[*1..1]-(other:Account)
WHERE other.id <> $account_id
RETURN count(distinct other) as shared_account_count
"""

CIRCULAR_FLOW_QUERY = """
MATCH (a:Account {id: $account_id})-[*1..3]-(b:Account {id: $account_id})
RETURN count(*) > 0 as has_circular_flow
"""

FRAUD_RING_PROXIMITY_QUERY = """
MATCH (a:Account {id: $account_id})
MATCH (fraud:Account {is_fraud_seed: true})
WHERE fraud.id <> $account_id
MATCH p = shortestPath((a)-[*1..4]-(fraud))
RETURN fraud.id as fraud_node, length(p) as distance
ORDER BY distance ASC LIMIT 1
"""

NEO4J_QUERY_TIMEOUT_SECONDS = 5.0


def _neo4j_database() -> str | None:
    """Target Neo4j database, read lazily so it respects a late-loaded .env.

    Falls back to the driver default when unset.
    """
    return os.environ.get("NEO4J_DATABASE") or None


def evaluate_geo(txn_id: str, account_id: str, db_connection, neo4j_driver) -> dict[str, Any]:
    geo_event = _fetch_geo_event(txn_id, account_id, db_connection)
    if geo_event is None:
        raise TransactionNotFoundError("Transaction not found")

    resolved_account_id = str(geo_event.get("account_id") or account_id)
    device_id = geo_event.get("device_id")
    device = _fetch_device_fingerprint(device_id, db_connection) if device_id else None
    prior_device_seen_count = (
        _fetch_prior_device_seen_count(txn_id, resolved_account_id, device_id, db_connection)
        if device_id
        else None
    )

    breakdown = _calculate_postgres_breakdown(geo_event, device, prior_device_seen_count)
    fraud_ring_details = {
        "is_near_fraud_seed": False,
        "nearest_fraud_node_distance_hops": None,
        "nearest_fraud_node_id": None,
    }
    graph_status = {
        "neo4j_available": neo4j_driver is not None,
        "timeout_count": 0,
        "skipped_count": 0,
    }

    if neo4j_driver is not None:
        graph_breakdown, fraud_ring_details, graph_status = _calculate_graph_breakdown(
            resolved_account_id,
            neo4j_driver,
        )
        breakdown.update(graph_breakdown)
    else:
        breakdown.update(_empty_graph_breakdown())

    risk_score = min(round(sum(breakdown.values()), 4), 1.0)
    confidence = _calculate_confidence(
        geo_event,
        device,
        graph_status,
        prior_device_seen_count,
    )

    return {
        "txn_id": txn_id,
        "risk_score": risk_score,
        "confidence": confidence,
        "breakdown": breakdown,
        "fraud_ring_details": fraud_ring_details,
    }


def query_shared_ip_accounts(account_id: str, neo4j_driver) -> int:
    record = _run_neo4j_single(
        neo4j_driver,
        SHARED_IP_QUERY,
        {"account_id": account_id},
    )
    return _int_or_zero(_record_get(record, "shared_account_count"))


def query_circular_flow(account_id: str, neo4j_driver) -> bool:
    record = _run_neo4j_single(
        neo4j_driver,
        CIRCULAR_FLOW_QUERY,
        {"account_id": account_id},
    )
    return _bool_value(_record_get(record, "has_circular_flow"))


def query_fraud_ring_proximity(account_id: str, neo4j_driver) -> dict[str, Any]:
    record = _run_neo4j_single(
        neo4j_driver,
        FRAUD_RING_PROXIMITY_QUERY,
        {"account_id": account_id},
    )
    if record is None:
        return {
            "is_near_fraud_seed": False,
            "nearest_fraud_node_distance_hops": None,
            "nearest_fraud_node_id": None,
        }

    distance = _int_or_none(_record_get(record, "distance"))
    fraud_node = _record_get(record, "fraud_node")
    return {
        "is_near_fraud_seed": distance is not None and distance <= 3,
        "nearest_fraud_node_distance_hops": distance,
        "nearest_fraud_node_id": fraud_node,
    }


def _fetch_geo_event(txn_id: str, account_id: str, db_connection) -> dict[str, Any] | None:
    result = db_connection.execute(
        text(
            """
            SELECT
                g.txn_id,
                COALESCE(g.account_id, t.account_id, :account_id) AS account_id,
                g.timestamp,
                COALESCE(g.ip_address, t.ip_address) AS ip_address,
                g.ip_country,
                g.ip_city,
                g.ip_isp,
                g.latitude,
                g.longitude,
                g.is_vpn,
                g.is_tor,
                g.is_datacenter,
                g.velocity_flag,
                g.km_from_home_district,
                g.prev_txn_km,
                g.prev_txn_time_delta_min,
                g.impossible_travel,
                c.district AS home_district,
                t.device_id
            FROM geo_events g
            LEFT JOIN transactions t ON t.txn_id = g.txn_id
            LEFT JOIN customers c ON c.account_id = COALESCE(g.account_id, t.account_id, :account_id)
            WHERE g.txn_id = :txn_id
            """
        ),
        {"txn_id": txn_id, "account_id": account_id},
    )
    return _row_to_dict(result.fetchone())


def _fetch_device_fingerprint(device_id: str, db_connection) -> dict[str, Any] | None:
    result = db_connection.execute(
        text(
            """
            SELECT
                device_id,
                locale,
                is_rooted_or_jailbroken,
                vpn_detected,
                tor_exit_node,
                num_accounts_seen_on_device,
                is_shared_device,
                risk_signals
            FROM device_fingerprints
            WHERE device_id = :device_id
            """
        ),
        {"device_id": device_id},
    )
    return _row_to_dict(result.fetchone())


def _fetch_prior_device_seen_count(
    txn_id: str,
    account_id: str,
    device_id: str,
    db_connection,
) -> int:
    result = db_connection.execute(
        text(
            """
            SELECT count(*) AS prior_seen_count
            FROM transactions
            WHERE account_id = :account_id
              AND device_id = :device_id
              AND txn_id <> :txn_id
            """
        ),
        {
            "txn_id": txn_id,
            "account_id": account_id,
            "device_id": device_id,
        },
    )
    row = _row_to_dict(result.fetchone())
    return _int_or_zero(row.get("prior_seen_count") if row else None)


def _calculate_postgres_breakdown(
    geo_event: dict[str, Any],
    device: dict[str, Any] | None,
    prior_device_seen_count: int | None,
) -> dict[str, float]:
    is_vpn = _bool_value(geo_event.get("is_vpn")) or _bool_value(
        device.get("vpn_detected") if device else False
    )
    is_tor = _bool_value(geo_event.get("is_tor")) or _bool_value(
        device.get("tor_exit_node") if device else False
    )
    vpn_tor_risk = 0.30 if is_tor else 0.20 if is_vpn else 0.0

    new_device_risk = 0.0
    if device is None:
        new_device_risk = 0.10
    elif prior_device_seen_count == 0:
        new_device_risk = 0.25

    rooted_locale_mismatch_risk = 0.0
    if (
        device is not None
        and _bool_value(device.get("is_rooted_or_jailbroken"))
        and str(device.get("locale") or "").strip() == "en_US"
        and str(geo_event.get("ip_country") or "").strip().lower() == "nepal"
    ):
        rooted_locale_mismatch_risk = 0.40

    return {
        "impossible_travel_risk": 0.50
        if _bool_value(geo_event.get("impossible_travel"))
        else 0.0,
        "new_device_risk": new_device_risk,
        "rooted_locale_mismatch_risk": rooted_locale_mismatch_risk,
        "vpn_tor_risk": vpn_tor_risk,
        "datacenter_risk": 0.15 if _bool_value(geo_event.get("is_datacenter")) else 0.0,
    }


def _calculate_graph_breakdown(
    account_id: str,
    neo4j_driver,
) -> tuple[dict[str, float], dict[str, Any], dict[str, Any]]:
    graph_status = {
        "neo4j_available": True,
        "timeout_count": 0,
        "skipped_count": 0,
    }
    breakdown = _empty_graph_breakdown()
    fraud_ring_details = {
        "is_near_fraud_seed": False,
        "nearest_fraud_node_distance_hops": None,
        "nearest_fraud_node_id": None,
    }

    try:
        shared_account_count = query_shared_ip_accounts(account_id, neo4j_driver)
        breakdown["shared_ip_risk"] = min(shared_account_count * 0.20, 0.20)
    except Neo4jQueryTimeoutError:
        graph_status["timeout_count"] += 1
        graph_status["skipped_count"] += 1
    except Neo4jUnavailableError:
        graph_status["neo4j_available"] = False
        graph_status["skipped_count"] += 3
        return breakdown, fraud_ring_details, graph_status

    try:
        if query_circular_flow(account_id, neo4j_driver):
            breakdown["circular_flow_risk"] = 0.25
    except Neo4jQueryTimeoutError:
        graph_status["timeout_count"] += 1
        graph_status["skipped_count"] += 1
    except Neo4jUnavailableError:
        graph_status["neo4j_available"] = False
        graph_status["skipped_count"] += 2
        return breakdown, fraud_ring_details, graph_status

    try:
        fraud_ring_details = query_fraud_ring_proximity(account_id, neo4j_driver)
        distance = fraud_ring_details["nearest_fraud_node_distance_hops"]
        breakdown["fraud_ring_proximity_risk"] = _fraud_ring_risk(distance)
    except Neo4jQueryTimeoutError:
        graph_status["timeout_count"] += 1
        graph_status["skipped_count"] += 1
    except Neo4jUnavailableError:
        graph_status["neo4j_available"] = False
        graph_status["skipped_count"] += 1

    return breakdown, fraud_ring_details, graph_status


def _empty_graph_breakdown() -> dict[str, float]:
    return {
        "shared_ip_risk": 0.0,
        "circular_flow_risk": 0.0,
        "fraud_ring_proximity_risk": 0.0,
    }


def _fraud_ring_risk(distance: int | None) -> float:
    if distance == 1:
        return 0.35
    if distance == 2:
        return 0.25
    if distance == 3:
        return 0.10
    return 0.0


def _calculate_confidence(
    geo_event: dict[str, Any],
    device: dict[str, Any] | None,
    graph_status: dict[str, Any],
    prior_device_seen_count: int | None,
) -> float:
    if _bool_value(geo_event.get("impossible_travel")):
        return 0.98

    missing_count = 0
    if not geo_event.get("ip_country"):
        missing_count += 1
    if not geo_event.get("device_id"):
        missing_count += 1
    if device is None:
        missing_count += 1

    is_first_transaction = (
        geo_event.get("prev_txn_time_delta_min") is None
        and prior_device_seen_count in (None, 0)
    )

    if missing_count >= 2 or is_first_transaction:
        confidence = 0.50
    elif missing_count == 1:
        confidence = 0.75
    else:
        confidence = 0.95

    if not graph_status.get("neo4j_available", True):
        confidence = min(confidence, 0.60)
    if graph_status.get("timeout_count", 0):
        confidence = max(confidence - 0.10 * graph_status["timeout_count"], 0.0)

    return round(confidence, 2)


def _run_neo4j_single(neo4j_driver, query: str, params: dict[str, Any]) -> Any | None:
    started = time.perf_counter()
    try:
        with neo4j_driver.session(database=_neo4j_database()) as session:
            result = session.run(Query(query, timeout=NEO4J_QUERY_TIMEOUT_SECONDS), params)
            record = result.single()
    except Neo4jError as exc:
        logger.warning("Neo4j query failed:\n%s", traceback.format_exc())
        if "timeout" in str(exc).lower():
            raise Neo4jQueryTimeoutError(str(exc)) from exc
        raise Neo4jUnavailableError(str(exc)) from exc
    except TimeoutError as exc:
        logger.warning("Neo4j query timed out:\n%s", traceback.format_exc())
        raise Neo4jQueryTimeoutError(str(exc)) from exc
    except Exception as exc:
        logger.warning("Neo4j query failed:\n%s", traceback.format_exc())
        raise Neo4jUnavailableError(str(exc)) from exc

    query_time_ms = int((time.perf_counter() - started) * 1000)
    logger.debug("Cypher query executed in %sms", query_time_ms)
    if query_time_ms > NEO4J_QUERY_TIMEOUT_SECONDS * 1000:
        logger.warning("Cypher query exceeded %ss timeout", NEO4J_QUERY_TIMEOUT_SECONDS)
        raise Neo4jQueryTimeoutError("Cypher query timeout")
    return record


def _record_get(record: Any, key: str) -> Any:
    if record is None:
        return None
    if isinstance(record, dict):
        return record.get(key)
    return record[key]


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    return dict(row)


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    return bool(value)


def _int_or_zero(value: Any) -> int:
    return _int_or_none(value) or 0


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
