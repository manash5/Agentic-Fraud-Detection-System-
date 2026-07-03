"""Graph Agent — Track B (GIBL 2026) account-network fraud risk over Neo4j.

Given a bank account, walk the account graph and emit a fraud score in [0,1],
a coarse ``flag`` (LOW/MEDIUM/HIGH) for the Synthesis Agent (graph weight 0.4),
and an ALLOW / OTP_ONLY / BLOCK decision.

Graph model (already loaded into the ``fraud-detection`` database)::

    (:Account|:Merchant|:ExternalAccount {id})
        -[:SENT {txn_id, amount_npr, timestamp, txn_type,
                 is_first_transfer_to_target, within_24h_reciprocal,
                 is_structuring_amount}]->(target)

Feature policy (per the data description): node-table ``degree_in`` /
``degree_out`` / ``total_*_npr`` are the RELIABLE 90-day full-window
aggregates and are used for volume/mule features; fan-in / fan-out topology is
recomputed from the SAMPLED :SENT edges. Every threshold and weight lives in
``feature_engineering/feature_config.yaml`` under ``graph_agent:`` — no magic
numbers here.

Signals (doc references in the config):
1. Smurfing / fan-out  — max distinct recipients in a single calendar day.
2. Fan-in              — distinct all-time senders (collector-level >= 50).
3. Structuring         — share of outgoing transfers hugging NRB thresholds.
4. Layering            — within-24h reciprocal transfers.
5. Mule shape          — high degree_in, near-zero net balance (node table).
6. COMM-042 proximity  — direct / 2-hop reach to WATCHLIST collector ACC-0011204.
7. Identity            — the account's own risk_tier / is_fraud_seed.

All Cypher is parameterised (ids are never string-formatted into queries) and
every session is opened against ``database="fraud-detection"`` explicitly —
this instance has no default ``neo4j`` database.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from feature_engineering.config import load_config  # noqa: E402

# Load backend/.env so NEO4J_* are available when run as a CLI.
load_dotenv(BACKEND_DIR / ".env")

NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "fraud-detection")


# -- connection ----------------------------------------------------------------


def get_driver() -> Driver:
    """Neo4j driver from the .env connection settings.

    This instance has NO default ``neo4j`` database, so callers must always
    open sessions with ``database=NEO4J_DATABASE``.
    """
    uri = os.environ.get("NEO4J_URI", "neo4j://127.0.0.1:7687")
    user = os.environ.get("NEO4J_USERNAME", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "neo4j")
    return GraphDatabase.driver(uri, auth=(user, password))


def _gcfg(cfg: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    return (cfg or load_config())["graph_agent"]


def _clip01(x: float) -> float:
    return min(1.0, max(0.0, x))


# -- signal queries (all parameterised) ---------------------------------------

_NODE_FACTS = """
MATCH (a:Account {id: $id})
RETURN a.degree_in         AS degree_in,
       a.degree_out        AS degree_out,
       a.total_received_npr AS total_received_npr,
       a.total_sent_npr     AS total_sent_npr,
       a.risk_tier          AS risk_tier,
       a.is_fraud_seed      AS is_fraud_seed
"""

_OUT_AGG = """
MATCH (a {id: $id})-[r:SENT]->(t)
RETURN count(r)                                                  AS out_txns,
       count(DISTINCT t)                                         AS out_counterparties,
       sum(CASE WHEN r.is_structuring_amount THEN 1 ELSE 0 END)  AS structuring_txns,
       sum(CASE WHEN r.within_24h_reciprocal THEN 1 ELSE 0 END)  AS reciprocal_txns
