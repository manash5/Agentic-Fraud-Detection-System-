"""State projector — a read-only Kafka consumer inside the FastAPI process.

Consumer group `fraud-state-projector` (independent of `fraud-orchestrator`,
so the orchestrator keeps exclusive ownership of running the agents; this
consumer only *observes* the event stream). For every event about a known app
transaction it projects progress into Redis `txn:state:{txn_id}` — which the
frontend polls — and on `final_decision` applies the terminal side effects:

    PASS  -> debit the account, mark app txn success
    OTP   -> initiate the SMS OTP challenge (the ONLY place OTP is triggered)
    BLOCK -> mark app txn blocked
    ERROR -> mark failed

Transactions submitted through the bare `/pipeline/submit` (dataset replays)
have no app_transactions row and are skipped. Terminal side effects only fire
for rows still in status 'pending', so seed-time backfills of historical
transactions update their analysis without touching balances or sending SMS.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from aiokafka import AIOKafkaConsumer

from app.deps import TXN_STATE_KEY, app_db, redis_client
from app.services import mappers, otp_service, txn_logger
from kafka_bus import config as kafka_config
from kafka_bus.config import EventType
from kafka_bus.events import Event

logger = logging.getLogger("state-projector")

PROJECTOR_GROUP = os.environ.get("FRAUD_KAFKA_PROJECTOR_GROUP", "fraud-state-projector")

_AGENT_EVENTS = {
    EventType.VELOCITY_COMPLETED: "velocity",
    EventType.GEO_COMPLETED: "geo",
    EventType.GRAPH_COMPLETED: "graph",
    EventType.BEHAVIOR_COMPLETED: "behavior",
}

_TERMINAL = {"completed", "blocked", "failed"}


async def _load_state(txn_id: str) -> dict[str, Any] | None:
    raw = await redis_client.get(TXN_STATE_KEY.format(txn_id=txn_id))
    return json.loads(raw) if raw else None


async def _save_state(txn_id: str, state: dict[str, Any]) -> None:
    await redis_client.setex(
        TXN_STATE_KEY.format(txn_id=txn_id), 3600, json.dumps(state, default=str))


async def _handle_event(event: Event) -> None:
    txn_id = event.transaction_id
    state = await _load_state(txn_id)
    if state is None:
        return  # not an app-submitted transaction (e.g. bare /pipeline/submit replay)
    if state.get("status") in _TERMINAL:
        return  # forward-only

    if event.event_type in _AGENT_EVENTS:
        agent = _AGENT_EVENTS[event.event_type]
        state.setdefault("agents", {})[agent] = event.payload
        await _save_state(txn_id, state)
        return

    if event.event_type == EventType.SYNTHESIS_COMPLETED:
        state["synthesis"] = event.payload
        await _save_state(txn_id, state)
        return

    if event.event_type == EventType.FINAL_DECISION:
        await _handle_final_decision(txn_id, state, event.payload)


async def _handle_final_decision(txn_id: str, state: dict[str, Any],
                                 payload: dict[str, Any]) -> None:
    decision = str(payload.get("decision", "ERROR"))
    state["decision"] = decision

    if decision == "ERROR":
        state["status"] = "failed"
        state["failReason"] = payload.get("detail", "pipeline error")
        await _save_state(txn_id, state)
        await otp_service.fail_transaction(txn_id)
        return

    from datetime import datetime, timezone
    submitted = state.get("submitted_at")
    total_ms = 0.0
    if submitted:
        try:
            delta = datetime.now(timezone.utc) - datetime.fromisoformat(submitted)
            total_ms = delta.total_seconds() * 1000
        except ValueError:
            pass

    amount = float(state.get("amount", 0.0))
    hour = datetime.now(timezone.utc).hour
    fraud = mappers.build_fraud_analysis(
        agents=state.get("agents", {}),
        synthesis=state.get("synthesis", {}),
        final=payload,
        amount=amount,
        hour=int(state.get("local_hour", hour)),
        total_ms=total_ms,
    )
    agents_used = fraud.pop("agentsUsed", [])
    state["fraud"] = {
        "reference": state.get("reference", txn_id),
        "score": fraud["synthesis"]["finalRisk"],
        "decision": decision,
        "pattern": fraud["synthesis"]["pattern"],
        "analysis": fraud,
    }
    final_score = fraud["synthesis"]["finalRisk"]
    fraud_type = fraud["synthesis"]["fraudType"]

    # Append this evaluation to transactions_logs.json (model-verdict format).
    now = datetime.now(timezone.utc)
    await txn_logger.append_log(mappers.build_txn_log_entry(
        txn_id=txn_id,
        evaluated_at=now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}",
        agents=state.get("agents", {}),
        weights=state.get("synthesis", {}).get("weights_applied", {}),
        final_score=final_score,
        decision=decision,
        baseline_decision=fraud["baselineDecision"],
        total_ms=total_ms,
    ))

    # Persist analysis on the app row regardless of its lifecycle stage...
    async with app_db.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM app_transactions WHERE id = $1", txn_id)
        if row is None:
            logger.warning("final_decision for unknown app txn %s", txn_id)
            return
        pending = row["status"] == "pending"
        await conn.execute(
            """UPDATE app_transactions
               SET decision=$2, risk_score=$3, latency_ms=$4, fraud=$5, fraud_type=$6
               WHERE id=$1""",
            txn_id, decision, final_score, round(total_ms, 1),
            json.dumps(fraud), fraud_type)

    # ...but side effects (debit / OTP / block) only for live pending txns.
    if not pending:
        state["status"] = "completed"
        await _save_state(txn_id, state)
        return

    if decision == "PASS":
        await otp_service.complete_transaction(txn_id)
        state["status"] = "completed"
        logger.info("%s PASS (score %.3f, %d agents) — completed",
                    txn_id, final_score, len(agents_used))
    elif decision == "OTP":
        reason = _otp_trigger_reason(state, payload)
        try:
            otp_block = await otp_service.initiate(
                txn_id, state.get("account_id", ""), state.get("mobile", ""),
                amount, reason)
            state["otp"] = otp_block
            state["status"] = "otp_pending"
            async with app_db.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE app_transactions SET status='otp_required' WHERE id=$1",
                    txn_id)
            logger.info("%s OTP challenge sent (score %.3f): %s",
                        txn_id, final_score, reason)
        except Exception as exc:  # noqa: BLE001 — never leave the txn limbo'd
            logger.error("OTP initiation failed for %s: %s", txn_id, exc)
            state["status"] = "failed"
            state["failReason"] = "otp initiation failed"
            await otp_service.fail_transaction(txn_id)
    else:  # BLOCK
        async with app_db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE app_transactions SET status='blocked' WHERE id=$1", txn_id)
        state["status"] = "blocked"
        logger.info("%s BLOCKED (score %.3f, pattern %s)",
                    txn_id, final_score, payload.get("fraud_pattern"))

    await _save_state(txn_id, state)


def _otp_trigger_reason(state: dict[str, Any], payload: dict[str, Any]) -> str:
    if payload.get("otp_forced_by_disagreement"):
        return "AGENT_DISAGREEMENT"
    agents = state.get("agents", {})
    scored = {n: o.get("risk_score") for n, o in agents.items()
              if isinstance(o, dict) and o.get("risk_score") is not None}
    if not scored:
        return "ELEVATED_RISK"
    top = max(scored, key=lambda n: scored[n])
    return {
        "velocity": "VELOCITY_SPIKE",
        "geo": "GEO_ANOMALY",
        "graph": "NETWORK_RISK",
        "behavior": "BEHAVIOR_ANOMALY",
    }.get(top, "ELEVATED_RISK")


async def run_state_projector() -> None:
    """Consume fraud-events forever; survives broker restarts with backoff."""
    while True:
        consumer = AIOKafkaConsumer(
            kafka_config.TOPIC,
            bootstrap_servers=kafka_config.BOOTSTRAP_SERVERS,
            group_id=PROJECTOR_GROUP,
            enable_auto_commit=True,
            auto_offset_reset="latest",
        )
        try:
            await consumer.start()
            logger.info("State projector consuming %s @ %s (group %s)",
                        kafka_config.TOPIC, kafka_config.BOOTSTRAP_SERVERS,
                        PROJECTOR_GROUP)
            async for msg in consumer:
                try:
                    event = Event.from_bytes(msg.value)
                    await _handle_event(event)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — one bad event never kills the loop
                    logger.error("Projector failed on message: %s", exc)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 — broker hiccup: retry
            logger.warning("State projector connection lost (%s); retrying in 5s", exc)
            await asyncio.sleep(5)
        finally:
            try:
                await consumer.stop()
            except Exception:  # noqa: BLE001
                pass
