"""Banking reads: customers (Redis cache-aside), accounts, cards, transactions,
recipient resolution. All endpoints require a session."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import CUSTOMER_CACHE_KEY, app_db, get_current_user, redis_client
from app.services import mappers

logger = logging.getLogger("banking-router")

router = APIRouter(tags=["banking"])

CUSTOMER_CACHE_TTL_S = 300


@router.get("/customers/{customer_id}")
async def get_customer(customer_id: str,
                       user: dict = Depends(get_current_user)) -> dict[str, Any]:
    cache_key = CUSTOMER_CACHE_KEY.format(customer_id=customer_id)
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception as exc:  # noqa: BLE001 — cache miss path on Redis trouble
        logger.warning("Customer cache read failed: %s", exc)
    async with app_db.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM app_customers WHERE id = $1", customer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    customer = mappers.row_to_customer(row)
    try:
        await redis_client.setex(cache_key, CUSTOMER_CACHE_TTL_S, json.dumps(customer))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Customer cache write failed: %s", exc)
    return customer


@router.get("/accounts")
async def get_accounts(customerId: str,
                       user: dict = Depends(get_current_user)) -> list[dict[str, Any]]:
    async with app_db.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM app_accounts WHERE customer_id = $1 ORDER BY type", customerId)
    return [mappers.row_to_account(r) for r in rows]


@router.get("/cards")
async def get_cards(customerId: str,
                    user: dict = Depends(get_current_user)) -> list[dict[str, Any]]:
    async with app_db.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM app_cards WHERE customer_id = $1", customerId)
    return [mappers.row_to_card(r) for r in rows]


@router.get("/transactions")
async def get_transactions(
    user: dict = Depends(get_current_user),
    customerId: str | None = None,
    search: str | None = None,
    type: str | None = None,
    decision: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    minAmount: float | None = None,
    maxAmount: float | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    args: list[Any] = []

    def add(clause: str, value: Any) -> None:
        args.append(value)
        clauses.append(clause.format(n=len(args)))

    if customerId:
        add("customer_id = ${n}", customerId)
    if type and type != "all":
        add("type = ${n}", type)
    if decision and decision != "all":
        add("decision = ${n}", decision)
    if from_:
        add("ts >= ${n}::timestamptz", from_)
    if to:
        add("ts <= ${n}::timestamptz", to)
    if minAmount is not None:
        add("amount >= ${n}", minAmount)
    if maxAmount is not None:
        add("amount <= ${n}", maxAmount)
    if search:
        add("(reference ILIKE ${n} OR id ILIKE ${n} OR cp_name ILIKE ${n} "
            "OR customer_name ILIKE ${n})", f"%{search}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    args.append(min(limit, 500))
    sql = (f"SELECT * FROM app_transactions {where} "
           f"ORDER BY ts DESC LIMIT ${len(args)}")
    async with app_db.pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [mappers.row_to_transaction(r) for r in rows]


@router.get("/transactions/{txn_id}")
async def get_transaction(txn_id: str,
                          user: dict = Depends(get_current_user)) -> dict[str, Any]:
    async with app_db.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM app_transactions WHERE id = $1", txn_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return mappers.row_to_transaction(row)


@router.get("/recipients/resolve")
async def resolve_recipient(
    account: str,
    destination: str,
    bank: str | None = None,
    user: dict = Depends(get_current_user),
) -> dict[str, str]:
    """Account-holder name auto-fetch for the transfer form."""
    clean = account.strip()
    if len(clean) < 6:
        raise HTTPException(status_code=404, detail="Account number not found.")
    async with app_db.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT name FROM app_customers WHERE account_number = $1 OR id = $1", clean)
    if destination in ("global_ime", "own"):
        resolved_bank = "Global IME Bank"
    else:
        resolved_bank = bank or "Global IME Bank"
    if row is not None:
        return {"accountNumber": clean, "name": row["name"], "bank": resolved_bank}
    if destination in ("global_ime", "own"):
        # Same-bank transfers must resolve to a real customer of this bank.
        raise HTTPException(status_code=404, detail="Account number not found.")
    # Other-bank/wallet recipients live outside our books: echo a deterministic
    # holder name derived from the account number (a real integration would
    # call the interbank name-enquiry API here).
    seed = sum(ord(c) for c in clean)
    first = ["Aarav", "Sita", "Bibek", "Anisha", "Prakash", "Maya",
             "Suraj", "Pooja", "Kiran", "Nisha"][seed % 10]
    last = ["Shrestha", "Gurung", "Tamang", "Karki", "Adhikari",
            "Thapa", "Rai", "Magar", "Basnet", "Koirala"][(seed // 10) % 10]
    return {"accountNumber": clean, "name": f"{first} {last}", "bank": resolved_bank}
