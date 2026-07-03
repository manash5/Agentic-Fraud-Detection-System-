from enum import StrEnum


class EventType(StrEnum):
    TRANSACTION_CREATED = "transaction.created"
    GEO_RISK_EVALUATED = "geo.risk.evaluated"
    VELOCITY_RISK_EVALUATED = "velocity.risk.evaluated"
    BEHAVIOR_RISK_EVALUATED = "behavior.risk.evaluated"
    SYNTHESIS_RISK_COMPLETED = "synthesis.risk.completed"
    DECISION_COMPLETED = "decision.completed"
    OTP_INITIATED = "otp.initiated"
    OTP_VERIFIED = "otp.verified"
