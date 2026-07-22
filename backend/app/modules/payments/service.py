from __future__ import annotations

import uuid
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.integrations import razorpay as razorpay_client
from app.modules.payments.repository import PaymentRepository


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
            session_id=session_id, order_id=order_id, amount=amount, currency=currency,
            idempotency_key=idempotency_key, razorpay_order_id=rzp_order["id"],
        )
        await emit_event(
            self.session, aggregate_type="payment", aggregate_id=payment["payment_id"],
            event_type="payment_created", payload={"payment_id": str(payment["payment_id"]), "amount": amount, "razorpay_order_id": rzp_order["id"]},
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

        return await self.update_status(payment["payment_id"], status="paid", payment_method="upi", _razorpay_payment_id=rzp_payment_id)

    async def get(self, payment_id: UUID) -> dict:
        payment = await self.repo.get(payment_id)
        if not payment:
            raise NotFoundError("Payment not found", code="PAYMENT_NOT_FOUND")
        return payment

    async def list(self, clinic_id: UUID) -> list[dict]:
        return await self.repo.list_by_clinic(clinic_id)

    async def update_status(self, payment_id: UUID, *, status: str, payment_method, waived_by=None, waived_reason=None, _razorpay_payment_id=None) -> dict:
        await self.get(payment_id)
        updated = await self.repo.set_status(payment_id, status=status, payment_method=payment_method, waived_by=waived_by, waived_reason=waived_reason, razorpay_payment_id=_razorpay_payment_id)
        await emit_event(
            self.session, aggregate_type="payment", aggregate_id=payment_id,
            event_type="payment_completed" if status == "paid" else "payment_waived" if status == "waived" else "payment_status_changed",
            payload={"payment_id": str(payment_id), "status": status},
        )

        # Propagate to the treatment_session's payment_status gate (Stage 8
        # billing rule) — direct SQL, not a full clinical-module import, to
        # avoid a circular dependency (clinical doesn't need to know about
        # payments to function, payments just needs to unlock what it gates).
        if updated and updated.get("session_id") and status in ("paid", "waived"):
            from sqlalchemy import text

            await self.session.execute(
                text("UPDATE treatment_sessions SET payment_status = :status WHERE session_id = :sid"),
                {"status": status, "sid": str(updated["session_id"])},
            )
        return updated  # type: ignore[return-value]
