"""Transfer submission (the entry point of the live pipeline) + status polling.

POST /transfer persists the transaction everywhere the agents will look for it
(transactions_raw + velocity_snapshots + geo_events, all flagged source='live'),
creates the pending app-ledger row, seeds the Redis workflow state, and
publishes `transaction_received` to Kafka. The orchestrator process runs the
agents; the in-process state projector turns their events into the state that
GET /transfers/{id}/status serves back to the polling frontend.
"""

from __future__ import annotations

import json
import logging
import math
import random
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.deps import OTP_KEY, TXN_STATE_KEY, app_db, get_current_user, redis_client
from app.services import mappers, otp_service
from kafka_bus.config import EventType
from kafka_bus.events import Event

logger = logging.getLogger("transfers-router")

router = APIRouter(tags=["transfers"])

KTM = ZoneInfo("Asia/Kathmandu")
KTM_LAT, KTM_LNG = 27.7172, 85.3240


class TransferBody(BaseModel):
    fromAccountId: str = Field(..., min_length=1)
    destination: str
    recipientAccount: str = Field(..., min_length=1)
    recipientName: str = ""
    recipientBank: str = ""
    amount: float = Field(..., gt=0)
    remarks: str = ""
    mode: str | None = None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def _account_context(conn: Any, account_id: str, ts) -> dict[str, Any]:
    """Device, location, velocity-window and counterparty context for the new txn,
    all derived from the account's real history."""
    device = await conn.fetchrow(
        """SELECT device_id FROM transactions_raw
           WHERE account_id = $1 AND device_id IS NOT NULL
           ORDER BY timestamp DESC LIMIT 1""", account_id)
    geo = await conn.fetchrow(
        """SELECT latitude, longitude, ip_address, ip_city, timestamp
           FROM geo_events WHERE account_id = $1
           ORDER BY timestamp DESC LIMIT 1""", account_id)
    windows = await conn.fetchrow(
        """SELECT
             count(*) FILTER (WHERE timestamp > $2::timestamp - interval '1 minute')  + 1 AS c1m,
             count(*) FILTER (WHERE timestamp > $2::timestamp - interval '5 minutes') + 1 AS c5m,
             count(*) FILTER (WHERE timestamp > $2::timestamp - interval '15 minutes')+ 1 AS c15m,
             count(*) FILTER (WHERE timestamp > $2::timestamp - interval '1 hour')    + 1 AS c1h,
             count(*) FILTER (WHERE timestamp > $2::timestamp - interval '24 hours')  + 1 AS c24h,
             count(*) FILTER (WHERE timestamp > $2::timestamp - interval '7 days')    + 1 AS c7d,
             coalesce(sum(amount_npr) FILTER (WHERE timestamp > $2::timestamp - interval '1 hour'), 0)  AS amt1h,
             coalesce(sum(amount_npr) FILTER (WHERE timestamp > $2::timestamp - interval '24 hours'), 0) AS amt24h,
             count(DISTINCT counterparty_id) FILTER (WHERE timestamp > $2::timestamp - interval '1 hour')  AS uc1h,
             count(DISTINCT counterparty_id) FILTER (WHERE timestamp > $2::timestamp - interval '24 hours') AS uc24h,
             coalesce(max(amount_npr) FILTER (WHERE timestamp > $2::timestamp - interval '24 hours'), 0)   AS max24h,
             max(timestamp) AS last_ts
           FROM transactions_raw WHERE account_id = $1""",
        account_id, ts)
    stats = await conn.fetchrow(
        """SELECT avg(amount_npr) AS avg_all, stddev_samp(amount_npr) AS std_all,
                  avg(amount_npr) FILTER (WHERE timestamp > $2::timestamp - interval '30 days') AS avg_30d,
                  stddev_samp(amount_npr) FILTER (WHERE timestamp > $2::timestamp - interval '30 days') AS std_30d,
                  count(*) FILTER (WHERE timestamp > $2::timestamp - interval '30 days') AS n_30d
           FROM transactions_raw WHERE account_id = $1""",
        account_id, ts)
    return {"device": device, "geo": geo, "windows": windows, "stats": stats}


