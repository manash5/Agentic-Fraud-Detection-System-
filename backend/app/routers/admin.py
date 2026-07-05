"""Admin console endpoints: dashboards, live feeds, system health, network
graph, decision-threshold settings, baseline comparison, CSV reports.

Data sources are entirely live: app_transactions / app_customers /
app_otp_events aggregates, synthesis_audit joined to fraud_labels for model
metrics, direct pings for system health, and Neo4j for the fraud-ring graph.
The admin console (like the current frontend) has no separate analyst login;
these endpoints are deliberately session-free — front them with network-level
auth in a real deployment.
"""

from __future__ import annotations

import io
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.deps import THRESHOLDS_KEY, app_db, redis_client
from app.services import mappers

logger = logging.getLogger("admin-router")

router = APIRouter(prefix="/admin", tags=["admin"])

_STARTED_AT = time.monotonic()
_health_counters: dict[str, list[int]] = {}  # key -> [ok, total]


@router.get("/stats")
async def stats() -> dict[str, Any]:
    async with app_db.pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
              count(*) FILTER (WHERE ts::date = (now() at time zone 'utc')::date) AS today_count,
              coalesce(sum(amount) FILTER (WHERE ts::date = (now() at time zone 'utc')::date), 0) AS today_volume,
              coalesce(sum(amount) FILTER (WHERE decision = 'BLOCK'), 0) AS fraud_prevented,
              count(*) FILTER (WHERE decision = 'OTP') AS otp_challenges,
              count(*) FILTER (WHERE decision = 'BLOCK') AS blocked_count,
              avg(latency_ms) FILTER (WHERE latency_ms > 0) AS avg_ms
            FROM app_transactions""")
        customers = await conn.fetchval(
            "SELECT count(*) FROM app_customers WHERE risk_level != 'high'")
    ok = sum(c[0] for c in _health_counters.values())
    total = sum(c[1] for c in _health_counters.values())
    uptime = round(100.0 * ok / total, 2) if total else 100.0
    return {
        "todayCount": int(row["today_count"]),
        "todayVolume": float(row["today_volume"]),
        "fraudPrevented": float(row["fraud_prevented"]),
        "otpChallenges": int(row["otp_challenges"]),
        "blockedCount": int(row["blocked_count"]),
        "activeCustomers": int(customers or 0),
        "uptime": uptime,
        "avgDetectionMs": round(float(row["avg_ms"] or 0.0), 0),
    }


@router.get("/trends")
async def trends() -> list[dict[str, Any]]:
    async with app_db.pool.acquire() as conn:
        rows = await conn.fetch("""
            WITH days AS (
              SELECT generate_series((now() at time zone 'utc')::date - 13,
                                     (now() at time zone 'utc')::date,
                                     '1 day')::date AS day)
            SELECT d.day,
                   count(t.id) AS transactions,
                   count(t.id) FILTER (WHERE t.decision IN ('OTP','BLOCK')) AS fraud,
                   coalesce(sum(t.amount), 0) AS volume
            FROM days d LEFT JOIN app_transactions t ON t.ts::date = d.day
            GROUP BY d.day ORDER BY d.day""")
    return [
        {"label": r["day"].strftime("%d %b"), "transactions": int(r["transactions"]),
         "fraud": int(r["fraud"]), "volume": round(float(r["volume"]))}
        for r in rows
    ]


@router.get("/risk-locations")
async def risk_locations() -> list[dict[str, Any]]:
    async with app_db.pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT location_city AS city, count(*) AS count,
                   avg(coalesce(risk_score, 0)) AS avg_risk
            FROM app_transactions WHERE location_city IS NOT NULL
            GROUP BY location_city ORDER BY avg_risk DESC LIMIT 8""")
    return [{"city": r["city"], "count": int(r["count"]),
             "avgRisk": round(float(r["avg_risk"]), 2)} for r in rows]


@router.get("/live-transactions")
async def live_transactions(limit: int = 60) -> list[dict[str, Any]]:
    async with app_db.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM app_transactions ORDER BY ts DESC LIMIT $1", min(limit, 200))
    return [mappers.row_to_transaction(r) for r in rows]


