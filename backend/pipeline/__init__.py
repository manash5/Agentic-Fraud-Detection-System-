"""Shared pipeline plumbing: the normalized transaction, the per-agent runners
(used by BOTH the HTTP /evaluate endpoint and the Kafka orchestrator), and the
fusion helper. Keeping this here means the two entry points run the agents in
exactly the same way — there is one source of truth for "run an agent".
"""
