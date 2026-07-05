"""Probe the TRUE live-transfer decision for candidate accounts.

    uv run python -m scripts.probe_decision ACC-1234567:RTGS:45000 ACC-...:...

Inserts a live txn (+ companion velocity/geo rows) exactly like POST /transfer,
runs the real 4-agent pipeline + synthesis, prints the decision, then deletes
the probe rows. Used to pick demo profiles whose live decision is reliable.
"""

from __future__ import annotations

import asyncio
import os
import random
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

import asyncpg  # noqa: E402

from agents.behavior_agent import BehaviorAgent  # noqa: E402
from agents.geo_agent import GeoAgent  # noqa: E402
from agents.graph_agent import NEO4J_DATABASE, get_driver  # noqa: E402
from agents.velocity_agent import VelocityAgent  # noqa: E402
from behavior_agent.config import pg_connect_kwargs  # noqa: E402
from pipeline.agent_runner import (  # noqa: E402
    PipelineTxn, fuse, run_behavior, run_geo, run_graph, run_velocity)


async def _insert_live(conn, account_id, txn_type, amount, now_local):
    txn_id = f"PROBE-{secrets.token_hex(4).upper()}"
    dev = await conn.fetchrow(
        "SELECT device_id FROM transactions_raw WHERE account_id=$1 AND device_id IS NOT NULL ORDER BY timestamp DESC LIMIT 1",
        account_id)
    geo = await conn.fetchrow(
        "SELECT latitude, longitude FROM geo_events WHERE account_id=$1 ORDER BY timestamp DESC LIMIT 1",
        account_id)
    device_id = dev["device_id"] if dev else None
    lat = float(geo["latitude"]) if geo else 27.7172
    lon = float(geo["longitude"]) if geo else 85.324
    w = await conn.fetchrow("""
        SELECT count(*) FILTER (WHERE timestamp > $2::timestamp - interval '1 minute')+1 c1m,
               count(*) FILTER (WHERE timestamp > $2::timestamp - interval '1 hour')+1 c1h,
               count(*) FILTER (WHERE timestamp > $2::timestamp - interval '24 hours')+1 c24h,
               count(*) FILTER (WHERE timestamp > $2::timestamp - interval '7 days')+1 c7d,
               avg(amount_npr) avg_all, stddev_samp(amount_npr) std_all
        FROM transactions_raw WHERE account_id=$1""", account_id, now_local)
    avg = float(w["avg_all"] or 0); std = float(w["std_all"] or 0)
    z = max(-10, min(10, (amount - avg) / std if std > 1 else 0))
    await conn.execute("""
        INSERT INTO transactions_raw (txn_id,timestamp,account_id,counterparty_id,txn_type,
          amount_npr,currency,channel,device_id,ip_address,auth_method,response_code,
          processing_time_ms,is_international,source)
        VALUES ($1,$2,$3,'ACC-PROBE',$4,$5,'NPR','MOBILE_APP',$6,'27.34.72.19','MPIN','00',$7,false,'live')""",
        txn_id, now_local, account_id, txn_type, amount, device_id, random.randint(150, 600))
    await conn.execute("""
        INSERT INTO velocity_snapshots (txn_id,account_id,snapshot_time,txn_count_1m,txn_count_5m,
          txn_count_15m,txn_count_1h,txn_count_24h,txn_count_7d,total_amount_1h_npr,total_amount_24h_npr,
          unique_counterparties_1h,unique_counterparties_24h,new_counterparty_flag,max_single_txn_24h_npr,
          avg_txn_amount_30d_npr,std_txn_amount_30d_npr,z_score_amount,dormancy_break,weekend_flag,night_flag)
        VALUES ($1,$2,$3,$4,$4,$4,$5,$6,$7,$8,$8,1,1,true,$8,$9,$10,$11,false,false,false)""",
        txn_id, account_id, now_local, int(w["c1m"]), int(w["c1h"]), int(w["c24h"]),
        int(w["c7d"]), amount, avg, std, z)
    await conn.execute("""
        INSERT INTO geo_events (txn_id,account_id,timestamp,ip_address,ip_country,ip_city,latitude,
          longitude,accuracy_km,is_vpn,is_tor,is_datacenter,velocity_flag,km_from_home_district,
          prev_txn_km,prev_txn_time_delta_min,impossible_travel)
        VALUES ($1,$2,$3,'27.34.72.19','Nepal','Kathmandu',$4,$5,1.0,false,false,false,false,1.0,1.0,60,false)""",
        txn_id, account_id, now_local, lat, lon)
    return txn_id, device_id, lat, lon


async def main():
    specs = sys.argv[1:]
    dsn = os.environ.get("FRAUD_DB_DSN", "dbname=fraud_detection_global")
    conn = await asyncpg.connect(**pg_connect_kwargs(dsn))
    velocity, geo, behavior = VelocityAgent(), GeoAgent(), BehaviorAgent()
    await geo.connect()
    berr = None
    try:
        await behavior.connect()
    except Exception as exc:  # noqa: BLE001
        berr = str(exc)
    driver = get_driver()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for spec in specs:
        account_id, txn_type, amount = spec.split(":")
        amount = float(amount)
        txn_id, dev, lat, lon = await _insert_live(conn, account_id, txn_type, amount, now)
        try:
            txn = PipelineTxn(txn_id=txn_id, account_id=account_id, txn_type=txn_type,
                              amount=amount, timestamp=now.replace(tzinfo=timezone.utc),
                              device_id=dev, latitude=lat, longitude=lon)
            outcomes = {"velocity": await run_velocity(velocity, txn),
                        "geo": await run_geo(geo, txn),
                        "graph": await run_graph(driver, NEO4J_DATABASE, txn),
                        "behavior": await run_behavior(behavior, berr, txn)}
            result, _, _ = fuse(outcomes, txn_type)
            def s(n):
                o = outcomes[n]; return f"{o.risk_score:.2f}" if o.risk_score is not None else o.status[:4]
            print(f"{account_id} {txn_type} {int(amount)} => {result.decision.value} "
                  f"{result.final_score:.3f}  v/g/gr/b={s('velocity')}/{s('geo')}/{s('graph')}/{s('behavior')}")
        finally:
            for tbl in ("velocity_snapshots", "geo_events", "transactions_raw"):
                await conn.execute(f"DELETE FROM {tbl} WHERE txn_id=$1", txn_id)
    await geo.close(); await behavior.close(); driver.close(); await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