@router.get("/flagged")
async def flagged() -> list[dict[str, Any]]:
    async with app_db.pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM app_transactions WHERE decision IN ('OTP','BLOCK')
               ORDER BY ts DESC LIMIT 80""")
    return [mappers.row_to_transaction(r) for r in rows]


@router.get("/customers")
async def all_customers() -> list[dict[str, Any]]:
    async with app_db.pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM app_customers ORDER BY name")
    return [mappers.row_to_customer(r) for r in rows]


@router.get("/transactions/{txn_id}")
async def admin_transaction(txn_id: str) -> dict[str, Any]:
    """Single-transaction detail for the analyst console (session-free like
    the rest of /admin)."""
    async with app_db.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM app_transactions WHERE id = $1", txn_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return mappers.row_to_transaction(row)


@router.get("/customers/{customer_id}")
async def admin_customer(customer_id: str) -> dict[str, Any]:
    async with app_db.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM app_customers WHERE id = $1", customer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return mappers.row_to_customer(row)


@router.get("/accounts")
async def all_accounts() -> list[dict[str, Any]]:
    async with app_db.pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM app_accounts ORDER BY id")
    return [mappers.row_to_account(r) for r in rows]


@router.get("/otp-sessions")
async def otp_sessions() -> list[dict[str, Any]]:
    """Transactions with an OTP challenge, newest first (admin OTP Center)."""
    async with app_db.pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT ON (t.id) t.*
            FROM app_transactions t JOIN app_otp_events o ON o.txn_id = t.id
            ORDER BY t.id, t.ts DESC""")
    txns = [mappers.row_to_transaction(r) for r in rows]
    return sorted(txns, key=lambda t: t["timestamp"], reverse=True)


@router.get("/otp-events")
async def otp_events() -> list[dict[str, Any]]:
    """Raw OTP audit trail with per-challenge status/attempts."""
    async with app_db.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM app_otp_events ORDER BY sent_at DESC LIMIT 100")
    return [
        {"id": r["id"], "txnId": r["txn_id"], "accountId": r["account_id"],
         "mobile": r["mobile"], "channel": r["channel"],
         "triggerReason": r["trigger_reason"], "status": r["status"],
         "attempts": r["attempts"],
         "sentAt": r["sent_at"].isoformat(),
         "verifiedAt": r["verified_at"].isoformat() if r["verified_at"] else None}
        for r in rows
    ]


# -- system health ----------------------------------------------------------------


async def _timed(coro) -> tuple[bool, float]:
    started = time.monotonic()
    try:
        await coro
        return True, (time.monotonic() - started) * 1000
    except Exception:  # noqa: BLE001
        return False, (time.monotonic() - started) * 1000


def _record(key: str, ok: bool) -> float:
    counter = _health_counters.setdefault(key, [0, 0])
    counter[0] += 1 if ok else 0
    counter[1] += 1
    return round(100.0 * counter[0] / counter[1], 2)


@router.get("/system-health")
async def system_health(request: Request) -> list[dict[str, Any]]:
    state = request.app.state
    services: list[dict[str, Any]] = []

    async def probe(name: str, key: str, category: str, coro) -> None:
        ok, ms = await _timed(coro)
        uptime = _record(key, ok)
        services.append({
            "name": name, "key": key,
            "status": "operational" if ok else "down",
            "uptime": uptime, "latencyMs": round(ms, 1), "category": category,
        })

    async def _pg() -> None:
        async with app_db.pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

    async def _neo4j() -> None:
        driver = state.graph_driver
        if driver is None:
            raise RuntimeError("neo4j not connected")

        def _ping() -> None:
            from agents.graph_agent import NEO4J_DATABASE
            with driver.session(database=NEO4J_DATABASE) as session:
                session.run("RETURN 1").consume()
        await run_in_threadpool(_ping)

    async def _velocity() -> None:
        await run_in_threadpool(state.velocity_agent.client.ping)

    async def _geo() -> None:
        await state.geo_agent.redis.ping()

    async def _behavior() -> None:
        if state.behavior_model_error is not None:
            raise RuntimeError(state.behavior_model_error)
        if state.behavior_agent.pg_pool is None:
            raise RuntimeError("postgres not connected")
        async with state.behavior_agent.pg_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

    async def _kafka() -> None:
        if not state.kafka_ready:
            raise RuntimeError("kafka producer not started")

    await probe("API Gateway", "gateway", "gateway", _pg())
    await probe("Velocity Agent", "velocity", "agent", _velocity())
    await probe("Geo Agent", "geo", "agent", _geo())
    await probe("Graph Agent", "graph", "agent", _neo4j())
    await probe("Behavior Agent", "behavior", "agent", _behavior())
    await probe("Synthesis Agent", "synthesis", "agent", _pg())
    await probe("OTP Service", "otp", "core", redis_client.ping())
    await probe("Kafka Event Bus", "kafka", "core", _kafka())
    await probe("PostgreSQL", "postgres", "datastore", _pg())
    await probe("Redis", "redis", "datastore", redis_client.ping())
    return services


# -- fraud-ring network graph -------------------------------------------------------

