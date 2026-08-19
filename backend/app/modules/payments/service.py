from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import RequestContext
from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.core.scoping import assert_clinic_scope, assert_owns_profile
from app.integrations import razorpay as razorpay_client
from app.modules.admin.repository import BillableItemRepository
from app.modules.payments.repository import PaymentRepository

# Last-resort fallback only — used when reference.billable_items has no
# active row (default or clinic-specific) for this appointment_type yet.
# Keeps booking working in an unseeded environment instead of hard-failing
# every checkout; once an admin prices an appointment_type via the Billable
# Items screen, resolve_price() finds it and this constant stops mattering
# for that type.
MOCK_APPOINTMENT_AMOUNT = 500.00


class PaymentService:
    """Real Razorpay order creation when settings.razorpay_key_id/secret are
    configured (Stage 10 — sandbox keys, provided separately); stub mode
    (synthetic order id, no gateway call) otherwise, behind the same
    interface — see app/integrations/razorpay.py. 'waived' never involves a
    gateway either way (Clinic Admin only, Master Doc Section 13.2)."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = PaymentRepository(session)

    async def create(self, *, session_id=None, order_id=None, amount: float, currency: str = "INR") -> dict:
        receipt = f"session-{session_id}" if session_id else f"order-{order_id}"
        rzp_order = razorpay_client.create_order(amount=amount, currency=currency, receipt=receipt)
        idempotency_key = f"{rzp_order['id']}"
        payment = await self.repo.create(
            session_id=session_id,
            order_id=order_id,
            amount=amount,
            currency=currency,
            idempotency_key=idempotency_key,
            razorpay_order_id=rzp_order["id"],
        )
        await emit_event(
            self.session,
            aggregate_type="payment",
            aggregate_id=payment["payment_id"],
            event_type="payment_created",
            payload={
                "payment_id": str(payment["payment_id"]),
                "amount": amount,
                "razorpay_order_id": rzp_order["id"],
            },
        )
        return payment

    async def handle_webhook(self, *, payload: bytes, signature: str, body: dict) -> dict:
        if not razorpay_client.verify_webhook_signature(payload=payload, signature=signature):
            raise BusinessRuleError("Invalid Razorpay webhook signature", code="INVALID_WEBHOOK_SIGNATURE")

        # Server-to-server call, no logged-in user — get_db() never sets
        # app.current_user_role for this request, so rls_user_role() would be
        # NULL and the UPDATE below would silently match 0 rows (FORCE RLS,
        # no policy for NULL). 'system', not 'super_admin' — this is an
        # unattended write and the RLS/audit trail should say so honestly.
        # app.current_user_id stays unset on purpose: changed_by should be
        # NULL for a system-initiated change, not attributed to a person.
        from app.core.db import text_set_local

        await self.session.execute(text_set_local("app.current_user_role", "system"))

        rzp_order_id = body.get("payload", {}).get("payment", {}).get("entity", {}).get("order_id")
        rzp_payment_id = body.get("payload", {}).get("payment", {}).get("entity", {}).get("id")
        if not rzp_order_id:
            raise BusinessRuleError("Webhook payload missing order_id", code="INVALID_WEBHOOK_PAYLOAD")

        payment = await self.repo.get_by_razorpay_order_id(rzp_order_id)
        if not payment:
            raise NotFoundError("No payment found for this Razorpay order", code="PAYMENT_NOT_FOUND")

        # Idempotent — Razorpay retries webhook delivery; a payment already
        # marked paid is a no-op, not an error (Architecture Section 14).
        if payment["status"] == "paid":
            return payment

        # Razorpay fires many event types at this same endpoint (order.paid,
        # payment.failed, payment.authorized, refund.*, ...) and all of them
        # carry an order_id — only a captured payment should ever flip
        # status to 'paid'. Anything else is acknowledged (200) but ignored.
        event = body.get("event", "")
        if event not in ("payment.captured", "order.paid"):
            return payment

        return await self.update_status(payment["payment_id"], status="paid", payment_method="upi", _razorpay_payment_id=rzp_payment_id)

    async def get(self, payment_id: UUID) -> dict:
        payment = await self.repo.get(payment_id)
        if not payment:
            raise NotFoundError("Payment not found", code="PAYMENT_NOT_FOUND")
        return payment

    async def list(self, clinic_id: UUID) -> list[dict]:
        return await self.repo.list_by_clinic(clinic_id)

    async def update_status(
        self,
        payment_id: UUID,
        *,
        status: str,
        payment_method,
        waived_by=None,
        waived_reason=None,
        _razorpay_payment_id=None,
        _changed_by: UUID | None = None,
        _changed_by_role: str = "system",
    ) -> dict:
        payment = await self.get(payment_id)

        # core.appointments is the source of truth for "is this booking
        # paid" — every caller (webhook, mock-confirm, staff PATCH) routes
        # through here, and marks the appointment first; payments and
        # treatment_sessions reflect from that, never the other way round.
        # mark_paid() is idempotent (selected -> paid only), so calling it
        # again on an already-paid appointment is a no-op, not an error.
        appt = None
        if payment.get("appointment_id") and status == "paid":
            from app.modules.scheduling.service import PatientBookingService

            appt = await PatientBookingService(self.session).mark_paid(
                payment["appointment_id"], changed_by=_changed_by, changed_by_role=_changed_by_role
            )

        updated = await self.repo.set_status(
            payment_id,
            status=status,
            payment_method=payment_method,
            waived_by=waived_by,
            waived_reason=waived_reason,
            razorpay_payment_id=_razorpay_payment_id,
        )
        await emit_event(
            self.session,
            aggregate_type="payment",
            aggregate_id=payment_id,
            event_type="payment_completed" if status == "paid" else "payment_waived" if status == "waived" else "payment_status_changed",
            payload={"payment_id": str(payment_id), "status": status},
        )

        # Propagate to the treatment_session's payment_status gate (Stage 8
        # billing rule) — direct SQL, not a full clinical-module import, to
        # avoid a circular dependency (clinical doesn't need to know about
        # payments to function, payments just needs to unlock what it gates).
        # Sourced from the appointment's session_id when this payment is
        # appointment-scoped — chk_payments_single_target guarantees the
        # payment's own session_id is NULL in that case, so reading
        # updated["session_id"] here would always miss it. Falls back to the
        # payment's own session_id for the older direct-session flow, where
        # there is no appointment in the picture at all.
        session_id = (appt or {}).get("session_id") or updated.get("session_id")
        if session_id and status in ("paid", "waived"):
            from sqlalchemy import text

            await self.session.execute(
                text("UPDATE treatment_sessions SET payment_status = :status WHERE session_id = :sid"),
                {"status": status, "sid": str(session_id)},
            )
        return updated  # type: ignore[return-value]

    # ── mock payment (dummy checkout, real appointment lifecycle) ────────────
    #
    # Same two-step shape a real gateway would have (create an order, then
    # confirm it) so this can be swapped for the real Razorpay flow later
    # without the frontend checkout screen changing shape. The only thing
    # "mock" about it is that confirm_mock_payment marks itself paid on the
    # caller's say-so instead of a signed webhook.

    async def _get_appointment_for_pay(self, appointment_id: UUID, ctx: RequestContext) -> dict:
        from app.modules.scheduling.service import AppointmentService

        appt = await AppointmentService(self.session).get(appointment_id)
        if ctx.role == "patient":
            assert_owns_profile(ctx, appt["patient_id"])
        else:
            await assert_clinic_scope(ctx, self.session, appt["clinic_id"])
        return appt

    async def create_mock_order(self, appointment_id: UUID, ctx: RequestContext) -> dict:
        appt = await self._get_appointment_for_pay(appointment_id, ctx)

        existing = await self.repo.get_for_appointment(appointment_id)
        if existing and existing["status"] in ("pending", "paid"):
            return existing

        if appt["status"] != "selected":
            raise BusinessRuleError(f"Appointment is '{appt['status']}', not awaiting payment", code="NOT_AWAITING_PAYMENT")

        priced = await BillableItemRepository(self.session).resolve_price(
            category="appointment", clinic_id=appt["clinic_id"], appointment_type=appt["appointment_type"]
        )
        amount = float(priced["price"]) if priced else MOCK_APPOINTMENT_AMOUNT
        currency = priced["currency"] if priced else "INR"

        rzp_order = razorpay_client.create_order(amount=amount, currency=currency, receipt=f"appt-{appointment_id}")
        payment = await self.repo.create(
            session_id=None,
            order_id=None,
            appointment_id=appointment_id,
            amount=amount,
            currency=currency,
            idempotency_key=rzp_order["id"],
            razorpay_order_id=rzp_order["id"],
        )
        await emit_event(
            self.session,
            aggregate_type="payment",
            aggregate_id=payment["payment_id"],
            event_type="payment_created",
            payload={
                "payment_id": str(payment["payment_id"]),
                "appointment_id": str(appointment_id),
                "amount": amount,
                "razorpay_order_id": rzp_order["id"],
            },
        )
        return payment

    async def confirm_mock_payment(self, payment_id: UUID, ctx: RequestContext) -> dict:
        from app.core.db import text_set_local

        payment = await self.get(payment_id)
        if not payment.get("appointment_id"):
            raise BusinessRuleError("Payment is not linked to an appointment", code="NOT_A_MOCK_PAYMENT")

        await self._get_appointment_for_pay(payment["appointment_id"], ctx)

        if payment["status"] == "paid":
            return payment
        if payment["status"] != "pending":
            raise BusinessRuleError(f"Payment is '{payment['status']}', cannot confirm", code="PAYMENT_NOT_PENDING")

        # The caller may be authenticated as 'patient' — rls_payments_update
        # only grants staff/system, matching every other unattended write in
        # this codebase (handle_webhook above does the identical thing).
        # Ownership was already proven by _get_appointment_for_pay above.
        await self.session.execute(text_set_local("app.current_user_role", "system"))
        return await self.update_status(
            payment_id,
            status="paid",
            payment_method="mock",
            _razorpay_payment_id=f"pay_mock_{payment_id.hex[:14]}",
            _changed_by=UUID(ctx.user_id),
            _changed_by_role=ctx.role,
        )
