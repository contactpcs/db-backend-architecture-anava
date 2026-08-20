from __future__ import annotations

import builtins
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import RequestContext
from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.core.scoping import assert_clinic_scope, assert_owns_profile
from app.integrations import razorpay as razorpay_client
from app.modules.admin.repository import BillableItemRepository, ClinicRepository
from app.modules.payments.repository import PaymentRepository

logger = structlog.get_logger()


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
            # TEMP diagnostic — remove once webhook order-id lookup is confirmed working.
            logger.warning(
                "webhook_payment_not_found",
                razorpay_event=body.get("event"),
                rzp_order_id=rzp_order_id,
                rzp_payment_id=rzp_payment_id,
                body=body,
            )
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

        return await self.update_status(
            payment["payment_id"],
            status="paid",
            payment_method="razorpay_webhook",
            _razorpay_payment_id=rzp_payment_id,
        )

    async def get(self, payment_id: UUID) -> dict:
        payment = await self.repo.get(payment_id)
        if not payment:
            raise NotFoundError("Payment not found", code="PAYMENT_NOT_FOUND")
        return payment

    async def list(self, clinic_id: UUID) -> builtins.list[dict]:
        return await self.repo.list_by_clinic(clinic_id)

    async def list_mine(self, patient_id: UUID) -> builtins.list[dict]:
        return await self.repo.list_for_patient(patient_id)

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
        # through here, and marks the appointment first; payments reflect
        # from that, never the other way round. mark_paid() is idempotent
        # (selected -> paid only), so calling it again on an already-paid
        # appointment is a no-op, not an error.
        if payment.get("appointment_id") and status == "paid":
            from app.modules.scheduling.service import PatientBookingService

            await PatientBookingService(self.session).mark_paid(
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

        return updated  # type: ignore[return-value]

    # ── mock payment (dummy checkout, real appointment lifecycle) ────────────
    #
    # Real order creation against a real (or, absent keys, stubbed —
    # integrations/razorpay.py handles that) gateway. No self-confirm exists
    # anywhere in this module: 'paid' is only ever reached through the
    # signed webhook (handle_webhook above) or an explicit staff PATCH.

    async def _get_appointment_for_pay(self, appointment_id: UUID, ctx: RequestContext) -> dict:
        from app.modules.scheduling.service import AppointmentService

        appt = await AppointmentService(self.session).get(appointment_id)
        if ctx.role == "patient":
            assert_owns_profile(ctx, appt["patient_id"])
        else:
            await assert_clinic_scope(ctx, self.session, appt["clinic_id"])
        return appt

    async def _resolve_amount(self, appt: dict) -> dict:
        """billable_items keys an 'appointment' row by appointment_type, but
        a 'device_session' row by device_id instead (chk_billable_items_
        category_shape) — this always queried the appointment_type key,
        which a device_session's row (appointment_type IS NULL) can never
        match, so a real per-device price never resolved regardless of
        whether one was set. Same clinic_device -> catalog device_id lookup
        DeviceCapacityService._resolve_duration already uses for duration."""
        if appt["appointment_type"] == "device_session":
            from app.modules.scheduling.repository import ClinicDeviceRepository

            clinic_device = await ClinicDeviceRepository(self.session).get(appt["clinic_device_id"])
            priced = await BillableItemRepository(self.session).resolve_price(
                category="device_session",
                clinic_id=appt["clinic_id"],
                device_id=clinic_device["device_id"] if clinic_device else None,
            )
        else:
            priced = await BillableItemRepository(self.session).resolve_price(
                category="appointment", clinic_id=appt["clinic_id"], appointment_type=appt["appointment_type"]
            )
        if not priced:
            raise BusinessRuleError(f"No price configured for appointment_type '{appt['appointment_type']}'", code="PRICE_NOT_CONFIGURED")
        return {"amount": float(priced["price"]), "currency": priced["currency"], "item_name": priced["name"]}

    async def get_payment_amount(self, appointment_id: UUID, ctx: RequestContext) -> dict:
        """Step 2 — amount for display, before any order/payment row exists."""
        appt = await self._get_appointment_for_pay(appointment_id, ctx)
        priced = await self._resolve_amount(appt)
        return {"appointment_id": appointment_id, **priced}

    async def create_order(self, appointment_id: UUID, ctx: RequestContext) -> dict:
        """Step 3 — 'Pay Now'. Creates a real Razorpay order tied to the
        billable_items price; the webhook (step 5-6) is the only thing that
        can ever mark it paid."""
        appt = await self._get_appointment_for_pay(appointment_id, ctx)

        existing = await self.repo.get_for_appointment(appointment_id)
        if existing and existing["status"] in ("pending", "paid"):
            return {**existing, "razorpay_key_id": razorpay_client.settings.razorpay_key_id}

        if appt["status"] != "selected":
            raise BusinessRuleError(f"Appointment is '{appt['status']}', not awaiting payment", code="NOT_AWAITING_PAYMENT")

        priced = await self._resolve_amount(appt)
        amount, currency = priced["amount"], priced["currency"]

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
        return {**payment, "razorpay_key_id": razorpay_client.settings.razorpay_key_id}

    async def verify_payment(
        self, payment_id: UUID, *, razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str, ctx: RequestContext
    ) -> dict:
        """The client-callback confirmation path — Checkout hands the
        browser these three fields on success. Same trust level as the
        webhook (a cryptographic signature, not the caller's say-so), just a
        different secret (key_secret, not webhook_secret). Idempotent and
        safe to race against handle_webhook — whichever lands first marks it
        paid, the other is then a no-op via the status=='paid' check below."""
        payment = await self.get(payment_id)
        if not payment.get("appointment_id"):
            raise BusinessRuleError("Payment is not linked to an appointment", code="NOT_APPOINTMENT_PAYMENT")

        await self._get_appointment_for_pay(payment["appointment_id"], ctx)

        if payment["status"] == "paid":
            return payment
        if payment["razorpay_order_id"] != razorpay_order_id:
            raise BusinessRuleError("Order id does not match this payment", code="ORDER_MISMATCH")
        if not razorpay_client.verify_payment_signature(
            razorpay_order_id=razorpay_order_id, razorpay_payment_id=razorpay_payment_id, razorpay_signature=razorpay_signature
        ):
            raise BusinessRuleError("Invalid payment signature", code="INVALID_PAYMENT_SIGNATURE")

        # Same unattended-write pattern as handle_webhook and the old
        # mock-confirm — rls_payments_update only grants staff/system, and
        # ownership was already proven by _get_appointment_for_pay above.
        from app.core.db import text_set_local

        await self.session.execute(text_set_local("app.current_user_role", "system"))
        return await self.update_status(
            payment_id,
            status="paid",
            payment_method="razorpay_checkout",
            _razorpay_payment_id=razorpay_payment_id,
            _changed_by=UUID(ctx.user_id),
            _changed_by_role=ctx.role,
        )

    async def get_receipt_pdf(self, appointment_id: UUID, ctx: RequestContext) -> bytes:
        appt = await self._get_appointment_for_pay(appointment_id, ctx)

        payment = await self.repo.get_for_appointment(appointment_id)
        if not payment or payment["status"] not in ("paid", "waived", "refunded"):
            raise BusinessRuleError("Receipt not available until payment is completed", code="RECEIPT_NOT_AVAILABLE")

        priced = await BillableItemRepository(self.session).resolve_price(
            category="appointment", clinic_id=appt["clinic_id"], appointment_type=appt["appointment_type"]
        )
        # Falls back to the raw appointment_type if the billable_items row
        # priced at checkout time has since been deactivated/reworded — the
        # receipt should still render, just with a plainer description.
        item_name = priced["name"] if priced else str(appt["appointment_type"]).replace("_", " ").title()

        clinic = await ClinicRepository(self.session).get(appt["clinic_id"])

        from app.modules.payments.receipt import build_receipt_pdf

        return build_receipt_pdf(payment=payment, appointment=appt, clinic=clinic or {}, item_name=item_name)