RING_COLLECTOR = "ACC-0011204"  # COMM-042 watchlist collector (feature_config.yaml)


@router.get("/network-graph")
async def network_graph(request: Request, accountId: str | None = None) -> dict[str, Any]:
    """COMM-042 ring members plus (when accountId given) the account's real
    Neo4j SENT-neighborhood, for the admin Account Network panel."""
    driver = request.app.state.graph_driver
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j unavailable")

    def _query() -> dict[str, Any]:
        from agents.graph_agent import NEO4J_DATABASE
        with driver.session(database=NEO4J_DATABASE) as session:
            members = session.run(
                """MATCH (m:Account)-[r:SENT]->(c:Account {id: $collector})
                   RETURN m.id AS id, count(r) AS transfers,
                          coalesce(sum(r.amount_npr), 0) AS total
                   ORDER BY total DESC LIMIT 12""",
                collector=RING_COLLECTOR).data()
            neighbors: list[dict[str, Any]] = []
            if accountId:
                neighbors = session.run(
                    """MATCH (a:Account {id: $id})-[r:SENT]->(t:Account)
                       RETURN t.id AS id, 'out' AS direction, count(r) AS transfers,
                              coalesce(t.is_fraud_seed, false) AS is_fraud_seed
                       ORDER BY transfers DESC LIMIT 5""",
                    id=accountId).data()
                neighbors += session.run(
                    """MATCH (s:Account)-[r:SENT]->(a:Account {id: $id})
                       RETURN s.id AS id, 'in' AS direction, count(r) AS transfers,
                              coalesce(s.is_fraud_seed, false) AS is_fraud_seed
                       ORDER BY transfers DESC LIMIT 4""",
                    id=accountId).data()
            return {"collector": RING_COLLECTOR, "members": members,
                    "neighbors": neighbors}

    try:
        return await run_in_threadpool(_query)
    except Exception as exc:  # noqa: BLE001
        logger.error("Network graph query failed: %s", exc)
        raise HTTPException(status_code=503, detail="Neo4j query failed") from None


# -- baseline comparison (real metrics over pipeline-scored labelled txns) ----------


@router.get("/baseline-comparison")
async def baseline_comparison() -> dict[str, Any]:
    """Model vs amount/hour rule engine, computed on synthesis_audit rows that
    have ground-truth fraud_labels (the seed backfills historical txns through
    the real pipeline, so these are genuine model outputs)."""
    async with app_db.pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT ON (s.txn_id)
                   s.final_score, s.decision, l.is_fraud,
                   t.amount_npr, extract(hour FROM t.timestamp) AS hour
            FROM synthesis_audit s
            JOIN fraud_labels l ON l.txn_id = s.txn_id
            JOIN transactions_raw t ON t.txn_id = s.txn_id
            ORDER BY s.txn_id, s.created_at DESC""")
        p95 = await conn.fetchval("""
            SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
            FROM app_transactions WHERE latency_ms > 0""")

    def rates(pred_flagged: list[bool], truth: list[bool]) -> tuple[float, float]:
        tp = sum(1 for p, t in zip(pred_flagged, truth) if p and t)
        fp = sum(1 for p, t in zip(pred_flagged, truth) if p and not t)
        fn = sum(1 for p, t in zip(pred_flagged, truth) if not p and t)
        tn = sum(1 for p, t in zip(pred_flagged, truth) if not p and not t)
        recall = 100.0 * tp / (tp + fn) if tp + fn else 0.0
        fpr = 100.0 * fp / (fp + tn) if fp + tn else 0.0
        return recall, fpr

    def auroc(scores: list[float], truth: list[bool]) -> float:
        pos = [s for s, t in zip(scores, truth) if t]
        neg = [s for s, t in zip(scores, truth) if not t]
        if not pos or not neg:
            return 0.0
        wins = sum(1 for p in pos for n in neg if p > n)
        ties = sum(1 for p in pos for n in neg if p == n)
        return 100.0 * (wins + 0.5 * ties) / (len(pos) * len(neg))

    truth = [bool(r["is_fraud"]) for r in rows]
    model_scores = [float(r["final_score"]) for r in rows]
    model_flagged = [r["decision"] in ("OTP", "BLOCK") for r in rows]
    baseline_decisions = [
        mappers.baseline_rule_decision(float(r["amount_npr"]), int(r["hour"]))
        for r in rows]
    baseline_flagged = [d in ("OTP", "BLOCK") for d in baseline_decisions]
    baseline_scores = [
        {"PASS": 0.1, "OTP": 0.5, "BLOCK": 0.9}[d] for d in baseline_decisions]

    model_recall, model_fpr = rates(model_flagged, truth)
    rule_recall, rule_fpr = rates(baseline_flagged, truth)
    missed_by_rule = sum(
        1 for mf, bf, t in zip(model_flagged, baseline_flagged, truth)
        if mf and t and not bf)

    return {
        "sampleSize": len(rows),
        "ruleEngineAurocPct": round(auroc(baseline_scores, truth), 1),
        "modelAurocPct": round(auroc(model_scores, truth), 1),
        "ruleEngineRecallPct": round(rule_recall, 1),
        "modelRecallPct": round(model_recall, 1),
        "ruleEngineFprPct": round(rule_fpr, 1),
        "modelFprPct": round(model_fpr, 1),
        "p95LatencyMs": round(float(p95 or 0.0)),
        "ruleEngineWouldAllow": missed_by_rule,
    }


# -- settings (live decision thresholds) --------------------------------------------


class ThresholdSettings(BaseModel):
    otpThreshold: float = Field(0.30, ge=0.0, le=1.0)
    blockThreshold: float = Field(0.70, ge=0.0, le=1.0)
    disagreementThreshold: float = Field(0.04, ge=0.0, le=1.0)


@router.get("/settings")
async def get_settings() -> dict[str, float]:
    async with app_db.pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = 'thresholds'")
    if raw is None:
        return ThresholdSettings().model_dump()
    return json.loads(raw) if isinstance(raw, str) else raw


@router.put("/settings")
async def put_settings(body: ThresholdSettings) -> dict[str, Any]:
    if body.otpThreshold >= body.blockThreshold:
        raise HTTPException(status_code=400,
                            detail="OTP threshold must be below the block threshold.")
    payload = json.dumps(body.model_dump())
    async with app_db.pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO app_settings (key, value) VALUES ('thresholds', $1)
               ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = now()""",
            payload)
    await redis_client.set(THRESHOLDS_KEY, payload)
    logger.info("Decision thresholds updated: %s", payload)
    return {"saved": True, **body.model_dump()}


