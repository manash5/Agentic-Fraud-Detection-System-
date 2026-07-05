"""Seed the app-layer tables from the real agent datasets.

    uv run python -m scripts.seed_app_data [--customers 100] [--backfill 150]

What it does (idempotent — safe to re-run):

1. Ensures the app_* tables exist (app/db_schema.py DDL).
2. Picks the N highest-history accounts from customer_profiles (always
   including the demo account ACC-1207531 and the COMM-042 ring collector
   ACC-0011204) and creates app_customers / app_accounts / app_cards with
   deterministic Nepali identities. app_accounts.id IS the agent account id.
3. Creates the demo login: Biplov Gautam / mobile DEMO_USER_MOBILE
   (default 9801234567) / mPIN 1234, mapped to ACC-1207531 (68 txns of real
   history -> all three behavior models fire).
4. Seeds app_transactions from each account's real transactions_raw history,
   display timestamps shifted forward by a whole number of WEEKS so recent
   dashboards have data while hour-of-day and weekday stay truthful.
5. Hydrates the Redis state the live agents read: velocity baselines +
   txn-type distributions (the documented nightly-job hashes), geo last
   location, known devices, and observation counts.
6. Backfills the most recent K seeded transactions through the REAL pipeline
   (velocity/geo/graph/behavior + synthesis, direct in-process calls) so
   seeded rows carry genuine agent verdicts, SHAP and synthesis_audit rows —
   no fabricated analysis anywhere.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

import asyncpg  # noqa: E402

from app.db_schema import ensure_schema  # noqa: E402
from app.routers.auth import make_mpin_hash  # noqa: E402
from app.services import mappers  # noqa: E402
from behavior_agent.config import pg_connect_kwargs  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("seed")

SEED = 20260703
DEMO_ACCOUNT = "ACC-1207531"
DEMO_ACCOUNT_2 = "ACC-4016576"
RING_COLLECTOR = "ACC-0011204"
DEMO_MOBILE = os.environ.get("DEMO_USER_MOBILE", "9801234567")

MALE = ["Biplov", "Aarav", "Bibek", "Prakash", "Suraj", "Kiran", "Roshan",
        "Sandeep", "Nabin", "Dipesh", "Sagar", "Anil", "Rajesh", "Milan", "Sunil"]
FEMALE = ["Sita", "Anisha", "Maya", "Pooja", "Nisha", "Srijana", "Bina",
          "Kabita", "Sarita", "Mina", "Laxmi", "Gita", "Rekha", "Sunita", "Asmita"]
LAST = ["Gautam", "Shrestha", "Gurung", "Tamang", "Karki", "Adhikari", "Thapa",
        "Rai", "Magar", "Basnet", "Koirala", "Poudel", "Bhattarai", "KC", "Sharma"]
BANKS = ["Global IME Bank", "Nabil Bank", "NIC Asia Bank", "Himalayan Bank",
         "Nepal SBI Bank", "Everest Bank", "Prabhu Bank", "Machhapuchchhre Bank"]
BRANCHES = ["Kathmandu Main", "Lalitpur", "Pokhara", "Biratnagar", "Butwal",
            "Chitwan", "Birgunj", "Dharan"]
COLORS = ["#0ea5e9", "#8b5cf6", "#f97316", "#10b981", "#ef4444", "#eab308",
          "#ec4899", "#14b8a6"]
CITY_BY_PROVINCE = {
    "Koshi": "Biratnagar", "Madhesh": "Janakpur", "Bagmati": "Kathmandu",
    "Gandaki": "Pokhara", "Lumbini": "Butwal", "Karnali": "Surkhet",
    "Sudurpashchim": "Dhangadhi",
}

KYC_TIER_MAP = {"BASIC": "TIER_1", "STANDARD": "TIER_2", "ENHANCED": "TIER_3"}
RISK_MAP = {"LOW": "low", "MEDIUM": "medium", "HIGH": "high", "WATCHLIST": "high"}


def cp_name(counterparty_id: str, names: dict[str, str], rng: random.Random) -> str:
    if counterparty_id in names:
        return names[counterparty_id]
    seed = sum(ord(c) for c in counterparty_id)
    pool = MALE if seed % 2 == 0 else FEMALE
    return f"{pool[seed % len(pool)]} {LAST[(seed // 7) % len(LAST)]}"


async def seed_identities(conn: asyncpg.Connection, n_customers: int) -> dict[str, str]:
    """Create app_customers/app_accounts/app_cards; returns account_id -> name."""
    rng = random.Random(SEED)
    rows = await conn.fetch("""
        SELECT p.*, c.n_txns FROM customer_profiles p
        JOIN (SELECT account_id, count(*) AS n_txns FROM transactions_raw
              GROUP BY account_id) c ON c.account_id = p.account_id
        ORDER BY c.n_txns DESC LIMIT $1""", n_customers + 20)
    chosen = {r["account_id"]: r for r in rows}
    for must in (DEMO_ACCOUNT, DEMO_ACCOUNT_2, RING_COLLECTOR):
        if must not in chosen:
            extra = await conn.fetchrow("""
                SELECT p.*, (SELECT count(*) FROM transactions_raw t
                             WHERE t.account_id = p.account_id) AS n_txns
                FROM customer_profiles p WHERE p.account_id = $1""", must)
            if extra is not None:
                chosen[must] = extra
            else:
                logger.warning("Required account %s missing from customer_profiles", must)

    names: dict[str, str] = {}
    seq = 0
    # Demo accounts first: the demo customer row must exist before the second
    # account references it, and ordering keeps CUST ids deterministic.
    ordered = sorted(
        chosen.items(),
        key=lambda kv: (kv[0] != DEMO_ACCOUNT, kv[0] != DEMO_ACCOUNT_2, kv[0]))
    for account_id, profile in ordered:
        seq += 1
        is_demo = account_id == DEMO_ACCOUNT
        is_demo2 = account_id == DEMO_ACCOUNT_2
        if is_demo or is_demo2:
            customer_id = "CUST-0000001"
            name, gender = "Biplov Gautam", "male"
            mobile, email = DEMO_MOBILE, "biplov.gautam@email.com"
        else:
            customer_id = f"CUST-{seq:07d}"
            gender = "male" if rng.random() < 0.55 else "female"
            first = rng.choice(MALE if gender == "male" else FEMALE)
            name = f"{first} {rng.choice(LAST)}"
            mobile = f"98{rng.randint(0, 9)}{rng.randint(1000000, 9999999)}"
            email = f"{name.lower().replace(' ', '.')}{seq}@email.com"
        names[account_id] = name

        province = profile["province"] or "Bagmati"
        city = CITY_BY_PROVINCE.get(province, "Kathmandu")
        risk = RISK_MAP.get(profile["risk_tier"], "low")
        account_number = f"12010101{rng.randint(10**7, 10**8 - 1)}" if not is_demo \
            else "1201010100456789"
        joined = profile["customer_since"] or datetime(2020, 1, 1).date()

        if not is_demo2:  # demo's 2nd account shares the same customer row
            await conn.execute("""
                INSERT INTO app_customers
                (id, agent_account_id, name, gender, account_number, mobile, email,
                 address, city, kyc_status, risk_level, joined_at, avatar_color,
                 citizenship_no, branch, district, province, kyc_tier, is_dormant,
                 num_beneficiaries_registered)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
                        $18,$19,$20)
                ON CONFLICT (id) DO NOTHING""",
                customer_id, account_id, name, gender, account_number, mobile,
                email, f"{profile['district'] or city}, {province}", city,
                "verified" if profile["kyc_tier"] != "BASIC" else "pending",
                risk, datetime.combine(joined, datetime.min.time(), timezone.utc),
                rng.choice(COLORS), f"{rng.randint(10, 78)}-01-{rng.randint(60, 80)}-{rng.randint(10000, 99999)}",
                rng.choice(BRANCHES), profile["district"] or city, province,
                KYC_TIER_MAP.get(profile["kyc_tier"], "TIER_1"),
                bool(profile["is_dormant"]),
                int(profile["num_beneficiaries_registered"] or 0))

        if is_demo:
            balance, acct_type, acct_name = 284650.75, "savings", "Everyday Savings"
        elif is_demo2:
            balance, acct_type, acct_name = 96410.20, "current", "Salary Account"
        else:
            balance = round(rng.uniform(8000, 900000), 2)
            acct_type = rng.choice(["savings", "savings", "current"])
            acct_name = "Everyday Savings" if acct_type == "savings" else "Business Current"
        await conn.execute("""
            INSERT INTO app_accounts
            (id, customer_id, type, name, account_number, balance, currency,
             status, interest_rate)
            VALUES ($1,$2,$3,$4,$5,$6,'NPR',$7,$8)
            ON CONFLICT (id) DO NOTHING""",
            account_id, customer_id, acct_type, acct_name,
            account_number if not is_demo2 else "1201010100456790", balance,
            "dormant" if profile["is_dormant"] and not (is_demo or is_demo2) else "active",
            5.5 if acct_type == "savings" else 1.0)

        if not is_demo2 and rng.random() < 0.7:
            await conn.execute("""
                INSERT INTO app_cards
                (id, customer_id, type, scheme, number, holder, expiry, status,
                 card_limit)
                VALUES ($1,$2,$3,$4,$5,$6,$7,'active',$8)
                ON CONFLICT (id) DO NOTHING""",
                f"CARD-{seq:05d}", customer_id,
                "debit" if rng.random() < 0.8 else "credit",
                rng.choice(["visa", "mastercard"]),
                f"4{rng.randint(10**14, 10**15 - 1)}", name.upper(),
                f"{rng.randint(1, 12):02d}/{rng.randint(27, 30)}",
                float(rng.choice([100000, 200000, 500000])))

    await conn.execute("""
        INSERT INTO app_users (customer_id, mobile, mpin_hash)
        VALUES ('CUST-0000001', $1, $2)
        ON CONFLICT (customer_id) DO UPDATE SET mobile = $1, mpin_hash = $2""",
        DEMO_MOBILE, make_mpin_hash("1234", "gime-demo-salt"))
    # OTP delivery reads app_customers.mobile (via the session user), so a
    # changed DEMO_USER_MOBILE must propagate there too on re-runs.
    await conn.execute(
        "UPDATE app_customers SET mobile = $1 WHERE id = 'CUST-0000001'",
        DEMO_MOBILE)
    logger.info("Seeded %d customers (demo: Biplov Gautam / %s / mPIN 1234 -> %s)",
                len(chosen) - 1, DEMO_MOBILE, DEMO_ACCOUNT)
    return names


async def seed_transactions(conn: asyncpg.Connection,
                            names: dict[str, str]) -> int:
    """Copy real transactions_raw history into the app ledger (week-aligned shift)."""
    max_ts = await conn.fetchval(
        "SELECT max(timestamp) FROM transactions_raw WHERE source = 'dataset'")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    shift_weeks = max(((now - max_ts).days - 1) // 7, 0)
    shift = timedelta(weeks=shift_weeks)
    logger.info("Display-time shift: +%d weeks (dataset max %s)", shift_weeks, max_ts)

    customer_by_account = {
        r["id"]: (r["customer_id"], r["account_number"])
        for r in await conn.fetch(
            "SELECT id, customer_id, account_number FROM app_accounts")}
    customer_names = {
        r["id"]: r["name"] for r in await conn.fetch(
            "SELECT id, name FROM app_customers")}

    rng = random.Random(SEED + 1)
    inserted = 0
    for account_id, (customer_id, account_number) in customer_by_account.items():
        limit = 68 if account_id in (DEMO_ACCOUNT, DEMO_ACCOUNT_2) else 8
        rows = await conn.fetch("""
            SELECT t.*, g.ip_city, g.latitude, g.longitude, g.is_vpn, g.is_tor,
                   g.impossible_travel, g.prev_txn_km, g.prev_txn_time_delta_min,
                   v.z_score_amount, v.txn_count_1m, v.dormancy_break, v.night_flag,
                   v.new_counterparty_flag
            FROM transactions_raw t
            LEFT JOIN geo_events g ON g.txn_id = t.txn_id
            LEFT JOIN velocity_snapshots v ON v.txn_id = t.txn_id
            WHERE t.account_id = $1 AND t.source = 'dataset'
            ORDER BY t.timestamp DESC LIMIT $2""", account_id, limit)
        for r in rows:
            display_ts = (r["timestamp"] + shift).replace(tzinfo=timezone.utc)
            frontend_type = mappers.RAW_TO_FRONTEND_TYPE.get(r["txn_type"], "transfer")
            channel = mappers.RAW_CHANNEL_TO_FRONTEND.get(r["channel"], "mobile")
            auth = mappers.RAW_AUTH_TO_FRONTEND.get(r["auth_method"], "PIN")
            counterparty = r["counterparty_id"] or "EXTERNAL"
            is_wallet = r["txn_type"] in ("ESEWA_P2P", "KHALTI_QR")
            direction = "debit" if r["txn_type"] != "CARD_POS" or rng.random() < 0.9 \
                else "credit"
            status = "success" if r["response_code"] == "00" else "failed"
            await conn.execute("""
                INSERT INTO app_transactions
                (id, reference, customer_id, customer_name, account_id,
                 account_number, cp_name, cp_account, cp_bank, cp_is_wallet,
                 amount, direction, type, channel, status, location_city,
                 location_lat, location_lng, device, ip_address, remarks, ts,
                 txn_type, counterparty_id, auth_method, mcc, is_vpn, is_tor,
                 impossible_travel, prev_txn_km, prev_txn_delta_min,
                 z_score_amount, txn_count_1m, dormancy_break, night_flag,
                 new_counterparty_flag, device_id)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                        $17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,
                        $31,$32,$33,$34,$35,$36,$37)
                ON CONFLICT (id) DO NOTHING""",
                r["txn_id"], f"REF{r['txn_id'][-10:].replace('-', '')}",
                customer_id, customer_names.get(customer_id, "Customer"),
                account_id, account_number,
                cp_name(counterparty, names, rng), counterparty,
                "eSewa" if is_wallet and r["txn_type"] == "ESEWA_P2P"
                else "Khalti" if is_wallet else rng.choice(BANKS), is_wallet,
                float(r["amount_npr"] or 0), direction, frontend_type, channel,
                status, r["ip_city"] or "Kathmandu",
                float(r["latitude"] or 27.7172), float(r["longitude"] or 85.324),
                "Mobile App" if r["channel"] == "MOBILE_APP" else r["channel"].title(),
                r["ip_address"] or "", r["notes"] or "", display_ts,
                r["txn_type"], counterparty, auth,
                r["merchant_category_code"], bool(r["is_vpn"]), bool(r["is_tor"]),
                bool(r["impossible_travel"]), float(r["prev_txn_km"] or 0),
                float(r["prev_txn_time_delta_min"] or 0),
                float(r["z_score_amount"] or 0), int(r["txn_count_1m"] or 0),
                bool(r["dormancy_break"]), bool(r["night_flag"]),
                bool(r["new_counterparty_flag"]), r["device_id"] or "")
            inserted += 1
    logger.info("Seeded %d app transactions", inserted)
    return inserted


async def hydrate_redis(conn: asyncpg.Connection) -> None:
    """Warm the Redis state the velocity/geo agents read (documented nightly-job
    hashes + last-location/known-device caches), from real history."""
    import redis as sync_redis

    from agents.velocity_agent import write_baseline, write_type_dist

    r = sync_redis.Redis(
        host=os.environ.get("FRAUD_REDIS_HOST", "localhost"),
        port=int(os.environ.get("FRAUD_REDIS_PORT", "6379")),
        decode_responses=True)

    accounts = [row["id"] for row in await conn.fetch("SELECT id FROM app_accounts")]
    for account_id in accounts:
        stats = await conn.fetchrow("""
            SELECT count(*) AS n, avg(amount_npr) AS avg_amt,
                   max(timestamp) AS last_ts, min(timestamp) AS first_ts
            FROM transactions_raw WHERE account_id = $1""", account_id)
        n = int(stats["n"] or 0)
        if n == 0:
            continue
        span_days = max(((stats["last_ts"] - stats["first_ts"]).days), 1)
        per_day = n / span_days
        write_baseline(account_id, {
            "hist_txn_count_2min_mean": round(per_day / 720, 6),
            "hist_txn_count_1hr_mean": round(per_day / 24, 6),
            "hist_amount_avg": round(float(stats["avg_amt"] or 0), 2),
            "observation_count": n,
        }, r)
        type_rows = await conn.fetch("""
            SELECT txn_type, count(*) AS c FROM transactions_raw
            WHERE account_id = $1 GROUP BY txn_type""", account_id)
        total = sum(int(t["c"]) for t in type_rows)
        write_type_dist(account_id, {
            t["txn_type"]: round(int(t["c"]) / total, 4) for t in type_rows}, r)

        geo = await conn.fetchrow("""
            SELECT latitude, longitude, timestamp FROM geo_events
            WHERE account_id = $1 ORDER BY timestamp DESC LIMIT 1""", account_id)
        if geo is not None:
            r.hset(f"geo:last:{account_id}", mapping={
                "lat": geo["latitude"], "lon": geo["longitude"],
                "ts_epoch_ms": int(geo["timestamp"].replace(
                    tzinfo=timezone.utc).timestamp() * 1000)})
            r.expire(f"geo:last:{account_id}", 90 * 86400)
        devices = await conn.fetch("""
            SELECT DISTINCT device_id FROM transactions_raw
            WHERE account_id = $1 AND device_id IS NOT NULL""", account_id)
        if devices:
            r.sadd(f"devices:known:{account_id}",
                   *[d["device_id"] for d in devices])
            r.expire(f"devices:known:{account_id}", 90 * 86400)
        n_geo = await conn.fetchval(
            "SELECT count(*) FROM geo_events WHERE account_id = $1", account_id)
        r.set(f"geo:obs:{account_id}", int(n_geo or 0), ex=3600)
    logger.info("Hydrated Redis baselines/type-dists/geo state for %d accounts",
                len(accounts))


async def backfill_pipeline(conn: asyncpg.Connection, k: int) -> None:
    """Score the most recent K seeded txns with the REAL pipeline so their
    displayed fraud analysis is genuine model output."""
    from agents.behavior_agent import BehaviorAgent
    from agents.geo_agent import GeoAgent
    from agents.graph_agent import NEO4J_DATABASE, get_driver
    from agents.velocity_agent import VelocityAgent
    from pipeline.agent_runner import (
        PipelineTxn, fuse, run_behavior, run_geo, run_graph, run_velocity)
    from pipeline.audit import write_pipeline_audit
    from pipeline.explanations import primary_shap_summary
    from synthesis_agent.api import store as synthesis_store

    velocity, geo, behavior = VelocityAgent(), GeoAgent(), BehaviorAgent()
    behavior_error: str | None = None
    await geo.connect()
    try:
        await behavior.connect()
    except Exception as exc:  # noqa: BLE001
        behavior_error = str(exc)
        logger.warning("Behavior agent unavailable for backfill: %s", exc)
    graph_driver = None
    try:
        graph_driver = get_driver()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neo4j unavailable for backfill: %s", exc)
    await synthesis_store.connect()

    rows = await conn.fetch("""
        SELECT a.id, a.account_id, t.txn_type, t.amount_npr, t.timestamp,
               t.device_id, g.latitude, g.longitude
        FROM app_transactions a
        JOIN transactions_raw t ON t.txn_id = a.id
        LEFT JOIN geo_events g ON g.txn_id = a.id
        WHERE a.fraud IS NULL
        ORDER BY t.timestamp DESC LIMIT $1""", k)
    # Also score a labelled fraud/legit mix (audit-only; no app ledger rows) so
    # the admin baseline-comparison has ground-truth positives to measure against.
    labelled = await conn.fetch("""
        (SELECT t.txn_id AS id, t.account_id, t.txn_type, t.amount_npr,
                t.timestamp, t.device_id, g.latitude, g.longitude
         FROM fraud_labels l
         JOIN transactions_raw t ON t.txn_id = l.txn_id
         LEFT JOIN geo_events g ON g.txn_id = t.txn_id
         WHERE l.is_fraud AND NOT EXISTS
               (SELECT 1 FROM synthesis_audit s WHERE s.txn_id = t.txn_id)
         ORDER BY t.timestamp DESC LIMIT 40)
        UNION ALL
        (SELECT t.txn_id, t.account_id, t.txn_type, t.amount_npr,
                t.timestamp, t.device_id, g.latitude, g.longitude
         FROM fraud_labels l
         JOIN transactions_raw t ON t.txn_id = l.txn_id
         LEFT JOIN geo_events g ON g.txn_id = t.txn_id
         WHERE NOT l.is_fraud AND NOT EXISTS
               (SELECT 1 FROM synthesis_audit s WHERE s.txn_id = t.txn_id)
         ORDER BY t.timestamp DESC LIMIT 60)""")
    app_txn_ids = {r["id"] for r in rows}
    rows = list(rows) + [r for r in labelled if r["id"] not in app_txn_ids]
    rows = sorted(rows, key=lambda r: r["timestamp"])  # replay chronologically
    logger.info("Backfilling %d transactions through the real pipeline...", len(rows))

    decisions: Counter[str] = Counter()
    for i, r in enumerate(rows, 1):
        txn = PipelineTxn(
            txn_id=r["id"], account_id=r["account_id"], txn_type=r["txn_type"],
            amount=float(r["amount_npr"] or 0),
            timestamp=r["timestamp"].replace(tzinfo=timezone.utc),
            device_id=r["device_id"],
            latitude=float(r["latitude"]) if r["latitude"] is not None else None,
            longitude=float(r["longitude"]) if r["longitude"] is not None else None)
        outcomes = {
            "velocity": await run_velocity(velocity, txn),
            "geo": await run_geo(geo, txn),
            "graph": await run_graph(graph_driver, NEO4J_DATABASE, txn),
            "behavior": await run_behavior(behavior, behavior_error, txn),
        }
        try:
            result, mapped, verdicts = fuse(outcomes, txn.txn_type)
        except ValueError as exc:
            logger.warning("No verdicts for %s: %s", txn.txn_id, exc)
            continue
        await write_pipeline_audit(
            txn_id=txn.txn_id, txn_type_raw=txn.txn_type,
            txn_type_mapped=mapped.value, verdicts=verdicts, result=result,
            outcomes=outcomes)
        fraud = mappers.build_fraud_analysis(
            agents={n: o.model_dump() for n, o in outcomes.items()},
            synthesis={"weights_applied": result.weights_applied.model_dump()},
            final={
                "decision": result.decision.value,
                "final_score": result.final_score,
                "fraud_pattern": result.fraud_pattern.value,
                "disagreement_score": result.disagreement_score,
                "otp_forced_by_disagreement": result.otp_forced_by_disagreement,
                "shap": primary_shap_summary(outcomes),
            },
            amount=txn.amount, hour=r["timestamp"].hour,
            total_ms=sum(o.latency_ms or 0 for o in outcomes.values()))
        fraud.pop("agentsUsed", None)
        decision = result.decision.value
        decisions[decision] += 1
        # Labelled-eval txns outside the app ledger only get the audit row.
        await conn.execute("""
            UPDATE app_transactions
            SET decision=$2, risk_score=$3, latency_ms=$4, fraud=$5, fraud_type=$6,
                status = CASE WHEN $2 = 'BLOCK' THEN 'blocked' ELSE status END
            WHERE id=$1""",
            r["id"], decision, result.final_score,
            round(sum(o.latency_ms or 0 for o in outcomes.values()), 1),
            json.dumps(fraud), fraud["synthesis"]["fraudType"])
        if i % 25 == 0:
            logger.info("  %d/%d scored (%s)", i, len(rows), dict(decisions))

    logger.info("Backfill complete: %s", dict(decisions))
    await geo.close()
    await behavior.close()
    await synthesis_store.close()
    if graph_driver is not None:
        graph_driver.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customers", type=int, default=100)
    parser.add_argument("--backfill", type=int, default=150)
    parser.add_argument("--skip-backfill", action="store_true")
    args = parser.parse_args()

    dsn = os.environ.get("FRAUD_DB_DSN", "dbname=fraud_detection_global")
    conn = await asyncpg.connect(**pg_connect_kwargs(dsn))
    try:
        await ensure_schema(conn)
        names = await seed_identities(conn, args.customers)
        await seed_transactions(conn, names)
        await hydrate_redis(conn)
        if not args.skip_backfill:
            await backfill_pipeline(conn, args.backfill)
    finally:
        await conn.close()
    logger.info("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
