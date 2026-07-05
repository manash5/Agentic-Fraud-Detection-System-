"""Append one record per evaluated transaction to transactions_logs.json.

The file is a JSON array (one transaction object per line, matching the
model-verdict sample). Writes are serialized with an asyncio lock so concurrent
transactions never corrupt the file. Path override: env ``TXN_LOG_PATH``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("txn-logger")

BACKEND_DIR = Path(__file__).resolve().parents[2]
LOG_PATH = Path(os.environ.get("TXN_LOG_PATH", BACKEND_DIR / "transactions_logs.json"))

_lock = asyncio.Lock()


def _read() -> list[dict[str, Any]]:
    if not LOG_PATH.exists():
        return []
    try:
        text = LOG_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.error("Could not read %s: %s", LOG_PATH, exc)
        return []
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("%s was not valid JSON — starting a fresh log", LOG_PATH)
        return []
    return data if isinstance(data, list) else []


def _write(entries: list[dict[str, Any]]) -> None:
    # One object per line inside the array (matches the provided sample).
    body = ",\n".join(json.dumps(e, ensure_ascii=False) for e in entries)
    LOG_PATH.write_text(f"[\n{body}\n]\n", encoding="utf-8")


async def append_log(entry: dict[str, Any]) -> None:
    """Append a transaction record; best-effort (never raises to the caller)."""
    async with _lock:
        try:
            entries = _read()
            entries.append(entry)
            await asyncio.to_thread(_write, entries)
            logger.info("Logged %s to %s (%d total)",
                        entry.get("txn_id"), LOG_PATH.name, len(entries))
        except Exception as exc:  # noqa: BLE001 — logging must never break the pipeline
            logger.error("Failed to append transaction log: %s", exc)
