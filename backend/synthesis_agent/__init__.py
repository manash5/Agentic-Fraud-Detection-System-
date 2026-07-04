"""Synthesis Agent service layer — FastAPI endpoint + txn_type mapping.

The fusion math itself lives in ``agents.synthesis_agent`` (pure functions);
this package wraps it in an HTTP endpoint and the Postgres audit write.
Synthesis stays free of any Redis dependency end to end — no module in this
package may pull in the Redis client (enforced by a test).
"""
