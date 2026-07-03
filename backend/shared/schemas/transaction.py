from datetime import datetime

from pydantic import BaseModel, Field


class TransactionEvent(BaseModel):
    transaction_id: str = Field(..., description="Unique transaction identifier.")
    user_id: str
    amount: float
    currency: str = "NPR"
    timestamp: datetime
    txn_type: str | None = Field(
        default=None, description="Declared transaction type (e.g. p2p, qr, pos)."
    )
    merchant_id: str | None = None
    device_id: str | None = None
    ip_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
