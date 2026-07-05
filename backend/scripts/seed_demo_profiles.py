"""Seed the 3 fixed demo login profiles (app/demo_profiles.py).

    uv run python -m scripts.seed_demo_profiles

Idempotent. Upserts app_customers / app_accounts for each profile (identity +
balance), and copies a little real transaction history into app_transactions so
the dashboard has content. Accounts with a customer_profiles row inherit its
district/kyc; the watchlist collector (no profile) gets sensible defaults.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

import asyncpg  # noqa: E402

from app.db_schema import ensure_schema  # noqa: E402
from app.demo_profiles import DEMO_PROFILES  # noqa: E402
from app.services import mappers  # noqa: E402
from behavior_agent.config import pg_connect_kwargs  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("seed-demo")

BANKS = ["Global IME Bank", "Nabil Bank", "NIC Asia Bank", "Everest Bank"]


async def _upsert_profile(conn: asyncpg.Connection, profile: dict) -> None:
    account_id = profile["accountId"]
    customer_id = profile["customerId"]
    prof = await conn.fetchrow(
        "SELECT * FROM customer_profiles WHERE account_id = $1", account_id)
    district = (prof["district"] if prof else None) or "Kathmandu"
    province = (prof["province"] if prof else None) or "Bagmati"
    kyc_tier = {"BASIC": "TIER_1", "STANDARD": "TIER_2", "ENHANCED": "TIER_3"}.get(
        prof["kyc_tier"] if prof else None, "TIER_2")
    joined = (prof["customer_since"] if prof else None) or datetime(2021, 1, 1).date()
    account_number = f"12010101{abs(hash(account_id)) % 10**8:08d}"

    await conn.execute("""
        INSERT INTO app_customers
        (id, agent_account_id, name, gender, account_number, mobile, email,
         address, city, kyc_status, risk_level, joined_at, avatar_color,
         citizenship_no, branch, district, province, kyc_tier, is_dormant,
         num_beneficiaries_registered)
        VALUES ($1,$2,$3,'male',$4,$5,$6,$7,$8,'verified',$9,$10,'#0ea5e9',
                $11,'Kathmandu Main',$12,$13,$14,false,$15)
        ON CONFLICT (id) DO UPDATE SET
          name=$3, mobile=$5, agent_account_id=$2, account_number=$4""",
        customer_id, account_id, profile["name"], account_number,
        profile["mobile"], f"{profile['name'].lower().replace(' ', '.')}@email.com",
        f"{district}, {province}", district.split()[0] if district else "Kathmandu",
        "high" if profile["expected"] == "BLOCK" else
        "medium" if profile["expected"] == "OTP" else "low",
        datetime.combine(joined, datetime.min.time(), timezone.utc),
        f"{abs(hash(account_id)) % 90 + 10}-01-70-{abs(hash(customer_id)) % 90000 + 10000}",
        district, province, kyc_tier,
        int(prof["num_beneficiaries_registered"]) if prof else 2)

    await conn.execute("""
        INSERT INTO app_accounts
        (id, customer_id, type, name, account_number, balance, currency, status,
         interest_rate)
        VALUES ($1,$2,'savings','Everyday Savings',$3,$4,'NPR','active',5.5)
        ON CONFLICT (id) DO UPDATE SET balance=$4, status='active',
          customer_id=$2, account_number=$3""",
        account_id, customer_id, account_number, profile["balance"])


async def _seed_history(conn: asyncpg.Connection, profile: dict) -> None:
    account_id = profile["accountId"]
    customer_id = profile["customerId"]
    existing = await conn.fetchval(
        "SELECT count(*) FROM app_transactions WHERE customer_id = $1", customer_id)
    if existing and int(existing) >= 5:
        return
    account_number = await conn.fetchval(
        "SELECT account_number FROM app_accounts WHERE id = $1", account_id)
    rows = await conn.fetch("""
        SELECT t.*, g.ip_city, g.latitude, g.longitude
        FROM transactions_raw t LEFT JOIN geo_events g ON g.txn_id = t.txn_id
        WHERE t.account_id = $1 AND t.source = 'dataset'
        ORDER BY t.timestamp DESC LIMIT 12""", account_id)
    now = datetime.now(timezone.utc)
    if rows:
        for i, r in enumerate(rows):
            ts = now - timedelta(days=i + 1, hours=i)
            ftype = mappers.RAW_TO_FRONTEND_TYPE.get(r["txn_type"], "transfer")
            await conn.execute("""
                INSERT INTO app_transactions
                (id, reference, customer_id, customer_name, account_id,
                 account_number, cp_name, cp_account, cp_bank, cp_is_wallet,
                 amount, direction, type, channel, status, decision, risk_score,
                 location_city, location_lat, location_lng, device, remarks, ts,
                 txn_type, counterparty_id, auth_method, mcc)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,false,$10,'debit',$11,'mobile',
                        'success','PASS',NULL,$12,$13,$14,'Mobile App',$15,$16,$17,
                        $18,'PIN','6011')
                ON CONFLICT (id) DO NOTHING""",
                r["txn_id"], f"REF{r['txn_id'][-8:]}", customer_id, profile["name"],
                account_id, account_number, "Merchant", r["counterparty_id"] or "EXT",
                BANKS[i % len(BANKS)], float(r["amount_npr"] or 0), ftype,
                r["ip_city"] or "Kathmandu", float(r["latitude"] or 27.7172),
                float(r["longitude"] or 85.324), r["notes"] or "", ts,
                r["txn_type"], r["counterparty_id"] or "EXT")
    else:
        # Collector: no dataset history — a couple of plausible prior debits.
        for i in range(3):
            ts = now - timedelta(days=i * 2 + 1)
            await conn.execute("""
                INSERT INTO app_transactions
                (id, reference, customer_id, customer_name, account_id,
                 account_number, cp_name, cp_account, cp_bank, cp_is_wallet,
                 amount, direction, type, channel, status, decision, risk_score,
                 location_city, location_lat, location_lng, device, remarks, ts,
                 txn_type, counterparty_id, auth_method, mcc)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,false,$10,'debit','transfer',
                        'mobile','success','PASS',NULL,'Kathmandu',27.7172,85.324,
                        'Mobile App',$11,$12,'RTGS',$13,'PIN','6011')
                ON CONFLICT (id) DO NOTHING""",
                f"TXN-DEMO-{customer_id[-5:]}-{i}", f"REFDEMO{i}", customer_id,
                profile["name"], account_id, account_number, "Counterparty",
                f"ACC-{1000000 + i}", BANKS[i % len(BANKS)],
                float(50000 + i * 25000), "Consolidation", ts, f"ACC-{1000000 + i}")


async def main() -> None:
    dsn = os.environ.get("FRAUD_DB_DSN", "dbname=fraud_detection_global")
    conn = await asyncpg.connect(**pg_connect_kwargs(dsn))
    try:
        await ensure_schema(conn)
        for profile in DEMO_PROFILES:
            await _upsert_profile(conn, profile)
            await _seed_history(conn, profile)
            logger.info("Seeded profile %s (%s -> %s, expect %s)",
                        profile["id"], profile["name"], profile["accountId"],
                        profile["expected"])
    finally:
        await conn.close()
    logger.info("Demo profiles ready.")


if __name__ == "__main__":
    asyncio.run(main())
