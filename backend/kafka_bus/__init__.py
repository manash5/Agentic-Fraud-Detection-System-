"""Apache Kafka event bus for the fraud-detection pipeline.

Kafka is the *transport* only: every stage of the pipeline publishes a typed
event to the single ``fraud-events`` topic and the orchestrator reacts to them.
The orchestrator (``kafka_bus.orchestrator``) owns the workflow — it consumes a
``transaction_received`` event, fans out to the agents in parallel, fuses the
verdicts, and publishes the ``final_decision`` back to the same topic. Agents
never talk to each other; they only ever produce their own ``*_completed``
event through the orchestrator.

Layout:

    kafka_bus/config.py        bootstrap servers, topic name, consumer group
    kafka_bus/events.py        the {event_type, transaction_id, timestamp, payload} envelope
    kafka_bus/producer.py      async AIOKafkaProducer wrapper (publish one event)
    kafka_bus/orchestrator.py  the consumer loop that coordinates the workflow
"""