# -- CSV reports ---------------------------------------------------------------------

_REPORT_QUERIES: dict[str, tuple[str, str]] = {
    "daily-summary": (
        "Daily transaction summary (14 days)",
        """SELECT ts::date AS date, count(*) AS transactions,
                  sum(amount) AS volume_npr,
                  count(*) FILTER (WHERE decision='OTP') AS otp_challenges,
                  count(*) FILTER (WHERE decision='BLOCK') AS blocked,
                  round(avg(coalesce(risk_score,0))::numeric, 4) AS avg_risk
           FROM app_transactions
           WHERE ts > now() - interval '14 days'
           GROUP BY ts::date ORDER BY ts::date DESC"""),
    "flagged-transactions": (
        "Flagged transactions",
        """SELECT id, reference, ts, customer_name, account_id, cp_name, amount,
                  decision, round(coalesce(risk_score,0)::numeric,4) AS risk_score,
                  fraud_type, txn_type
           FROM app_transactions WHERE decision IN ('OTP','BLOCK')
           ORDER BY ts DESC LIMIT 1000"""),
    "otp-events": (
        "OTP challenge audit",
        """SELECT id, txn_id, account_id, mobile, channel, trigger_reason,
                  status, attempts, sent_at, verified_at
           FROM app_otp_events ORDER BY sent_at DESC LIMIT 1000"""),
    "model-verdicts": (
        "Model verdict audit",
        """SELECT txn_id, txn_type_raw, fraud_pattern, final_score, decision,
                  disagreement_score, otp_forced_by_disagreement,
                  array_to_string(agents_used, '|') AS agents_used, created_at
           FROM synthesis_audit ORDER BY created_at DESC LIMIT 5000"""),
}


@router.get("/reports/{key}")
async def report_csv(key: str) -> StreamingResponse:
    if key not in _REPORT_QUERIES:
        raise HTTPException(status_code=404,
                            detail=f"Unknown report. Available: {', '.join(_REPORT_QUERIES)}")
    _, sql = _REPORT_QUERIES[key]
    async with app_db.pool.acquire() as conn:
        rows = await conn.fetch(sql)
    buf = io.StringIO()
    if rows:
        cols = list(rows[0].keys())
        buf.write(",".join(cols) + "\n")
        for r in rows:
            buf.write(",".join(_csv_cell(r[c]) for c in cols) + "\n")
    else:
        buf.write("no data\n")
    buf.seek(0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="{key}-{stamp}.csv"'})


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if any(ch in text for ch in ",\"\n"):
        return '"' + text.replace('"', '""') + '"'
    return text