"""

# Distinct recipients per calendar day; the max over all days is the fan-out
# burst (24h proxy, since the edge set is a sample rather than the full 90d).
_MAX_FANOUT = """
MATCH (a {id: $id})-[r:SENT]->(t)
WITH date(r.timestamp) AS day, count(DISTINCT t) AS cpd
RETURN coalesce(max(cpd), 0) AS max_fanout_24h
"""

_IN_AGG = """
MATCH (s)-[r:SENT]->(a {id: $id})
RETURN count(r) AS in_txns, count(DISTINCT s) AS in_senders
"""

# EXISTS subqueries keep this O(neighbourhood) rather than materialising every
# path into the WATCHLIST hub.
_COLLECTOR_PROXIMITY = """
RETURN EXISTS { MATCH (a {id: $id})-[:SENT]->(c {id: $collector}) }        AS direct,
       EXISTS { MATCH (a {id: $id})-[:SENT*1..2]->(c {id: $collector}) }   AS within_two
"""


def collect_signals(
    account_id: str, session, *, cfg: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    """Run every signal query for one account. None if the account is unknown."""
    collector = _gcfg(cfg)["collector_id"]
    node = session.run(_NODE_FACTS, id=account_id).single()
    if node is None:
        return None
    out = session.run(_OUT_AGG, id=account_id).single()
    max_fanout = session.run(_MAX_FANOUT, id=account_id).single()["max_fanout_24h"]
    inn = session.run(_IN_AGG, id=account_id).single()
    is_self = account_id == collector
    if is_self:
        direct, within_two = False, False
    else:
        prox = session.run(_COLLECTOR_PROXIMITY, id=account_id, collector=collector).single()
        direct, within_two = bool(prox["direct"]), bool(prox["within_two"])

    return {
        "out_txns": int(out["out_txns"] or 0),
        "out_counterparties": int(out["out_counterparties"] or 0),
        "max_fanout_24h": int(max_fanout or 0),
        "in_txns": int(inn["in_txns"] or 0),
        "in_senders": int(inn["in_senders"] or 0),
        "structuring_txns": int(out["structuring_txns"] or 0),
        "reciprocal_txns": int(out["reciprocal_txns"] or 0),
        "degree_in_90d": int(node["degree_in"] or 0),
        "degree_out_90d": int(node["degree_out"] or 0),
        "total_received_npr": float(node["total_received_npr"] or 0.0),
        "total_sent_npr": float(node["total_sent_npr"] or 0.0),
        "direct_to_collector": direct,
        "two_hop_to_collector": within_two,
        "risk_tier": node["risk_tier"],
        "is_fraud_seed": bool(node["is_fraud_seed"]),
    }


# -- scoring -------------------------------------------------------------------


def score_signals(
    s: Mapping[str, Any], *, cfg: Mapping[str, Any] | None = None
) -> tuple[float, list[str]]:
    """Additive rule contributions, clipped to [0,1], with the reasons fired.

    Each rule adds its configured weight when it trips; the raw sum is clipped.
    Sizing (in feature_config.yaml) is deliberate: direct-to-collector plus the
    account's own fraud-seed flag clears BLOCK on its own.
    """
    g = _gcfg(cfg)
    w = g["weights"]
    reasons: list[str] = []
    score = 0.0
    collector = g["collector_id"]

    # 6. COMM-042 collector proximity (direct dominates 2-hop; no double count).
    if s["direct_to_collector"]:
        score += w["direct_to_collector"]
        reasons.append(f"Direct SENT transfer to WATCHLIST collector {collector} (COMM-042)")
    elif s["two_hop_to_collector"]:
        score += w["two_hop_to_collector"]
        reasons.append(f"Reaches WATCHLIST collector {collector} within 2 hops")

    # 7. Identity: fraud seed + risk tier (additive).
    if s["is_fraud_seed"]:
        score += w["is_fraud_seed"]
        reasons.append("Account is a known fraud seed (is_fraud_seed=True)")
    tier_score = g["risk_tier_scores"].get(s["risk_tier"], 0.0)
    if tier_score > 0:
        score += tier_score
        reasons.append(f"Account risk_tier={s['risk_tier']}")

    # 1. Smurfing / fan-out.
    fo = s["max_fanout_24h"]
    if fo >= g["fanout"]["smurfing_min"]:
        score += w["smurfing"]
        reasons.append(f"Smurfing: {fo} distinct recipients in a single day (>= {g['fanout']['smurfing_min']})")
    elif fo >= g["fanout"]["suspicious_min"]:
        score += w["smurfing_suspicious"]
        reasons.append(f"Elevated fan-out: {fo} distinct recipients in a single day")

    # 2. Fan-in.
    fi = s["in_senders"]
    if fi >= g["fanin"]["collector_min"]:
        score += w["fan_in_collector"]
        reasons.append(f"Collector-level fan-in: {fi} distinct senders (>= {g['fanin']['collector_min']})")
    elif fi >= g["fanin"]["elevated_min"]:
        score += w["fan_in_elevated"]
        reasons.append(f"Elevated fan-in: {fi} distinct senders")

    # 3. Structuring.
    ratio = s["structuring_txns"] / s["out_txns"] if s["out_txns"] else 0.0
    if ratio >= g["structuring"]["flag_ratio"]:
        score += w["structuring"]
        reasons.append(
            f"Structuring: {ratio:.0%} of outgoing transfers hug NRB thresholds "
            f"({s['structuring_txns']}/{s['out_txns']})"
        )

    # 5. Mule shape (node-table degrees + volume).
    received = s["total_received_npr"]
    net_ratio = abs(received - s["total_sent_npr"]) / received if received > 0 else 1.0
    if s["degree_in_90d"] >= g["mule"]["min_degree_in"] and net_ratio < g["mule"]["max_net_ratio"]:
        score += w["mule_shape"]
        reasons.append(
            f"Mule shape: degree_in={s['degree_in_90d']}, net balance ratio {net_ratio:.1%} "
            f"(< {g['mule']['max_net_ratio']:.0%}) — money passes straight through"
        )

    # 4. Layering (within-24h reciprocals), saturating at reciprocal_cap.
    if s["reciprocal_txns"] > 0:
        frac = min(1.0, s["reciprocal_txns"] / g["reciprocal_cap"])
        score += w["layering"] * frac
        reasons.append(f"Layering: {s['reciprocal_txns']} within-24h reciprocal transfer(s)")

    return _clip01(score), reasons


def decide(score: float, *, cfg: Mapping[str, Any] | None = None) -> tuple[str, str]:
    """Map a score to (decision, flag) using the config thresholds."""
    d = _gcfg(cfg)["decision"]
    if score >= d["block_at"]:
        return "BLOCK", "HIGH"
    if score >= d["otp_at"]:
        return "OTP_ONLY", "MEDIUM"
    return "ALLOW", "LOW"


def evaluate(account_id: str, session, *, cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Full Graph Agent verdict for one account (the JSON contract)."""
    cfg = cfg or load_config()
    signals = collect_signals(account_id, session, cfg=cfg)
    if signals is None:
        return {"account_id": account_id, "error": "account not found in graph"}
    score, reasons = score_signals(signals, cfg=cfg)
    decision, flag = decide(score, cfg=cfg)
    return {
        "account_id": account_id,
        "graph_score": round(score, 4),
        "flag": flag,
        "decision": decision,
        "reasons": reasons,
        "signals": signals,
    }


