from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional


class PaymentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, session_id, order_id, amount: float, currency: str, idempotency_key: str, razorpay_order_id: str | None = None) -> dict:
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO payments (session_id, order_id, amount, currency, idempotency_key, razorpay_order_id) "
                "VALUES (:session_id, :order_id, :amount, :currency, :idem, :rzp_order) RETURNING *"
            ),
            {"session_id": str(session_id) if session_id else None, "order_id": str(order_id) if order_id else None,
             "amount": amount, "currency": currency, "idem": idempotency_key, "rzp_order": razorpay_order_id},
        )

    async def get_by_razorpay_order_id(self, razorpay_order_id: str) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM payments WHERE razorpay_order_id = :id"), {"id": razorpay_order_id})

    async def get(self, payment_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM payments WHERE payment_id = :id"), {"id": str(payment_id)})

    async def get_for_session(self, session_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM payments WHERE session_id = :id ORDER BY created_at DESC LIMIT 1"), {"id": str(session_id)})

    async def set_status(self, payment_id: UUID, *, status: str, payment_method, waived_by, waived_reason, razorpay_payment_id=None) -> dict | None:
        paid_at_clause = ", paid_at = NOW()" if status == "paid" else ""
        rzp_clause = ", razorpay_payment_id = COALESCE(:rzp_payment, razorpay_payment_id)"
        return await fetch_optional(
            self.session,
            text(
                f"UPDATE payments SET status = :status, payment_method = :method, waived_by = :waived_by, "
                f"waived_reason = :reason {paid_at_clause} {rzp_clause} WHERE payment_id = :id RETURNING *"
            ),
            {"status": status, "method": payment_method, "waived_by": str(waived_by) if waived_by else None,
             "reason": waived_reason, "id": str(payment_id), "rzp_payment": razorpay_payment_id},
        )