@router.post("/transfer", status_code=202)
async def submit_transfer(body: TransferBody, request: Request,
                          user: dict = Depends(get_current_user)) -> dict[str, Any]:
    producer = request.app.state.app_event_producer
    if producer is None:
        raise HTTPException(status_code=503, detail="Transaction pipeline unavailable (Kafka down).")

    async with app_db.pool.acquire() as conn:
        account = await conn.fetchrow(
            "SELECT * FROM app_accounts WHERE id = $1", body.fromAccountId)
        if account is None or account["customer_id"] != user["customerId"]:
            raise HTTPException(status_code=403, detail="Account does not belong to you.")
        if account["status"] != "active":
            raise HTTPException(status_code=400, detail=f"Account is {account['status']}.")
        if float(account["balance"]) < body.amount:
            raise HTTPException(status_code=400, detail="Insufficient balance.")

        now_local = datetime.now(KTM).replace(tzinfo=None)
        now_utc = datetime.now(timezone.utc)
        txn_id = f"TXN-LIVE-{now_utc:%Y%m%d}-{secrets.token_hex(4).upper()}"
        reference = f"REF{now_utc:%y%m%d}{secrets.randbelow(10**8):08d}"
        txn_type_raw = mappers.txn_type_for_transfer(
            body.destination, body.recipientBank, body.mode)
        account_id = account["id"]

        ctx = await _account_context(conn, account_id, now_local)
        device_id = ctx["device"]["device_id"] if ctx["device"] is not None else None
        prev_geo = ctx["geo"]
        if prev_geo is not None:
            lat = float(prev_geo["latitude"]) + random.uniform(-0.01, 0.01)
            lng = float(prev_geo["longitude"]) + random.uniform(-0.01, 0.01)
            ip_address = prev_geo["ip_address"] or "27.34.72.19"
            city = prev_geo["ip_city"] or "Kathmandu"
            prev_km = _haversine_km(float(prev_geo["latitude"]),
                                    float(prev_geo["longitude"]), lat, lng)
            prev_delta_min = max(
                (now_local - prev_geo["timestamp"]).total_seconds() / 60.0, 0.0)
        else:
            lat, lng = KTM_LAT + random.uniform(-0.01, 0.01), KTM_LNG + random.uniform(-0.01, 0.01)
            ip_address, city = "27.34.72.19", "Kathmandu"
            prev_km, prev_delta_min = 0.0, 0.0

        w, s = ctx["windows"], ctx["stats"]
        n_30d = int(s["n_30d"] or 0)
        avg = float((s["avg_30d"] if n_30d >= 5 else s["avg_all"]) or 0.0)
        std = float((s["std_30d"] if n_30d >= 5 else s["std_all"]) or 0.0)
        z_score = (body.amount - avg) / std if std > 1.0 else 0.0
        z_score = max(-10.0, min(10.0, z_score))
        last_ts = w["last_ts"]
        dormancy_break = bool(last_ts and (now_local - last_ts) > timedelta(days=30))
        night_flag = now_local.hour >= 22 or now_local.hour < 6
        weekend_flag = now_local.weekday() >= 5
        counterparty_id = body.recipientAccount.strip()
        seen_cp = await conn.fetchval(
            "SELECT count(*) FROM transactions_raw WHERE account_id=$1 AND counterparty_id=$2",
            account_id, counterparty_id)
        new_counterparty = int(seen_cp or 0) == 0

        async with conn.transaction():
            await conn.execute(
                """INSERT INTO transactions_raw
                   (txn_id, timestamp, account_id, counterparty_id, txn_type, amount_npr,
                    currency, channel, device_id, ip_address, merchant_category_code,
                    terminal_id, session_id, auth_method, response_code,
                    processing_time_ms, is_international, fx_rate, notes, source)
                   VALUES ($1,$2,$3,$4,$5,$6,'NPR','MOBILE_APP',$7,$8,NULL,NULL,$9,
                           'MPIN','00',$10,FALSE,NULL,$11,'live')""",
                txn_id, now_local, account_id, counterparty_id, txn_type_raw,
                body.amount, device_id, ip_address,
                f"SES-{secrets.token_hex(6)}", random.randint(150, 600),
                body.remarks or None)
            await conn.execute(
                """INSERT INTO velocity_snapshots
                   (txn_id, account_id, snapshot_time, txn_count_1m, txn_count_5m,
                    txn_count_15m, txn_count_1h, txn_count_24h, txn_count_7d,
                    total_amount_1h_npr, total_amount_24h_npr, unique_counterparties_1h,
                    unique_counterparties_24h, new_counterparty_flag,
                    max_single_txn_24h_npr, avg_txn_amount_30d_npr,
                    std_txn_amount_30d_npr, z_score_amount, dormancy_break,
                    weekend_flag, night_flag)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
                           $18,$19,$20,$21)""",
                txn_id, account_id, now_local,
                int(w["c1m"]), int(w["c5m"]), int(w["c15m"]), int(w["c1h"]),
                int(w["c24h"]), int(w["c7d"]),
                float(w["amt1h"]) + body.amount, float(w["amt24h"]) + body.amount,
                int(w["uc1h"]) + (1 if new_counterparty else 0),
                int(w["uc24h"]) + (1 if new_counterparty else 0),
                new_counterparty, max(float(w["max24h"]), body.amount),
                avg, std, z_score, dormancy_break, weekend_flag, night_flag)
            await conn.execute(
                """INSERT INTO geo_events
                   (txn_id, account_id, timestamp, ip_address, ip_country, ip_city,
                    ip_isp, ip_asn, latitude, longitude, accuracy_km, is_vpn, is_tor,
                    is_datacenter, velocity_flag, km_from_home_district, prev_txn_km,
                    prev_txn_time_delta_min, impossible_travel)
                   VALUES ($1,$2,$3,$4,'Nepal',$5,'Nepal Telecom','AS23752',$6,$7,
                           $8,FALSE,FALSE,FALSE,FALSE,$9,$10,$11,FALSE)""",
                txn_id, account_id, now_local, ip_address, city, lat, lng,
                round(random.uniform(0.5, 5.0), 2),
                round(prev_km, 2), round(prev_km, 2), round(prev_delta_min, 2))

            is_wallet = body.destination == "wallet"
            frontend_type = ("payment" if body.mode == "bill"
                             else "topup" if is_wallet or body.mode == "topup"
                             else "qr_payment" if body.mode == "qr"
                             else "transfer")
            await conn.execute(
                """INSERT INTO app_transactions
                   (id, reference, customer_id, customer_name, account_id,
                    account_number, cp_name, cp_account, cp_bank, cp_is_wallet,
                    amount, direction, type, channel, status, decision, risk_score,
                    latency_ms, location_city, location_lat, location_lng, device,
                    ip_address, remarks, ts, txn_type, counterparty_id, auth_method,
                    mcc, prev_txn_km, prev_txn_delta_min, z_score_amount,
                    txn_count_1m, dormancy_break, night_flag, new_counterparty_flag,
                    device_id)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'debit',$12,'mobile',
                           'pending',NULL,NULL,NULL,$13,$14,$15,$16,$17,$18,$19,$20,
                           $21,'PIN','6011',$22,$23,$24,$25,$26,$27,$28,$29)""",
                txn_id, reference, user["customerId"], user["name"], account_id,
                account["account_number"], body.recipientName or "Unknown",
                counterparty_id, body.recipientBank or "Global IME Bank", is_wallet,
                body.amount, frontend_type, city, lat, lng, "Mobile App",
                ip_address, body.remarks or "Fund transfer", now_utc, txn_type_raw,
                counterparty_id, round(prev_km, 2), round(prev_delta_min, 2),
                round(z_score, 3), int(w["c1m"]), dormancy_break, night_flag,
                new_counterparty, device_id or "")

    state = {
        "status": "processing",
        "agents": {},
        "submitted_at": now_utc.isoformat(),
        "reference": reference,
        "account_id": account_id,
        "amount": body.amount,
        "mobile": user["mobile"],
        "local_hour": now_local.hour,
    }
    await redis_client.setex(
        TXN_STATE_KEY.format(txn_id=txn_id), 3600, json.dumps(state))

    event = Event.make(
        EventType.TRANSACTION_RECEIVED, txn_id,
        {"account_id": account_id, "txn_type": txn_type_raw, "amount": body.amount,
         "currency": "NPR", "device_id": device_id, "latitude": lat, "longitude": lng})
    try:
        await producer.publish(event)
    except Exception as exc:  # noqa: BLE001
        logger.error("Kafka publish failed for %s: %s", txn_id, exc)
        await otp_service.fail_transaction(txn_id)
        raise HTTPException(status_code=503, detail="Transaction pipeline unavailable.") from None

    logger.info("Transfer %s submitted: NPR %.2f from %s (%s)",
                txn_id, body.amount, account_id, txn_type_raw)
    return {"txnId": txn_id, "reference": reference, "status": "processing"}


