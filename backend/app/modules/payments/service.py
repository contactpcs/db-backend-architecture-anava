from __future__ import annotations

import builtins
import json
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import RequestContext, as_system
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


_PAISA = Decimal("0.01")


def money(value) -> Decimal:
    """Exact rupee amount, rounded half-up to the paisa (audit L6: float
    arithmetic replaced). str() first so a float's binary noise never leaks in."""
    return Decimal(str(value)).quantize(_PAISA, rounding=ROUND_HALF_UP)


def amount_matches(payment: dict, entity: dict) -> bool:
    """Does what Razorpay captured equal what we charged (audit M2)? Razorpay
    reports paise. An entity without an amount (some event shapes) is not
    treated as a mismatch."""
    if entity.get("amount") is None:
        return True
    expected = int(money(payment["amount"]) * 100)
    same_currency = (entity.get("currency") or payment["currency"]) == payment["currency"]
    return int(entity["amount"]) == expected and same_currency


# Manual status changes staff may make (audit M5). Anything else is refused —
# e.g. turning a refunded payment back into paid.
_MANUAL_TRANSITIONS = {
    "pending": {"paid", "failed", "waived"},
    "failed": {"paid", "waived"},
    "paid": {"refunded"},
    "partially_refunded": {"refunded"},
}

# Sources that mean "the gateway says this money was captured".
_GATEWAY_CONFIRMED = ("razorpay_webhook", "client_verify", "system")


def razorpay_receipt(prefix: str, entity_id) -> str:
    """Razorpay caps receipt ids at 40 characters (audit L5)."""
    return f"{prefix}-{UUID(str(entity_id)).hex}"


def refunded_status_for(amount: float, refund_amount: float) -> str:
    """payments.status once a refund has actually been returned: refunded
    when the whole amount went back, partially_refunded otherwise (118)."""
    return "refunded" if float(refund_amount) >= float(amount) else "partially_refunded"


_REVENUE_GROUP_BY = {"day", "week", "month", "year"}

