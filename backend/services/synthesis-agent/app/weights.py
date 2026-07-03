"""Layer 1 (transaction-type) and Layer 2 (fraud-pattern) weight tables."""

from shared.schemas.risk import (
    AgentWeights,
    BlendedWeights,
    FraudPattern,
    Layer1Weights,
    Layer2Weights,
    TransactionType,
)

LAYER1 = Layer1Weights()
LAYER2 = Layer2Weights()

LAYER_BLEND_RATIO: float = 0.50


def get_layer1_weights(transaction_type: TransactionType) -> AgentWeights:
    mapping = {
        TransactionType.P2P_TRANSFER: LAYER1.p2p_transfer,
        TransactionType.MERCHANT_PAYMENT: LAYER1.merchant_payment,
        TransactionType.ATM_WITHDRAWAL: LAYER1.atm_withdrawal,
        TransactionType.BILL_PAYMENT: LAYER1.bill_payment,
    }
    return mapping[transaction_type]


def get_layer2_weights(fraud_pattern: FraudPattern) -> AgentWeights:
    mapping = {
        FraudPattern.RAPID_TRANSFERS: LAYER2.rapid_transfers,
        FraudPattern.FRAUD_RING: LAYER2.fraud_ring,
        FraudPattern.MONEY_LAUNDERING: LAYER2.money_laundering,
        FraudPattern.NOVEL_PATTERN: LAYER2.novel_pattern,
    }
    return mapping[fraud_pattern]


def blend_weights(layer1: AgentWeights, layer2: AgentWeights) -> BlendedWeights:
    """50/50 blend of Layer 1 and Layer 2 weight tables."""
    alpha = LAYER_BLEND_RATIO
    return BlendedWeights(
        velocity=alpha * layer1.velocity + (1 - alpha) * layer2.velocity,
        geo=alpha * layer1.geo + (1 - alpha) * layer2.geo,
        behavior=alpha * layer1.behavior + (1 - alpha) * layer2.behavior,
    )
