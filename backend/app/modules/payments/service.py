from __future__ import annotations

import builtins
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import RequestContext
from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, NotFoundError, PermissionError_
from app.core.scoping import assert_clinic_scope, assert_owns_profile
from app.integrations import razorpay as razorpay_client
from app.modules.admin.repository import BillableItemRepository, CancellationPolicyRepository, ClinicRepository, PlatformFeeRepository
from app.modules.payments.repository import PaymentRepository

logger = structlog.get_logger()


def _session_type_for(appointment_type: str) -> str:
    """reference.platform_fee_config and reference.cancellation_policy_tiers
    are scoped by session_type ('appointment' | 'device_session'), not the
    finer-grained appointment_type — 'initial'/'follow_up'/'protocol_followup'
    are all doctor consultations and share one fee/tier config."""
    return "device_session" if appointment_type == "device_session" else "appointment"


_WEBHOOK_STATUS_FOR_EVENT = {
    "payment.captured": "paid",
    "order.paid": "paid",
    "payment.failed": "failed",
}


def webhook_status_for_event(event: str) -> str | None:
    """Which payments.status a Razorpay webhook event resolves to, or None
    for an event that's logged but doesn't change status (payment.authorized,
    refund.*, and anything else Razorpay might add later — an unrecognized
    event is deliberately a no-op, not a guess)."""
    return _WEBHOOK_STATUS_FOR_EVENT.get(event)


def extract_failure_info(body: dict) -> tuple[str | None, str | None]:
    """(error_code, error_description) from a Razorpay payment.failed
    webhook payload's payload.payment.entity — both None for any other
    event shape."""
    entity = body.get("payload", {}).get("payment", {}).get("entity", {})
    return entity.get("error_code"), entity.get("error_description")


def resolve_cancellation_refund_percent(tiers: list[dict], hours_until: float) -> float:
    """tiers must already be sorted highest min_hours_before first (see
    CancellationPolicyRepository.resolve_tiers). Picks the most generous
    tier whose notice-period threshold the actual notice period clears —
    e.g. tiers [(12h, 100%), (6h, 50%), (2h, 20%)] with hours_until=8 gives
    50% (clears the 6h tier, not the 12h one). Below every tier's threshold
    (or no tiers configured at all): 0% — free cancellation must be
    explicitly configured, never assumed."""
    for tier in tiers:
        if hours_until >= float(tier["min_hours_before"]):
            return float(tier["refund_percent"])
    return 0.0


_REVENUE_GROUP_BY = {"day", "week", "month", "year"}


def _history_scope(ctx: RequestContext) -> tuple[UUID | None, UUID | None]:
    """(clinic_id, region_id) for the payments-history/revenue endpoints —
    super_admin gets both None (every clinic), regional_admin gets their
    region, clinic_admin/receptionist get their own clinic. Exactly one is
    ever non-None; PaymentRepository._scope_clause relies on that."""
    if ctx.role == "regional_admin":
        return None, UUID(ctx.region_id) if ctx.region_id else None
    if ctx.role in ("clinic_admin", "receptionist"):
        return UUID(ctx.clinic_id) if ctx.clinic_id else None, None
    return None, None


async def assert_payment_clinic_scope(ctx: RequestContext, session: AsyncSession, clinic_id) -> None:
    """assert_clinic_scope only enforces clinic_admin (exact match) and
    regional_admin (region match) — it's a no-op for doctor/clinical_
    assistant/receptionist by design elsewhere in the codebase. Payments is a
    money path, so those three roles get an explicit same-clinic check here
    too, rather than silently inheriting that no-op."""
    if ctx.role in ("doctor", "clinical_assistant", "receptionist") and str(clinic_id) != ctx.clinic_id:
        raise PermissionError_("You can only manage payments for your own clinic", code="CLINIC_SCOPE_MISMATCH")
    await assert_clinic_scope(ctx, session, clinic_id)


