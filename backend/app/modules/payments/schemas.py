from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PaymentCreate(BaseModel):
    session_id: UUID | None = None
    order_id: UUID | None = None
    amount: float
    currency: str = "INR"


class PaymentStatusUpdate(BaseModel):
    status: str = Field(pattern="^(paid|failed|waived|refunded)$")
    payment_method: str | None = Field(default=None, pattern="^(cash|card|upi|bank_transfer|waived)$")
    waived_reason: str | None = None


class PaymentRead(BaseModel):
    payment_id: UUID
    session_id: UUID | None
    order_id: UUID | None
    amount: float
    currency: str
    status: str
    waived_by: UUID | None
    paid_at: datetime | None
    created_at: datetime
