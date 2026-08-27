from datetime import date, datetime, time
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


class PaymentHistoryDetailRead(PaymentRead):
    """/payments/history — one row per payment, pre-joined with everything
    the payments-history screens need so no per-row follow-up fetch is
    required: who paid, for what, at which clinic, with which doctor, and
    the linked appointment's own status/completion time."""

    patient_id: UUID | None = None
    patient_name: str | None = None
    effective_clinic_id: UUID | None = None
    clinic_name: str | None = None
    doctor_name: str | None = None
    purpose: str | None = None
    appointment_date: date | None = None
    appointment_start_time: time | None = None
    appointment_status: str | None = None
    appointment_completed_at: datetime | None = None


class RevenueSummaryPoint(BaseModel):
    period: datetime
    total: float
    payment_count: int


class RevenueByPurposePoint(BaseModel):
    period: datetime
    purpose: str
    total: float
    payment_count: int


class PatientRevenueTotal(BaseModel):
    patient_id: UUID
    patient_name: str | None = None
    total_paid: float
    payment_count: int


class PaymentLogRead(BaseModel):
    """core.payment_logs — one row per payment *event* (order created,
    webhook received, client-verify attempt, staff status change).
    Append-only; core.payments itself only ever shows current state."""

    log_id: UUID
    # Nullable — ON DELETE SET NULL when the parent payments row is later
    # deleted (e.g. an expired hold cleaned up by app/workers/hold_sweeper.py).
    # The row survives orphaned; every other field was already snapshotted.
    payment_id: UUID | None
    status: str
    amount: float
    currency: str
    payment_method: str | None = None
    razorpay_order_id: str | None = None
    razorpay_payment_id: str | None = None
    failure_code: str | None = None
    failure_reason: str | None = None
    source: str
    gateway_event: str | None = None
    gateway_response: dict = Field(default_factory=dict)
    changed_by: UUID | None = None
    changed_by_role: str | None = None
    created_at: datetime


class PaymentLogDetailRead(PaymentLogRead):
    """/payments/logs — cross-payment listing, pre-joined the same way
    /payments/history is so a "show me failed payments" screen needs no
    per-row follow-up fetch."""

    patient_name: str | None = None
    clinic_name: str | None = None


class PaymentVerify(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
