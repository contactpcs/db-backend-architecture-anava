from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional


class PaymentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        session_id,
        order_id,
        amount: float,
        currency: str,
        idempotency_key: str,
        razorpay_order_id: str | None = None,
        appointment_id=None,
        base_fee_amount: float | None = None,
        platform_fee_percent: float | None = None,
        platform_fee_amount: float | None = None,
    ) -> dict:
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO payments (session_id, order_id, appointment_id, amount, currency, idempotency_key, razorpay_order_id, "
                "base_fee_amount, platform_fee_percent, platform_fee_amount) "
                "VALUES (:session_id, :order_id, :appointment_id, :amount, :currency, :idem, :rzp_order, "
                ":base_fee_amount, :platform_fee_percent, :platform_fee_amount) RETURNING *"
            ),
            {
                "session_id": str(session_id) if session_id else None,
                "order_id": str(order_id) if order_id else None,
                "appointment_id": str(appointment_id) if appointment_id else None,
                "amount": amount,
                "currency": currency,
                "idem": idempotency_key,
                "rzp_order": razorpay_order_id,
                "base_fee_amount": base_fee_amount,
                "platform_fee_percent": platform_fee_percent,
                "platform_fee_amount": platform_fee_amount,
            },
        )

    async def set_cancellation_refund(self, payment_id: UUID, *, refund_percent: float, refund_amount: float) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE payments SET cancellation_refund_percent = :pct, cancellation_refund_amount = :amt "
                "WHERE payment_id = :id RETURNING *"
            ),
            {"pct": refund_percent, "amt": refund_amount, "id": str(payment_id)},
        )

    async def get_by_razorpay_order_id(self, razorpay_order_id: str) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM payments WHERE razorpay_order_id = :id"), {"id": razorpay_order_id})

    async def get_for_appointment(self, appointment_id) -> dict | None:
        """Most recent payment against an appointment — used to resume an
        in-progress mock checkout instead of spawning a duplicate order on
        every retry, same idea as get_for_session below."""
        return await fetch_optional(
            self.session,
            text("SELECT * FROM payments WHERE appointment_id = :id ORDER BY created_at DESC LIMIT 1"),
            {"id": str(appointment_id)},
        )

    async def get(self, payment_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM payments WHERE payment_id = :id"), {"id": str(payment_id)})

    async def get_owner_profile_id(self, payment_id: UUID) -> str | None:
        """A payment has no patient_id of its own — same two-hop join as
        list_by_clinic, since it's for either a store order or an
        appointment, and both carry patient_id (already resolved to
        profiles.id at creation time, see resolve_patient_profile_id). Used
        to enforce assert_owns_profile on the single-payment read (no
        ownership check existed at all here before the eng review — see
        get_payment)."""
        row = await fetch_optional(
            self.session,
            text(
                "SELECT COALESCE(so.patient_id, appt.patient_id) AS owner_profile_id "
                "FROM payments p "
                "LEFT JOIN store_orders so ON so.order_id = p.order_id "
                "LEFT JOIN appointments appt ON appt.appointment_id = p.appointment_id "
                "WHERE p.payment_id = :id"
            ),
            {"id": str(payment_id)},
        )
        return row["owner_profile_id"] if row else None

    async def get_for_session(self, session_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM payments WHERE session_id = :id ORDER BY created_at DESC LIMIT 1"),
            {"id": str(session_id)},
        )

    async def get_for_order(self, order_id: UUID) -> dict | None:
        """Most recent payment against a store order — same reuse-existing
        pattern as get_for_appointment, so retrying 'Pay Now' doesn't spawn a
        duplicate Razorpay order."""
        return await fetch_optional(
            self.session,
            text("SELECT * FROM payments WHERE order_id = :id ORDER BY created_at DESC LIMIT 1"),
            {"id": str(order_id)},
        )

    async def get_clinic_id(self, payment_id: UUID) -> str | None:
        """Same two-hop join as list_by_clinic/get_owner_profile_id — a
        payment has no clinic_id of its own, it's for either a store order or
        an appointment, and both carry clinic_id."""
        row = await fetch_optional(
            self.session,
            text(
                "SELECT COALESCE(so.clinic_id, appt.clinic_id) AS clinic_id "
                "FROM payments p "
                "LEFT JOIN store_orders so ON so.order_id = p.order_id "
                "LEFT JOIN appointments appt ON appt.appointment_id = p.appointment_id "
                "WHERE p.payment_id = :id"
            ),
            {"id": str(payment_id)},
        )
        return row["clinic_id"] if row else None

    async def list_by_clinic(self, clinic_id: UUID) -> list[dict]:
        # payments has no clinic_id of its own — scoped via a two-hop join,
        # since a payment is for either an appointment or a store order,
        # and both of those carry clinic_id.
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT p.* FROM payments p "
                        "LEFT JOIN store_orders so ON so.order_id = p.order_id "
                        "LEFT JOIN appointments appt ON appt.appointment_id = p.appointment_id "
                        "WHERE COALESCE(so.clinic_id, appt.clinic_id) = :clinic_id "
                        "ORDER BY p.created_at DESC"
                    ),
                    {"clinic_id": str(clinic_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def list_for_patient(self, patient_id: UUID) -> list[dict]:
        # Same two-hop join as list_by_clinic, scoped to patient_id instead of
        # clinic_id — appointment_type/appointment_date come along so the
        # billing-history screen needs no per-row follow-up fetch.
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT p.*, appt.appointment_type, appt.appointment_date FROM payments p "
                        "LEFT JOIN store_orders so ON so.order_id = p.order_id "
                        "LEFT JOIN appointments appt ON appt.appointment_id = p.appointment_id "
                        "WHERE COALESCE(so.patient_id, appt.patient_id) = :patient_id "
                        "ORDER BY p.created_at DESC"
                    ),
                    {"patient_id": str(patient_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def set_status(
        self, payment_id: UUID, *, status: str, payment_method, waived_by, waived_reason, razorpay_payment_id=None
    ) -> dict | None:
        paid_at_clause = ", paid_at = NOW()" if status == "paid" else ""
        rzp_clause = ", razorpay_payment_id = COALESCE(:rzp_payment, razorpay_payment_id)"
        return await fetch_optional(
            self.session,
            text(
                f"UPDATE payments SET status = :status, payment_method = :method, waived_by = :waived_by, "
                f"waived_reason = :reason {paid_at_clause} {rzp_clause} WHERE payment_id = :id RETURNING *"
            ),
            {
                "status": status,
                "method": payment_method,
                "waived_by": str(waived_by) if waived_by else None,
                "reason": waived_reason,
                "id": str(payment_id),
                "rzp_payment": razorpay_payment_id,
            },
        )
