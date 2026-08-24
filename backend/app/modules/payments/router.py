import io
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.db import RequestContext, get_db
from app.core.exceptions import BusinessRuleError, PermissionError_
from app.core.permissions import require_role
from app.core.scoping import assert_owns_profile
from app.modules.payments import schemas as s
from app.modules.payments.service import PaymentService, assert_payment_clinic_scope

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")
# Roles pinned to their own clinic_id — cannot cross-view/act on another
# clinic's payments. super_admin/regional_admin are cross-clinic by design.
_CLINIC_PINNED_STAFF = ("clinic_admin", "doctor", "clinical_assistant", "receptionist")


@router.post("/payments", response_model=s.PaymentRead, status_code=201)
async def create_payment(body: s.PaymentCreate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await PaymentService(db).create(order_id=body.order_id, ctx=ctx)


@router.get("/payments", response_model=list[s.PaymentRead])
async def list_payments(clinic_id: UUID | None = None, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    if ctx.role in _CLINIC_PINNED_STAFF:
        clinic_id = UUID(ctx.clinic_id)
    if clinic_id is None:
        raise BusinessRuleError("clinic_id is required", code="CLINIC_ID_REQUIRED")
    return await PaymentService(db).list(clinic_id)


@router.get("/payments/{payment_id}", response_model=s.PaymentRead)
async def get_payment(payment_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    service = PaymentService(db)
    payment = await service.get(payment_id)
    if ctx.role == "patient":
        owner_profile_id = await service.repo.get_owner_profile_id(payment_id)
        assert_owns_profile(ctx, owner_profile_id)
    else:
        clinic_id = await service.repo.get_clinic_id(payment_id)
        await assert_payment_clinic_scope(ctx, db, clinic_id)
    return payment


@router.patch("/payments/{payment_id}/status", response_model=s.PaymentRead)
async def update_payment_status(
    payment_id: UUID,
    body: s.PaymentStatusUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    if body.status == "waived" and ctx.role not in ("clinic_admin", "super_admin"):
        raise PermissionError_("Only a Clinic Admin can waive a payment", code="WAIVER_NOT_PERMITTED")
    clinic_id = await PaymentService(db).repo.get_clinic_id(payment_id)
    await assert_payment_clinic_scope(ctx, db, clinic_id)
    return await PaymentService(db).update_status(
        payment_id,
        status=body.status,
        payment_method=body.payment_method,
        waived_by=UUID(ctx.user_id) if body.status == "waived" else None,
        waived_reason=body.waived_reason,
        _changed_by=UUID(ctx.user_id),
        _changed_by_role=ctx.role,
    )


@router.get("/me/payments", response_model=list[s.PaymentHistoryRead])
async def list_my_payments(db=Depends(get_db), ctx: RequestContext = Depends(require_role("patient"))):
    return await PaymentService(db).list_mine(UUID(ctx.user_id))


@router.get("/appointments/{appointment_id}/payments/amount", response_model=s.PaymentAmountRead)
async def get_appointment_payment_amount(
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    """Step 2 — resolved price for display, before checkout starts."""
    return await PaymentService(db).get_payment_amount(appointment_id, ctx)


@router.post("/appointments/{appointment_id}/payments/order", response_model=s.PaymentOrderRead, status_code=201)
async def create_appointment_payment_order(
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    """Step 3 — "Pay Now". Creates a real Razorpay order; the frontend opens
    Razorpay Checkout with razorpay_order_id + razorpay_key_id from the
    response. Only the signed webhook or the signature-verified /verify
    call below can ever mark this paid."""
    return await PaymentService(db).create_order(appointment_id, ctx)


@router.post("/payments/{payment_id}/verify", response_model=s.PaymentRead)
async def verify_appointment_payment(
    payment_id: UUID,
    body: s.PaymentVerify,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    """Client-callback confirmation — Razorpay Checkout's success handler
    hands the browser these fields, the frontend forwards them here. A
    second, independent confirmation path alongside the webhook (step 5-7),
    not a replacement for it: both are signature-verified and idempotent."""
    return await PaymentService(db).verify_payment(
        payment_id,
        razorpay_order_id=body.razorpay_order_id,
        razorpay_payment_id=body.razorpay_payment_id,
        razorpay_signature=body.razorpay_signature,
        ctx=ctx,
    )


@router.get("/appointments/{appointment_id}/payments/receipt")
async def get_appointment_receipt(
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    """Branded PDF, built fresh from DB data on every request — nothing
    stored on disk. Same ownership/scope check as the amount/order endpoints
    above (_get_appointment_for_pay), so a patient can only pull their own."""
    pdf_bytes = await PaymentService(db).get_receipt_pdf(appointment_id, ctx)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="receipt-{appointment_id}.pdf"'},
    )


@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request, db=Depends(get_db)):
    """Public endpoint (added to PUBLIC_PATHS) — Razorpay calls this
    server-to-server, no user JWT involved. HMAC signature is the actual
    authentication (Architecture Section 14)."""
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    return await PaymentService(db).handle_webhook(payload=raw_body, signature=signature, body=json.loads(raw_body or b"{}"))
