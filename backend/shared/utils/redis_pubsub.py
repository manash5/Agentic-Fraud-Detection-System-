"""Redis Streams helpers for inter-service event broadcast.

Previously this module used Redis Pub/Sub (PUBLISH / SUBSCRIBE). The backend
now standardises on Redis Streams for at-least-once delivery and consumer
groups. The legacy ``publish_event`` wrapper is retained but delegates to
``append_stream_event``.
"""

from typing import Any

from redis import Redis

from shared.utils.serialization import to_json


def append_stream_event(
    redis_client: Redis,
    stream: str,
    payload: dict[str, Any],
    *,
    maxlen: int = 10_000,
) -> str:
    """Append a JSON-serialised event to a Redis Stream (XADD)."""
    message_id: bytes | str = redis_client.xadd(
        stream,
        {"payload": to_json(payload)},
        maxlen=maxlen,
        approximate=True,
    )
    if isinstance(message_id, bytes):
        return message_id.decode()
    return message_id


def publish_event(redis_client: Redis, channel: str, payload: dict[str, Any]) -> str:
    """Backward-compatible alias — treats *channel* as a stream name."""
    return append_stream_event(redis_client, channel, payload)


def read_stream_events(
    redis_client: Redis,
    stream: str,
    *,
    group: str,
    consumer: str,
    count: int = 10,
    block_ms: int = 2000,
) -> list[tuple[str, dict[str, Any]]]:
    """Read and acknowledge pending events from a consumer group."""
    try:
        redis_client.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception:
        pass

    entries = redis_client.xreadgroup(
        group,
        consumer,
        {stream: ">"},
        count=count,
        block=block_ms,
    )
    results: list[tuple[str, dict[str, Any]]] = []
    for _stream_name, messages in entries or []:
        for message_id, fields in messages:
            raw = fields.get(b"payload") or fields.get("payload")
            if isinstance(raw, bytes):
                raw = raw.decode()
            import json

            payload = json.loads(raw) if isinstance(raw, str) else raw
            redis_client.xack(stream, group, message_id)
            msg_id = message_id.decode() if isinstance(message_id, bytes) else message_id
            results.append((msg_id, payload))
    return results
