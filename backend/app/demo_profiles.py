"""The 3 fixed demo login profiles used by the profile-picker login screen.

Each maps a real dataset account to a synthetic banking identity plus a
"corresponding transaction" (prefill) drawn from the eval set, chosen so the
LIVE fraud pipeline reproduces its expected decision:

  ALLOW  ACC-1001433  — LOW-risk normal customer, small eSewa transfer -> PASS
  OTP    ACC-4173453  — COMM-042 ring member (graph score ~0.60) -> OTP step-up
  BLOCK  ACC-0011204  — COMM-042 collector / mule (graph score 1.0) -> BLOCK

Live decisions verified end-to-end via scripts.probe_decision (fresh velocity/
geo, not the /evaluate proxy): PASS ~0.10, OTP ~0.56, BLOCK ~0.98.

The account ids ARE the agent-space keys the pipeline scores on, so the outcome
is produced by the real agents (graph proximity to the watchlist collector),
never hardcoded.
"""

from __future__ import annotations

from typing import Any

# account_id -> profile. The prefill is the frontend TransferRequest the
# dashboard "Transfer Money" card pre-populates.
DEMO_PROFILES: list[dict[str, Any]] = [
    {
        "id": "allow",
        "customerId": "CUST-DEMO-ALLOW",
        "accountId": "ACC-1001433",
        "name": "Dikshanta Chapagain",
        "label": "Everyday Customer",
        "blurb": "Low-risk profile — a routine eSewa transfer clears instantly.",
        "expected": "PASS",
        "mobile": "9801110001",
        "balance": 248500.00,
        "sourceTxn": "TXN-20260522-052251A3",  # eval_hidden, is_fraud=False
        "prefill": {
            "destination": "global_ime",
            "recipientAccount": "1201010199887766",
            "recipientName": "Sita Gurung",
            "recipientBank": "Global IME Bank",
            "amount": 3606,
            "remarks": "Family support",
        },
    },
    {
        "id": "otp",
        "customerId": "CUST-DEMO-OTP",
        "accountId": "ACC-4173453",
        "name": "Pawan Acharya",
        "label": "Flagged Network Member",
        "blurb": "Account linked to a fraud ring — transfers need OTP step-up.",
        "expected": "OTP",
        "mobile": "9801110002",
        "balance": 312750.00,
        "sourceTxn": "TXN-20260507-15894953",  # eval_hidden, is_fraud=True (SMURFING)
        "prefill": {
            "destination": "other_bank",
            "recipientAccount": "0221010144556677",
            "recipientName": "Bikash Rai",
            "recipientBank": "Nabil Bank",
            "amount": 45000,
            "remarks": "Vendor payment",
        },
    },
    {
        "id": "block",
        "customerId": "CUST-DEMO-BLOCK",
        "accountId": "ACC-0011204",
        "name": "Pratik Joshi",
        "label": "Watchlist Collector (COMM-042)",
        "blurb": "Known mule collector account — high-value transfers are blocked.",
        "expected": "BLOCK",
        "mobile": "9801110003",
        "balance": 820000.00,
        "sourceTxn": "TXN-20260323-4DC7247F",  # eval_hidden fraud RTGS (values)
        "prefill": {
            "destination": "other_bank",
            "recipientAccount": "0331010177889900",
            "recipientName": "Offshore Holdings Pvt",
            "recipientBank": "Everest Bank",
            "amount": 467412,
            "remarks": "Consolidation transfer",
        },
    },
]

_BY_ID = {p["id"]: p for p in DEMO_PROFILES}
_BY_CUSTOMER = {p["customerId"]: p for p in DEMO_PROFILES}


def get_profile(profile_id: str) -> dict[str, Any] | None:
    return _BY_ID.get(profile_id)


def profile_for_customer(customer_id: str) -> dict[str, Any] | None:
    return _BY_CUSTOMER.get(customer_id)


def public_view(profile: dict[str, Any]) -> dict[str, Any]:
    """The subset the login screen / dashboard needs (no internals)."""
    return {
        "id": profile["id"],
        "customerId": profile["customerId"],
        "accountId": profile["accountId"],
        "name": profile["name"],
        "label": profile["label"],
        "blurb": profile["blurb"],
        "expected": profile["expected"],
        "prefill": {"fromAccountId": profile["accountId"], **profile["prefill"]},
    }