# Which breakdown a role may ask for. Region only makes sense cross-region
# (super_admin); clinic only when more than one clinic is in scope.
_BREAKDOWN_DIMENSIONS_BY_ROLE = {
    "super_admin": {"region", "clinic", "doctor", "purpose"},
    "regional_admin": {"clinic", "doctor", "purpose"},
    "clinic_admin": {"doctor", "purpose"},
    "receptionist": {"doctor", "purpose"},
}


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

        amount, currency = money(order["total_amount"]), "INR"
        receipt = razorpay_receipt("order", order_id)
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
                "amount": float(amount),
                "razorpay_order_id": rzp_order["id"],
            },
        )
        return payment

    async def handle_webhook(self, *, payload: bytes, signature: str) -> dict:
        # Signature before anything else touches the body (audit L1): an
        # unsigned or malformed request is refused, never parsed.
        if not razorpay_client.verify_webhook_signature(payload=payload, signature=signature):
            raise BusinessRuleError("Invalid Razorpay webhook signature", code="INVALID_WEBHOOK_SIGNATURE")
        try:
            body = json.loads(payload or b"{}")
        except ValueError as exc:
            raise BusinessRuleError("Webhook body is not valid JSON", code="INVALID_WEBHOOK_PAYLOAD") from exc

        # Server-to-server call, no logged-in user — get_db() never sets
        # app.current_user_role for this request, so rls_user_role() would be
        # NULL and the UPDATE below would silently match 0 rows (FORCE RLS,
        # no policy for NULL). 'system', not 'super_admin' — this is an
        # unattended write and the RLS/audit trail should say so honestly.
        # app.current_user_id stays unset on purpose: changed_by should be
        # NULL for a system-initiated change, not attributed to a person.
        from app.core.db import text_set_local

        await self.session.execute(text_set_local("app.current_user_role", "system"))

        event = body.get("event", "")
        if event.startswith("refund."):
            return await self._handle_refund_webhook(event, body)

        rzp_order_id = body.get("payload", {}).get("payment", {}).get("entity", {}).get("order_id")
        rzp_payment_id = body.get("payload", {}).get("payment", {}).get("entity", {}).get("id")
        if not rzp_order_id:
            raise BusinessRuleError("Webhook payload missing order_id", code="INVALID_WEBHOOK_PAYLOAD")

        payment = await self.repo.get_by_razorpay_order_id(rzp_order_id)
        if not payment:
            # Ids only — the body carries the payer's email, phone and UPI id (audit M3).
            logger.warning(
                "webhook_payment_not_found",
                razorpay_event=body.get("event"),
                rzp_order_id=rzp_order_id,
                rzp_payment_id=rzp_payment_id,
            )
            raise NotFoundError("No payment found for this Razorpay order", code="PAYMENT_NOT_FOUND")

        # Idempotent — Razorpay retries webhook delivery; a payment already
        # marked paid is a no-op, not an error (Architecture Section 14).
        # Still logged: a retried delivery is a real event that happened,
        # even though it changes nothing.
        if payment["status"] in ("paid", "refunded", "partially_refunded"):
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
        entity = body.get("payload", {}).get("payment", {}).get("entity", {})
        if new_status == "paid" and not amount_matches(payment, entity):
            return await self._log_amount_mismatch(payment, entity, source="razorpay_webhook", gateway_event=event, gateway_response=body)
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

    async def _handle_refund_webhook(self, event: str, body: dict) -> dict:
        """refund.processed / refund.failed (and refund.created, logged only).
        The refund worker reaches the same end state by polling; whichever
        arrives first wins, the other finds refund_status no longer pending."""
        refund = body.get("payload", {}).get("refund", {}).get("entity", {})
        payment = await self.repo.get_by_razorpay_payment_id(refund.get("payment_id") or "")
        if not payment:
            raise NotFoundError("No payment found for this Razorpay refund", code="PAYMENT_NOT_FOUND")
        outcome = {"refund.processed": "processed", "refund.failed": "failed"}.get(event)
        if outcome and payment.get("refund_status") == "pending":
            return await self.apply_refund_result(
                payment,
                razorpay_refund_id=refund.get("id"),
                refund_status=outcome,
                source="razorpay_webhook",
                gateway_event=event,
                gateway_response=body,
            )
        await self.repo.log_event(
            payment["payment_id"],
            status=payment["status"],
            amount=float(payment["amount"]),
            currency=payment["currency"],
            source="razorpay_webhook",
            razorpay_payment_id=payment.get("razorpay_payment_id"),
            gateway_event=event,
            gateway_response=body,
        )
        return payment

    async def apply_refund_result(
        self,
        payment: dict,
        *,
        razorpay_refund_id: str | None,
        refund_status: str,
        source: str,
        gateway_event: str | None = None,
        gateway_response: dict | None = None,
        failure_reason: str | None = None,
        changed_by: UUID | None = None,
        changed_by_role: str = "system",
    ) -> dict:
        """Shared by the refund worker, the refund webhooks and the front-desk
        cash confirmation. processed -> status refunded / partially_refunded;
        pending -> refund id stored, still waiting; failed -> left for staff."""
        new_status = None
        if refund_status == "processed":
            new_status = refunded_status_for(payment["amount"], payment["cancellation_refund_amount"] or payment["amount"])
        updated = await self.repo.set_refund_state(
            payment["payment_id"], refund_status=refund_status, status=new_status, razorpay_refund_id=razorpay_refund_id
        )
        assert updated is not None
        await self.repo.log_event(
            payment["payment_id"],
            status=updated["status"],
            amount=float(updated["amount"]),
            currency=updated["currency"],
            source=source,
            payment_method=updated.get("payment_method"),
            razorpay_order_id=updated.get("razorpay_order_id"),
            razorpay_payment_id=updated.get("razorpay_payment_id"),
            failure_reason=failure_reason,
            gateway_event=gateway_event,
            gateway_response=gateway_response or ({"razorpay_refund_id": razorpay_refund_id} if razorpay_refund_id else None),
            changed_by=changed_by,
            changed_by_role=changed_by_role,
        )
        if refund_status in ("processed", "failed"):
            await emit_event(
                self.session,
                aggregate_type="payment",
                aggregate_id=payment["payment_id"],
                event_type="payment_refund_processed" if refund_status == "processed" else "payment_refund_failed",
                payload={
                    "payment_id": str(payment["payment_id"]),
                    "appointment_id": str(updated["appointment_id"]) if updated.get("appointment_id") else None,
                    "refund_amount": float(updated["cancellation_refund_amount"] or 0),
                    "currency": updated["currency"],
                },
            )
        return updated

    async def get(self, payment_id: UUID) -> dict:
        payment = await self.repo.get(payment_id)
        if not payment:
            raise NotFoundError("Payment not found", code="PAYMENT_NOT_FOUND")
        return payment

    async def list(self, clinic_id: UUID) -> builtins.list[dict]:
        return await self.repo.list_by_clinic(clinic_id)

    async def summary(self, clinic_id: UUID | None, *, region_id: UUID | None = None) -> dict:
        return await self.repo.summary_by_clinic(clinic_id, region_id=region_id)

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
        refund_status: builtins.list[str] | None = None,
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
            refund_status=refund_status,
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

    async def revenue_breakdown(self, ctx: RequestContext, *, dimension: str, date_from=None, date_to=None) -> builtins.list[dict]:
        allowed = _BREAKDOWN_DIMENSIONS_BY_ROLE.get(ctx.role, set())
        if dimension not in allowed:
            raise BusinessRuleError(f"dimension must be one of {sorted(allowed)} for your role", code="INVALID_BREAKDOWN_DIMENSION")
        clinic_id, region_id = _history_scope(ctx)
        return await self.repo.revenue_breakdown(
            clinic_id=clinic_id, region_id=region_id, dimension=dimension, date_from=date_from, date_to=date_to
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

        # Front desk confirming a refund that the cancellation opened (cash, or
        # an online refund settled by hand): same end state as the worker.
        if status == "refunded" and payment.get("refund_status") in ("pending", "failed"):
            if payment["refund_status"] == "pending" and payment.get("razorpay_payment_id"):
                # The refund worker is already returning this through Razorpay;
                # settling it by hand as well would pay the patient twice.
                raise BusinessRuleError(
                    "This refund is being returned through Razorpay automatically - nothing to hand back",
                    code="REFUND_IN_PROGRESS",
                )
            return await self.apply_refund_result(
                payment,
                razorpay_refund_id=None,
                refund_status="processed",
                source=_source,
                changed_by=_changed_by,
                changed_by_role=_changed_by_role,
            )

        if _source == "staff_action":
            if status not in _MANUAL_TRANSITIONS.get(payment["status"], set()):
                raise BusinessRuleError(
                    f"A '{payment['status']}' payment cannot be changed to '{status}'", code="INVALID_PAYMENT_TRANSITION"
                )
            if status == "refunded":
                # A full refund handed back by hand: record the amount and the
                # timeline so revenue, the tracker and receipts all agree.
                await self.repo.set_cancellation_refund(payment_id, refund_percent=100.0, refund_amount=float(payment["amount"]))
                return await self.apply_refund_result(
                    await self.get(payment_id),
                    razorpay_refund_id=None,
                    refund_status="processed",
                    source=_source,
                    changed_by=_changed_by,
                    changed_by_role=_changed_by_role,
                )

        # Money the gateway captured for a booking that can no longer take it
        # (cancelled, hold expired, rescheduled, or already paid by another
        # order). Rejecting it used to roll everything back and leave the
        # customer charged with no record at all; it is now recorded and
        # refunded in full by the refund worker. Gateway-confirmed only — a
        # staff PATCH to 'paid' on such a booking still fails loudly.
        if (
            status == "paid"
            and payment.get("appointment_id")
            and _source in _GATEWAY_CONFIRMED
            and await self._capture_is_orphaned(payment)
        ):
            return await self._refund_orphaned_capture(
                payment,
                payment_method=payment_method,
                razorpay_payment_id=_razorpay_payment_id,
                source=_source,
                gateway_event=_gateway_event,
                gateway_response=_gateway_response,
                changed_by=_changed_by,
                changed_by_role=_changed_by_role,
            )

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

    async def _capture_is_orphaned(self, payment: dict) -> bool:
        from app.modules.scheduling.repository import AppointmentRepository

        # The fee was waived on this very order: money arriving on it later
        # was never owed (audit H3).
        if payment.get("status") == "waived":
            return True
        appt = await AppointmentRepository(self.session).get(payment["appointment_id"])
        if appt is None:
            return True
        if appt["status"] == "selected":
            return False
        if appt["status"] != "paid":
            return True
        return await self.repo.other_paid_for_appointment(payment["appointment_id"], payment["payment_id"])

    async def _refund_orphaned_capture(
        self,
        payment: dict,
        *,
        payment_method,
        razorpay_payment_id,
        source: str,
        gateway_event,
        gateway_response,
        changed_by,
        changed_by_role: str,
    ) -> dict:
        updated = await self.repo.mark_orphaned_capture(
            payment["payment_id"], razorpay_payment_id=razorpay_payment_id, payment_method=payment_method
        )
        assert updated is not None
        await self.repo.log_event(
            payment["payment_id"],
            status=updated["status"],
            amount=float(updated["amount"]),
            currency=updated["currency"],
            source=source,
            payment_method=updated.get("payment_method"),
            razorpay_order_id=updated.get("razorpay_order_id"),
            razorpay_payment_id=updated.get("razorpay_payment_id"),
            failure_reason="Captured after the booking was cancelled, expired or already paid - full refund queued",
            gateway_event=gateway_event,
            gateway_response=gateway_response,
            changed_by=changed_by,
            changed_by_role=changed_by_role,
        )
        logger.warning(
            "orphaned_capture_refund_queued", payment_id=str(payment["payment_id"]), appointment_id=str(payment["appointment_id"])
        )
        return updated

    async def _close_open_order(self, payment: dict, reason: str) -> None:
        """Marks an open (pending) checkout failed so nothing can confirm it
        any more. If the patient still completes it at Razorpay, the capture
        lands on a failed row whose booking no longer needs it and is
        refunded automatically (_capture_is_orphaned)."""
        async with as_system(self.session):
            await self.repo.set_status(payment["payment_id"], status="failed", payment_method=None, waived_by=None, waived_reason=None)
        await self.repo.log_event(
            payment["payment_id"],
            status="failed",
            amount=float(payment["amount"]),
            currency=payment["currency"],
            source="system",
            razorpay_order_id=payment.get("razorpay_order_id"),
            failure_reason=reason,
            changed_by_role="system",
        )

    async def _log_amount_mismatch(self, payment: dict, entity: dict, *, source: str, gateway_event, gateway_response) -> dict:
        reason = (
            f"Amount mismatch: captured {entity.get('amount')} paise {entity.get('currency')}, "
            f"expected {money(payment['amount'])} {payment['currency']}"
        )
        await self.repo.log_event(
            payment["payment_id"],
            status=payment["status"],
            amount=float(payment["amount"]),
            currency=payment["currency"],
            source=source,
            razorpay_payment_id=entity.get("id"),
            failure_reason=reason,
            gateway_event=gateway_event,
            gateway_response=gateway_response,
        )
        logger.error("payment_amount_mismatch", payment_id=str(payment["payment_id"]), captured=entity.get("amount"))
        return payment

    async def reconcile(self, payment: dict) -> str:
        """Asks Razorpay whether an order we never heard back about was paid
        (audit H1). A captured attempt goes through the normal confirmation:
        the booking is confirmed if it still awaits payment, otherwise the
        money is refunded automatically. Returns what happened."""
        attempts = razorpay_client.order_payments(payment["razorpay_order_id"])
        captured = next((a for a in attempts if a.get("status") == "captured"), None)
        if captured is None:
            return "not_captured"
        if not amount_matches(payment, captured):
            await self._log_amount_mismatch(payment, captured, source="system", gateway_event="reconciliation", gateway_response=captured)
            return "amount_mismatch"
        updated = await self.update_status(
            payment["payment_id"],
            status="paid",
            payment_method="razorpay_reconciled",
            _razorpay_payment_id=captured.get("id"),
            _source="system",
            _gateway_event="reconciliation",
            _gateway_response=captured,
        )
        return "confirmed" if updated["status"] == "paid" else "refund_queued"

    async def sync_with_gateway(self, payment_id: UUID, ctx: RequestContext) -> dict:
        """Patient-triggered reconcile. When Checkout ends without handing the app its signed result (the Android
        WebView case), the app asks here instead of waiting for the webhook or the 2-minute reconciler: Razorpay is
        asked about this one order and, if captured, the normal confirmation runs. Nothing is trusted from the
        caller; a payment that is not pending, or has no order, is returned as it is."""
        payment = await self.get(payment_id)
        if payment["status"] != "pending" or not payment.get("razorpay_order_id") or not razorpay_client.is_configured():
            return payment
        if payment.get("appointment_id"):
            await self._get_appointment_for_pay(payment["appointment_id"], ctx)
        try:
            async with as_system(self.session):
                await self.reconcile(payment)
        except Exception as exc:  # noqa: BLE001 - gateway hiccup: the webhook / reconciler still settle it
            logger.warning("payment_sync_error", payment_id=str(payment_id), error=str(exc)[:200])
        return await self.get(payment_id)

    async def record_cancellation_refund(self, appt: dict) -> None:
        """Called from scheduling's update_status right after an appointment
        is transitioned to 'cancelled'. No-op unless there's actually a paid
        payment to compute a refund for (waived/failed/pending payments have
        nothing to refund) — a booking cancelled before payment is common
        and not an error here. Stores the computed percent/amount only;
        moving real money is still a manual step (see 64_fee_breakdown_and_
        cancellation_policy.sql's header note)."""
        payment = await self.repo.get_for_appointment(appt["appointment_id"])
        if payment and payment["status"] == "pending":
            # Cancelled before paying: close the open checkout the same way
            # the hold sweeper does. If the patient still completes it, the
            # capture is refunded automatically (_refund_orphaned_capture).
            async with as_system(self.session):
                await self.repo.set_status(payment["payment_id"], status="failed", payment_method=None, waived_by=None, waived_reason=None)
            await self.repo.log_event(
                payment["payment_id"],
                status="failed",
                amount=float(payment["amount"]),
                currency=payment["currency"],
                source="system",
                razorpay_order_id=payment.get("razorpay_order_id"),
                failure_reason="Booking cancelled before payment completed",
                changed_by_role="system",
            )
            return
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
        refund_amount = float(money(money(payment["amount"]) * Decimal(str(refund_percent)) / 100))
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

        base_fee_amount = money(priced["price"])
        session_type = _session_type_for(appt["appointment_type"])
        fee_config = await PlatformFeeRepository(self.session).get(session_type)
        platform_fee_percent = Decimal(str(fee_config["fee_percent"])) if fee_config else Decimal("0")
        platform_fee_amount = money(base_fee_amount * platform_fee_percent / 100)

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
        if existing and existing["status"] == "paid":
            return {**existing, "razorpay_key_id": razorpay_client.settings.razorpay_key_id, "hold_expires_at": appt["hold_expires_at"]}

        if appt["status"] != "selected":
            raise BusinessRuleError(f"Appointment is '{appt['status']}', not awaiting payment", code="NOT_AWAITING_PAYMENT")

        priced = await self._resolve_amount(appt)
        amount, currency = priced["amount"], priced["currency"]

        if existing and existing["status"] == "pending":
            if money(existing["amount"]) == amount and existing["currency"] == currency:
                return {**existing, "razorpay_key_id": razorpay_client.settings.razorpay_key_id, "hold_expires_at": appt["hold_expires_at"]}
            # The price changed since this checkout was opened (audit L4).
            await self._close_open_order(existing, "Price changed - replaced by a new order")

        rzp_order = razorpay_client.create_order(amount=amount, currency=currency, receipt=razorpay_receipt("appt", appointment_id))
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
                "amount": float(amount),
                "razorpay_order_id": rzp_order["id"],
            },
        )
        return {**payment, "razorpay_key_id": razorpay_client.settings.razorpay_key_id, "hold_expires_at": appt["hold_expires_at"]}

    async def record_cash_payment(self, appointment_id: UUID, ctx: RequestContext) -> dict:
        """Front-desk cash collection — the counter equivalent of a completed
        Razorpay checkout. Same price resolution as create_order, same
        update_status path as the webhook/verify (appointment selected ->
        paid first, then the payment row, payment_log and payment_completed
        event), just payment_method='cash' and no gateway involved.

        Idempotent: an already-paid appointment returns its payment as-is. If
        an online attempt was started first (a 'pending' Razorpay-order row
        exists) and the patient then pays cash instead, that same row is
        settled as cash rather than creating a second payment."""
        appt = await self._get_appointment_for_pay(appointment_id, ctx)

        existing = await self.repo.get_for_appointment(appointment_id)
        if existing and existing["status"] == "paid":
            return existing

        if appt["status"] != "selected":
            raise BusinessRuleError(f"Appointment is '{appt['status']}', not awaiting payment", code="NOT_AWAITING_PAYMENT")

        if existing and existing["status"] == "pending" and not existing.get("razorpay_order_id"):
            payment_id = existing["payment_id"]
        else:
            if existing and existing["status"] == "pending":
                # An online checkout is still open at Razorpay. Close it so a
                # later online payment is refunded instead of swallowed (audit H3).
                await self._close_open_order(existing, "Closed - paid in cash at the counter")
            priced = await self._resolve_amount(appt)
            payment = await self.repo.create(
                session_id=None,
                order_id=None,
                appointment_id=appointment_id,
                amount=priced["amount"],
                currency=priced["currency"],
                # No gateway order to key on — one cash row per appointment
                # attempt, unique so a double-click can't insert twice.
                idempotency_key=f"cash-{appointment_id}",
                razorpay_order_id=None,
                base_fee_amount=priced["base_fee_amount"],
                platform_fee_percent=priced["platform_fee_percent"],
                platform_fee_amount=priced["platform_fee_amount"],
            )
            payment_id = payment["payment_id"]
            await self.repo.log_event(
                payment_id,
                status="pending",
                amount=priced["amount"],
                currency=priced["currency"],
                source="order_created",
                payment_method="cash",
                changed_by=UUID(ctx.user_id),
                changed_by_role=ctx.role,
            )

        # as_system: rls_payments_update admits only super_admin/clinic_admin/
        # system, so a receptionist's (or regional_admin's) UPDATE matched 0
        # rows and update_status asserted. The route's role list and
        # _get_appointment_for_pay's clinic scope already authorized this.
        async with as_system(self.session):
            return await self.update_status(
                payment_id,
                status="paid",
                payment_method="cash",
                _changed_by=UUID(ctx.user_id),
                _changed_by_role=ctx.role,
                _source="staff_action",
            )

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

        if payment["status"] in ("paid", "refunded", "partially_refunded"):
            return payment

        # A verify-failure here (mismatch or bad signature) is logged but
        # does NOT flip payments.status to 'failed' — unlike a genuine
        # gateway payment.failed webhook, this could just as easily be a
        # tampered client request as a real payment failure, and the
        # webhook is the authoritative source for that either way. The log
        # still captures it as a security-relevant event worth being able
        # to find.
        if payment["razorpay_order_id"] != razorpay_order_id:
            await self.repo.log_event_detached(
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
            await self.repo.log_event_detached(
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
        if not payment or payment["status"] not in ("paid", "waived", "refunded", "partially_refunded"):
            raise BusinessRuleError("Receipt not available until payment is completed", code="RECEIPT_NOT_AVAILABLE")

        # Same price lookup as checkout (device sessions are priced by
        # device, consultations by type). Falls back to the plain type name if
        # the price row has since been removed — the receipt still renders.
        try:
            item_name = (await self._resolve_amount(appt))["item_name"]
        except BusinessRuleError:
            item_name = str(appt["appointment_type"]).replace("_", " ").title()

        clinic = await ClinicRepository(self.session).get(appt["clinic_id"])

        from app.modules.payments.receipt import build_receipt_pdf

        return build_receipt_pdf(payment=payment, appointment=appt, clinic=clinic or {}, item_name=item_name)
