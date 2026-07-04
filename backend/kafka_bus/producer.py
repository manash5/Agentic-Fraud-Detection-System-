"""Async Kafka producer wrapper — publish one :class:`Event` to ``fraud-events``.

A thin layer over ``AIOKafkaProducer`` so the FastAPI app and the orchestrator
share the exact same publish path. The transaction_id is used as the Kafka
message key, so all events for one transaction land on the same partition and
stay strictly ordered (received → *_completed → synthesis → decision).
"""

from __future__ import annotations

import logging

from aiokafka import AIOKafkaProducer

from kafka_bus import config
from kafka_bus.events import Event

logger = logging.getLogger("kafka-bus.producer")


class EventProducer:
    def __init__(self, bootstrap_servers: str | None = None,
                 topic: str | None = None) -> None:
        self._bootstrap = bootstrap_servers or config.BOOTSTRAP_SERVERS
        self._topic = topic or config.TOPIC
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        if self._producer is None:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap,
                # Envelope is serialized by Event.to_bytes; keys are the txn id.
                key_serializer=lambda k: k.encode("utf-8") if k else None,
                enable_idempotence=True,
                acks="all",
            )
            await self._producer.start()
            logger.info("Kafka producer connected to %s (topic %s)",
                        self._bootstrap, self._topic)

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(self, event: Event) -> None:
        if self._producer is None:
            raise RuntimeError("producer not started — call start() first")
        await self._producer.send_and_wait(
            self._topic, value=event.to_bytes(), key=event.transaction_id)
        logger.debug("published %s for %s", event.event_type, event.transaction_id)