# -- bootstrap -----------------------------------------------------------------


def graph_counts(session) -> tuple[int, int]:
    n = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
    r = session.run("MATCH ()-[x:SENT]->() RETURN count(x) AS c").single()["c"]
    return int(n), int(r)


# -- CLI -----------------------------------------------------------------------


def _cmd_score(session, args, cfg) -> None:
    result = evaluate(args.account_id, session, cfg=cfg)
    if args.json:
        print(json.dumps(result, indent=2))
        return
    if "error" in result:
        print(f"{args.account_id}: {result['error']}")
        return
    print(f"{result['account_id']}  ->  {result['decision']}  "
          f"(score={result['graph_score']}, flag={result['flag']})")
    for reason in result["reasons"]:
        print(f"  - {reason}")
    if not result["reasons"]:
        print("  - no graph fraud signals fired")


def _cmd_scan(session, args, cfg) -> None:
    rows = session.run(
        """
        MATCH (a:Account)-[r:SENT]->(t)
        WITH a, date(r.timestamp) AS day, count(DISTINCT t) AS cpd
        WITH a, max(cpd) AS max_fanout_24h
        WHERE max_fanout_24h >= $min
        RETURN a.id AS id, max_fanout_24h
        ORDER BY max_fanout_24h DESC LIMIT $limit
        """,
        min=args.min, limit=args.limit,
    ).data()
    print(f"{len(rows)} account(s) sending to >= {args.min} distinct recipients in a single day:")
    for row in rows:
        print(f"  {row['id']}  max_fanout_24h={row['max_fanout_24h']}")


