from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PaymentCreate(BaseModel):
    """order_id is the only real input — amount/currency are always resolved
    server-side from store_orders.total_amount (never trusted from the
    client, see PaymentService.create). session_id accepted no longer:
    core.sessions is retired, nothing populates a real one."""

    order_id: UUID


class PaymentStatusUpdate(BaseModel):
    status: str = Field(pattern="^(paid|failed|waived|refunded)$")
    payment_method: str | None = Field(default=None, pattern="^(cash|card|upi|bank_transfer|waived)$")
    waived_reason: str | None = None


class PaymentRead(BaseModel):
    payment_id: UUID
    session_id: UUID | None
    order_id: UUID | None
    appointment_id: UUID | None = None
    amount: float
    currency: str
    status: str
    payment_method: str | None = None
    razorpay_order_id: str | None = None
    waived_by: UUID | None
    paid_at: datetime | None
    created_at: datetime
    base_fee_amount: float | None = None
    platform_fee_percent: float | None = None
    platform_fee_amount: float | None = None
    cancellation_refund_percent: float | None = None
    cancellation_refund_amount: float | None = None


class PaymentOrderRead(PaymentRead):
    """Response for order creation only — carries the public key the
    frontend needs to open Razorpay Checkout. Not part of PaymentRead so it
    doesn't leak into list/get/status-update responses."""

    razorpay_key_id: str | None = None


class PaymentHistoryRead(PaymentRead):
    """/me/payments — carries the appointment's type/date alongside the
    payment so the billing-history screen needs no per-row follow-up fetch."""

    appointment_type: str | None = None
    appointment_date: date | None = None


class PaymentAmountRead(BaseModel):
    appointment_id: UUID
    amount: float
    currency: str
    item_name: str
    base_fee_amount: float
    platform_fee_percent: float
    platform_fee_amount: float


class PaymentVerify(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
