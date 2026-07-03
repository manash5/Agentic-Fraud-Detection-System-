"""Load datasets/device_fingerprints.json into Postgres `device_fingerprints`.

The Geo Agent's device-novelty signal enriches unknown devices with this
table. `risk_signal_count` is derived from the risk_signals array at load
time. Duplicate device_ids keep the first occurrence (mirrors how the CSV
loads handled duplicate txn_ids).

Run from backend/: uv run python scripts/load_device_fingerprints.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ importable

from feature_engineering.config import load_config

logger = logging.getLogger(__name__)

DATASET = Path(__file__).resolve().parents[1] / "datasets" / "device_fingerprints.json"

DDL = """
CREATE TABLE IF NOT EXISTS device_fingerprints (
    device_id                   TEXT PRIMARY KEY,
    first_seen                  TIMESTAMPTZ,
    last_seen                   TIMESTAMPTZ,
    device_type                 TEXT,
    os                          TEXT,
    app_version                 TEXT,
    locale                      TEXT,
    timezone                    TEXT,
    is_rooted_or_jailbroken     BOOLEAN,
    vpn_detected                BOOLEAN,
    tor_exit_node               BOOLEAN,
    biometric_enrolled          BOOLEAN,
    num_accounts_seen_on_device INTEGER,
    is_shared_device            BOOLEAN,
    risk_signals                TEXT[],
    risk_signal_count           INTEGER
)
"""

COLUMNS = (
    "device_id", "first_seen", "last_seen", "device_type", "os", "app_version",
    "locale", "timezone", "is_rooted_or_jailbroken", "vpn_detected",
    "tor_exit_node", "biometric_enrolled", "num_accounts_seen_on_device",
    "is_shared_device", "risk_signals", "risk_signal_count",
)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    with open(DATASET) as f:
        records = json.load(f)
    logger.info("read %s device records", len(records))

    seen: set[str] = set()
    rows = []
    for rec in records:
        if rec["device_id"] in seen:
            continue
        seen.add(rec["device_id"])
        signals = rec.get("risk_signals") or []
        rows.append(tuple(
            [rec.get(c) for c in COLUMNS[:14]] + [signals, len(signals)]
        ))
    if len(rows) < len(records):
        logger.info("dropped %s duplicate device_ids", len(records) - len(rows))

    conn = psycopg2.connect(load_config()["database"]["dsn"])
    try:
        with conn, conn.cursor() as cur:
            cur.execute(DDL)
            cur.execute("TRUNCATE device_fingerprints")
            execute_values(
                cur,
                f"INSERT INTO device_fingerprints ({', '.join(COLUMNS)}) VALUES %s",
                rows,
                page_size=5000,
            )
            cur.execute("SELECT count(*) FROM device_fingerprints")
            logger.info("device_fingerprints loaded: %s rows", cur.fetchone()[0])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
