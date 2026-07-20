import json
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core.db import RequestContext, get_db
from app.core.exceptions import BusinessRuleError, PermissionError_
from app.core.permissions import require_role
from app.core.scoping import assert_owns_profile
from app.modules.payments import schemas as s
from app.modules.payments.service import PaymentService

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")


@router.post("/payments", response_model=s.PaymentRead, status_code=201)
async def create_payment(body: s.PaymentCreate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await PaymentService(db).create(session_id=body.session_id, order_id=body.order_id, amount=body.amount, currency=body.currency)


@router.get("/payments", response_model=list[s.PaymentRead])
async def list_payments(clinic_id: UUID | None = None, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    if clinic_id is None and ctx.role == "clinic_admin":
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
    return payment


@router.patch("/payments/{payment_id}/status", response_model=s.PaymentRead)
async def update_payment_status(payment_id: UUID, body: s.PaymentStatusUpdate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    if body.status == "waived" and ctx.role not in ("clinic_admin", "super_admin"):
        raise PermissionError_("Only a Clinic Admin can waive a payment", code="WAIVER_NOT_PERMITTED")
    return await PaymentService(db).update_status(
        payment_id, status=body.status, payment_method=body.payment_method,
        waived_by=UUID(ctx.user_id) if body.status == "waived" else None, waived_reason=body.waived_reason,
    )


@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request, db=Depends(get_db)):
    """Public endpoint (added to PUBLIC_PATHS) — Razorpay calls this
    server-to-server, no user JWT involved. HMAC signature is the actual
    authentication (Architecture Section 14)."""
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    return await PaymentService(db).handle_webhook(payload=raw_body, signature=signature, body=json.loads(raw_body or b"{}"))
