"""Redis Stream names for inter-service event broadcast.

The research paper describes Kafka topics for event propagation. For this
implementation we deliberately use Redis Streams (XADD / XREADGROUP) as a
lighter-weight alternative suitable for single-cluster deployments.
"""

# Transaction lifecycle
STREAM_TRANSACTIONS = "stream:transactions"

# Agent evaluation requests and results
STREAM_VELOCITY_REQUESTS = "stream:velocity:requests"
STREAM_VELOCITY_RESULTS = "stream:velocity:results"

STREAM_GEO_REQUESTS = "stream:geo:requests"
STREAM_GEO_RESULTS = "stream:geo:results"

STREAM_BEHAVIOR_REQUESTS = "stream:behavior:requests"
STREAM_BEHAVIOR_RESULTS = "stream:behavior:results"

# Synthesis and decision
STREAM_SYNTHESIS_REQUESTS = "stream:synthesis:requests"
STREAM_SYNTHESIS_RESULTS = "stream:synthesis:results"

STREAM_DECISION_REQUESTS = "stream:decision:requests"
STREAM_DECISION_RESULTS = "stream:decision:results"

# Consumer group shared by all stream consumers in a service pod
DEFAULT_CONSUMER_GROUP = "fraud-detection"

# --- Legacy pub/sub channel names (deprecated, kept for migration) ---
TRANSACTIONS_CREATED = "transactions.created"
GEO_RISK_REQUESTED = "geo.risk.requested"
GEO_RISK_EVALUATED = "geo.risk.evaluated"
VELOCITY_RISK_REQUESTED = "velocity.risk.requested"
VELOCITY_RISK_EVALUATED = "velocity.risk.evaluated"
BEHAVIOR_RISK_REQUESTED = "behavior.risk.requested"
BEHAVIOR_RISK_EVALUATED = "behavior.risk.evaluated"
SYNTHESIS_RISK_COMPLETED = "synthesis.risk.completed"