@router.get("/transfers/{txn_id}/status")
async def transfer_status(txn_id: str,
                          user: dict = Depends(get_current_user)) -> dict[str, Any]:
    raw = await redis_client.get(TXN_STATE_KEY.format(txn_id=txn_id))
    state: dict[str, Any] | None = json.loads(raw) if raw else None

    async with app_db.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM app_transactions WHERE id = $1", txn_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if row["customer_id"] != user["customerId"]:
        raise HTTPException(status_code=403, detail="Not your transaction")

    if state is None:
        # Redis state evicted/lost: fall back to the durable row.
        status = {"pending": "processing", "otp_required": "otp_pending",
                  "success": "completed", "blocked": "blocked",
                  "failed": "failed"}.get(row["status"], row["status"])
        state = {"status": status, "agents": {}}

    status = state.get("status", "processing")
    response: dict[str, Any] = {
        "txnId": txn_id,
        "status": status,
        "agents": state.get("agents", {}),
        "synthesis": state.get("synthesis"),
        "decision": state.get("decision") or row["decision"],
        "fraud": state.get("fraud"),
        "txn": None,
        "otp": None,
        "failReason": state.get("failReason"),
    }
    if status == "otp_pending":
        otp_block = state.get("otp") or {}
        ttl = await redis_client.ttl(OTP_KEY.format(txn_id=txn_id))
        if ttl and ttl > 0:
            otp_block["ttlSeconds"] = ttl
        response["otp"] = otp_block
    if status in ("completed", "blocked", "failed"):
        response["txn"] = mappers.row_to_transaction(row)
        if response["fraud"] is None and row["fraud"] is not None:
            fraud = row["fraud"]
            analysis = json.loads(fraud) if isinstance(fraud, str) else fraud
            response["fraud"] = {
                "reference": row["reference"],
                "score": float(row["risk_score"] or 0.0),
                "decision": row["decision"] or "PASS",
                "pattern": (analysis or {}).get("synthesis", {}).get("pattern", "none"),
                "analysis": analysis,
            }
    return response