class PaymentService:
    """Real Razorpay order creation when settings.razorpay_key_id/secret are
    configured (Stage 10 — sandbox keys, provided separately); stub mode
    (synthetic order id, no gateway call) otherwise, behind the same
    interface — see app/integrations/razorpay.py. 'waived' never involves a
    gateway either way (Clinic Admin only, Master Doc Section 13.2)."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = PaymentRepository(session)

    async def create(self, *, order_id: UUID, ctx: RequestContext) -> dict:
        """Store-order payment (device/accessory purchase). Mirrors
        create_order's appointment-payment pattern: amount is resolved
        server-side from the order's own total (already trustworthy — the
        server computed it from catalog price x quantity at order-creation
        time), never taken from the client. Scope-checked the same way, and
        idempotent against a retried 'Pay Now' click."""
        from app.modules.store.repository import StoreOrderRepository

        order = await StoreOrderRepository(self.session).get(order_id)
        if not order:
            raise NotFoundError("Store order not found", code="ORDER_NOT_FOUND")
        await assert_payment_clinic_scope(ctx, self.session, order["clinic_id"])

        existing = await self.repo.get_for_order(order_id)
        if existing and existing["status"] in ("pending", "paid"):
            return existing

        amount, currency = float(order["total_amount"]), "INR"
        receipt = f"order-{order_id}"
        rzp_order = razorpay_client.create_order(amount=amount, currency=currency, receipt=receipt)
        idempotency_key = f"{rzp_order['id']}"
        payment = await self.repo.create(
            session_id=None,
            order_id=order_id,
            amount=amount,
            currency=currency,
            idempotency_key=idempotency_key,
            razorpay_order_id=rzp_order["id"],
        )
        await self.repo.log_event(
            payment["payment_id"],
            status="pending",
            amount=amount,
            currency=currency,
            source="order_created",
            razorpay_order_id=rzp_order["id"],
            changed_by=UUID(ctx.user_id),
            changed_by_role=ctx.role,
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

        event = body.get("event", "")

        # Idempotent — Razorpay retries webhook delivery; a payment already
        # marked paid is a no-op, not an error (Architecture Section 14).
        # Still logged: a retried delivery is a real event that happened,
        # even though it changes nothing.
        if payment["status"] == "paid":
            await self.repo.log_event(
                payment["payment_id"],
                status=payment["status"],
                amount=float(payment["amount"]),
                currency=payment["currency"],
                source="razorpay_webhook",
                razorpay_payment_id=rzp_payment_id,
                gateway_event=event,
                gateway_response=body,
            )
            return payment

        # Razorpay fires many event types at this same endpoint (order.paid,
        # payment.failed, payment.authorized, refund.*, ...) and all of them
        # carry an order_id.
        new_status = webhook_status_for_event(event)
        if new_status:
            failure_code, failure_reason = extract_failure_info(body) if new_status == "failed" else (None, None)
            return await self.update_status(
                payment["payment_id"],
                status=new_status,
                payment_method="razorpay_webhook",
                _razorpay_payment_id=rzp_payment_id,
                _source="razorpay_webhook",
                _gateway_event=event,
                _gateway_response=body,
                _failure_code=failure_code,
                _failure_reason=failure_reason,
            )

        # Anything else (payment.authorized, refund.*, ...) — acknowledged
        # (200) but doesn't change payments.status; still logged so there's
        # a durable record that the event was seen and deliberately not
        # acted on, not silently dropped.
        await self.repo.log_event(
            payment["payment_id"],
            status=payment["status"],
            amount=float(payment["amount"]),
            currency=payment["currency"],
            source="razorpay_webhook",
            razorpay_payment_id=rzp_payment_id,
            gateway_event=event,
            gateway_response=body,
        )
        return payment

    async def get(self, payment_id: UUID) -> dict:
        payment = await self.repo.get(payment_id)
        if not payment:
            raise NotFoundError("Payment not found", code="PAYMENT_NOT_FOUND")
        return payment

    async def list(self, clinic_id: UUID) -> builtins.list[dict]:
        return await self.repo.list_by_clinic(clinic_id)

    async def list_mine(self, patient_id: UUID) -> builtins.list[dict]:
        return await self.repo.list_for_patient(patient_id)

    async def list_history(
        self,
        ctx: RequestContext,
        *,
        status: str | None = None,
        search: str | None = None,
        date_from=None,
        date_to=None,
        limit: int = 100,
        offset: int = 0,
    ) -> builtins.list[dict]:
        clinic_id, region_id = _history_scope(ctx)
        return await self.repo.list_history(
            clinic_id=clinic_id,
            region_id=region_id,
            status=status,
            search=search,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )

    async def revenue_summary(self, ctx: RequestContext, *, group_by: str, date_from=None, date_to=None) -> builtins.list[dict]:
        if group_by not in _REVENUE_GROUP_BY:
            raise BusinessRuleError(f"group_by must be one of {sorted(_REVENUE_GROUP_BY)}", code="INVALID_GROUP_BY")
        clinic_id, region_id = _history_scope(ctx)
        return await self.repo.revenue_summary(
            clinic_id=clinic_id, region_id=region_id, group_by=group_by, date_from=date_from, date_to=date_to
        )

    async def revenue_summary_by_purpose(self, ctx: RequestContext, *, group_by: str, date_from=None, date_to=None) -> builtins.list[dict]:
        if group_by not in _REVENUE_GROUP_BY:
            raise BusinessRuleError(f"group_by must be one of {sorted(_REVENUE_GROUP_BY)}", code="INVALID_GROUP_BY")
        clinic_id, region_id = _history_scope(ctx)
        return await self.repo.revenue_summary_by_purpose(
            clinic_id=clinic_id, region_id=region_id, group_by=group_by, date_from=date_from, date_to=date_to
        )

    async def patient_revenue_totals(self, ctx: RequestContext, *, date_from=None, date_to=None, limit: int = 20) -> builtins.list[dict]:
        clinic_id, region_id = _history_scope(ctx)
        return await self.repo.patient_revenue_totals(
            clinic_id=clinic_id, region_id=region_id, date_from=date_from, date_to=date_to, limit=limit
        )

    async def list_logs_for_payment(self, payment_id: UUID, ctx: RequestContext) -> builtins.list[dict]:
        """Full event history for one payment — same ownership/scope check
        as GET /payments/{id} (get_payment in the router), since seeing a
        payment's logs is strictly more detail about a payment you can
        already see, not a separate permission."""
        payment = await self.get(payment_id)
        if ctx.role == "patient":
            owner_profile_id = await self.repo.get_owner_profile_id(payment_id)
            assert_owns_profile(ctx, owner_profile_id)
        else:
            clinic_id = await self.repo.get_clinic_id(payment_id)
            await assert_payment_clinic_scope(ctx, self.session, clinic_id)
        return await self.repo.list_for_payment(payment["payment_id"])

    async def list_logs_by_status(
        self, ctx: RequestContext, *, status: str | None = None, date_from=None, date_to=None, limit: int = 100, offset: int = 0
    ) -> builtins.list[dict]:
        clinic_id, region_id = _history_scope(ctx)
        return await self.repo.list_by_status(
            status=status, clinic_id=clinic_id, region_id=region_id, date_from=date_from, date_to=date_to, limit=limit, offset=offset
        )

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
        _source: str = "staff_action",
        _failure_code: str | None = None,
        _failure_reason: str | None = None,
        _gateway_event: str | None = None,
        _gateway_response: dict | None = None,
    ) -> dict:
        payment = await self.get(payment_id)

        # core.appointments is the source of truth for "is this booking
        # paid" — every caller (webhook, mock-confirm, staff PATCH) routes
        # through here, and marks the appointment first; payments reflect
        # from that, never the other way round. mark_paid() is idempotent
        # (selected -> paid only), so calling it again on an already-paid
        # appointment is a no-op, not an error.
        if payment.get("appointment_id") and status in ("paid", "waived"):
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
        assert updated is not None
        await self.repo.log_event(
            payment_id,
            status=status,
            amount=float(updated["amount"]),
            currency=updated["currency"],
            source=_source,
            payment_method=payment_method,
            razorpay_order_id=updated.get("razorpay_order_id"),
            razorpay_payment_id=_razorpay_payment_id or updated.get("razorpay_payment_id"),
            failure_code=_failure_code,
            failure_reason=_failure_reason,
            gateway_event=_gateway_event,
            gateway_response=_gateway_response,
            changed_by=_changed_by,
            changed_by_role=_changed_by_role,
        )
        await emit_event(
            self.session,
            aggregate_type="payment",
            aggregate_id=payment_id,
            event_type="payment_completed" if status == "paid" else "payment_waived" if status == "waived" else "payment_status_changed",
            payload={"payment_id": str(payment_id), "status": status},
        )

        return updated  # type: ignore[return-value]

    async def record_cancellation_refund(self, appt: dict) -> None:
        """Called from scheduling's update_status right after an appointment
        is transitioned to 'cancelled'. No-op unless there's actually a paid
        payment to compute a refund for (waived/failed/pending payments have
        nothing to refund) — a booking cancelled before payment is common
        and not an error here. Stores the computed percent/amount only;
        moving real money is still a manual step (see 64_fee_breakdown_and_
        cancellation_policy.sql's header note)."""
        payment = await self.repo.get_for_appointment(appt["appointment_id"])
        if not payment or payment["status"] != "paid":
            return

        # A 'planned' row (protocol-born, never scheduled a time) can't be
        # paid in the first place, so start_time is always set here — but
        # guard anyway rather than let a None reach _hours_until.
        if appt["start_time"] is None:
            return

        from app.modules.scheduling.service import _hours_until

        hours_until = _hours_until(appt["appointment_date"], appt["start_time"])
        session_type = _session_type_for(appt["appointment_type"])
        tiers = await CancellationPolicyRepository(self.session).resolve_tiers(session_type=session_type, clinic_id=appt["clinic_id"])
        refund_percent = resolve_cancellation_refund_percent(tiers, hours_until)
        refund_amount = round(float(payment["amount"]) * refund_percent / 100, 2)
        await self.repo.set_cancellation_refund(payment["payment_id"], refund_percent=refund_percent, refund_amount=refund_amount)

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
            await assert_payment_clinic_scope(ctx, self.session, appt["clinic_id"])
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

        base_fee_amount = float(priced["price"])
        session_type = _session_type_for(appt["appointment_type"])
        fee_config = await PlatformFeeRepository(self.session).get(session_type)
        platform_fee_percent = float(fee_config["fee_percent"]) if fee_config else 0.0
        platform_fee_amount = round(base_fee_amount * platform_fee_percent / 100, 2)

        return {
            "amount": base_fee_amount + platform_fee_amount,
            "currency": priced["currency"],
            "item_name": priced["name"],
            "base_fee_amount": base_fee_amount,
            "platform_fee_percent": platform_fee_percent,
            "platform_fee_amount": platform_fee_amount,
        }

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
            base_fee_amount=priced["base_fee_amount"],
            platform_fee_percent=priced["platform_fee_percent"],
            platform_fee_amount=priced["platform_fee_amount"],
        )
        await self.repo.log_event(
            payment["payment_id"],
            status="pending",
            amount=amount,
            currency=currency,
            source="order_created",
            razorpay_order_id=rzp_order["id"],
            changed_by=UUID(ctx.user_id),
            changed_by_role=ctx.role,
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

        # A verify-failure here (mismatch or bad signature) is logged but
        # does NOT flip payments.status to 'failed' — unlike a genuine
        # gateway payment.failed webhook, this could just as easily be a
        # tampered client request as a real payment failure, and the
        # webhook is the authoritative source for that either way. The log
        # still captures it as a security-relevant event worth being able
        # to find.
        if payment["razorpay_order_id"] != razorpay_order_id:
            await self.repo.log_event(
                payment_id,
                status=payment["status"],
                amount=float(payment["amount"]),
                currency=payment["currency"],
                source="client_verify",
                razorpay_order_id=razorpay_order_id,
                razorpay_payment_id=razorpay_payment_id,
                failure_reason="Order id does not match this payment",
                changed_by=UUID(ctx.user_id),
                changed_by_role=ctx.role,
            )
            raise BusinessRuleError("Order id does not match this payment", code="ORDER_MISMATCH")
        if not razorpay_client.verify_payment_signature(
            razorpay_order_id=razorpay_order_id, razorpay_payment_id=razorpay_payment_id, razorpay_signature=razorpay_signature
        ):
            await self.repo.log_event(
                payment_id,
                status=payment["status"],
                amount=float(payment["amount"]),
                currency=payment["currency"],
                source="client_verify",
                razorpay_order_id=razorpay_order_id,
                razorpay_payment_id=razorpay_payment_id,
                failure_reason="Invalid payment signature",
                changed_by=UUID(ctx.user_id),
                changed_by_role=ctx.role,
            )
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
            _source="client_verify",
            _gateway_response={
                "razorpay_order_id": razorpay_order_id,
                "razorpay_payment_id": razorpay_payment_id,
            },
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