def _cmd_scan_in(session, args, cfg) -> None:
    rows = session.run(
        """
        MATCH (s)-[:SENT]->(a:Account)
        WITH a, count(DISTINCT s) AS in_senders
        WHERE in_senders >= $min
        RETURN a.id AS id, in_senders
        ORDER BY in_senders DESC LIMIT $limit
        """,
        min=args.min, limit=args.limit,
    ).data()
    print(f"{len(rows)} account(s) receiving from >= {args.min} distinct senders:")
    for row in rows:
        print(f"  {row['id']}  in_senders={row['in_senders']}")


def _cmd_demo(session, args, cfg) -> None:
    collector = _gcfg(cfg)["collector_id"]
    ring = session.run(
        "MATCH (a:Account)-[:SENT]->(c {id: $c}) WHERE a.is_fraud_seed AND a.id <> $c "
        "RETURN a.id AS id LIMIT 1",
        c=collector,
    ).single()
    clean = session.run(
        "MATCH (a:Account) WHERE a.risk_tier = 'LOW' AND a.is_fraud_seed = false "
        "AND a.degree_out < 30 RETURN a.id AS id LIMIT 1"
    ).single()
    picks = [("collector", collector)]
    if ring:
        picks.append(("ring member (fraud-seed spoke)", ring["id"]))
    if clean:
        picks.append(("clean account", clean["id"]))
    for label, aid in picks:
        result = evaluate(aid, session, cfg=cfg)
        print(f"\n=== {label}: {aid} ===")
        print(json.dumps(result, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Track B Graph Agent (Neo4j account-network fraud risk)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_score = sub.add_parser("score", help="score one account -> ALLOW/OTP_ONLY/BLOCK")
    p_score.add_argument("account_id")
    p_score.add_argument("--json", action="store_true", help="emit the full JSON contract")

    p_scan = sub.add_parser("scan", help="accounts sending to >= N distinct recipients/day")
    p_scan.add_argument("--min", type=int, default=5)
    p_scan.add_argument("--limit", type=int, default=50)

    p_scan_in = sub.add_parser("scan-in", help="accounts receiving from >= N distinct senders")
    p_scan_in.add_argument("--min", type=int, default=5)
    p_scan_in.add_argument("--limit", type=int, default=50)

    sub.add_parser("demo", help="ring member vs collector vs clean account")

    args = parser.parse_args(argv)
    cfg = load_config()

    dispatch = {
        "score": _cmd_score,
        "scan": _cmd_scan,
        "scan-in": _cmd_scan_in,
        "demo": _cmd_demo,
    }

    driver = get_driver()
    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            nodes, rels = graph_counts(session)
            if nodes == 0:
                print(
                    "Graph is empty in database "
                    f"'{NEO4J_DATABASE}'. Load it first "
                    "(scripts/load_neo4j) before scoring.",
                    file=sys.stderr,
                )
                return 2
            dispatch[args.command](session, args, cfg)
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
