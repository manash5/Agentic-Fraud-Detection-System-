"""Raw dataset txn_type → paper Table I TransactionType, explicit and reviewable.

The dataset (``transactions_raw.txn_type``) uses Nepali-rail values; Table I
(``shared.schemas.risk.Layer1Weights``) is keyed by four broad categories.
The mapping must be a visible, reviewable table — never an inline guess — so
it lives here and is printed at service startup via :func:`log_mapping_table`.

Rationale per row:

    ESEWA_P2P       wallet-to-wallet transfer            → p2p_transfer
    RTGS            high-value interbank transfer        → p2p_transfer
    SWIFT_OUTWARD   outward remittance (account-to-account) → p2p_transfer
    KHALTI_QR       QR merchant payment                  → merchant_payment
    CARD_POS        card present at merchant terminal    → merchant_payment
    ATM_WITHDRAWAL  cash withdrawal                      → atm_withdrawal
    UTILITY_BILL    scheduled bill payment               → bill_payment
    MOBILE_TOPUP    prepaid top-up (bill-like, recurring) → bill_payment

Unknown values fall back to :data:`DEFAULT_TXN_TYPE` **with a warning log** —
never silently. The default is P2P_TRANSFER to stay consistent with
``agents.synthesis_agent.layer1_weights``, which already falls back to the
P2P_TRANSFER row for unknown keys; one fallback, not two different ones.
"""

from __future__ import annotations

import logging

from shared.schemas.risk import TransactionType

logger = logging.getLogger("synthesis-agent")

RAW_TXN_TYPE_MAP: dict[str, TransactionType] = {
    "ESEWA_P2P": TransactionType.P2P_TRANSFER,
    "RTGS": TransactionType.P2P_TRANSFER,
    "SWIFT_OUTWARD": TransactionType.P2P_TRANSFER,
    "KHALTI_QR": TransactionType.MERCHANT_PAYMENT,
    "CARD_POS": TransactionType.MERCHANT_PAYMENT,
    "ATM_WITHDRAWAL": TransactionType.ATM_WITHDRAWAL,
    "UTILITY_BILL": TransactionType.BILL_PAYMENT,
    "MOBILE_TOPUP": TransactionType.BILL_PAYMENT,
}

# Matches layer1_weights' existing unknown-key fallback (P2P_TRANSFER row).
DEFAULT_TXN_TYPE = TransactionType.P2P_TRANSFER


def map_txn_type(raw: str) -> TransactionType:
    """Map a raw dataset txn_type to its Table I category.

    Unmapped values are logged loudly and default to :data:`DEFAULT_TXN_TYPE`.
    """
    mapped = RAW_TXN_TYPE_MAP.get(raw.strip().upper())
    if mapped is None:
        logger.warning(
            "txn_type %r has no entry in RAW_TXN_TYPE_MAP — defaulting to %s; "
            "add it to synthesis_agent/txn_type_mapping.py",
            raw, DEFAULT_TXN_TYPE.value,
        )
        return DEFAULT_TXN_TYPE
    return mapped


def log_mapping_table() -> None:
    """Print the full mapping at startup so it is reviewable in the logs."""
    lines = [
        f"    {raw:<15} -> {mapped.value}"
        for raw, mapped in RAW_TXN_TYPE_MAP.items()
    ]
    lines.append(f"    {'<unmapped>':<15} -> {DEFAULT_TXN_TYPE.value}  (logged as warning)")
    logger.info("Synthesis txn_type mapping (Table I categories):\n%s", "\n".join(lines))
