from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
from app.core.scoping import assert_clinic_scope, assert_owns_profile
from app.modules.store import schemas as s
from app.modules.store.service import DeviceAssignmentService, ProductService, StoreOrderService

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")


@router.post("/products", response_model=s.ProductRead, status_code=201)
async def create_product(body: s.ProductCreate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role("super_admin"))):
    return await ProductService(db).create(body.model_dump())


@router.get("/products", response_model=list[s.ProductRead])
async def list_products(
    category: str | None = None, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    return await ProductService(db).list(category=category)


@router.post("/store-orders", response_model=s.StoreOrderRead, status_code=201)
async def create_store_order(
    body: s.StoreOrderCreate, db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin", "receptionist")),
):
    data = body.model_dump()
    await assert_clinic_scope(ctx, db, data["clinic_id"])
    return await StoreOrderService(db).create(data, initiated_by=UUID(ctx.user_id))


@router.get("/store-orders", response_model=list[s.StoreOrderRead])
async def list_store_orders(
    patient_id: UUID | None = None, clinic_id: UUID | None = None, status: str | None = None,
    db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    if ctx.role == "patient":
        patient_id = UUID(ctx.user_id)
    return await StoreOrderService(db).list(patient_id=patient_id, clinic_id=clinic_id, status=status)


@router.get("/store-orders/{order_id}", response_model=s.StoreOrderRead)
async def get_store_order(order_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    order = await StoreOrderService(db).get(order_id)
    assert_owns_profile(ctx, order["patient_id"])
    return order


@router.patch("/store-orders/{order_id}/status", response_model=s.StoreOrderRead)
async def update_store_order_status(
    order_id: UUID, body: s.StoreOrderStatusUpdate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    return await StoreOrderService(db).update_status(order_id, status=body.status, changed_by=UUID(ctx.user_id))


@router.post("/device-assignments", response_model=s.DeviceAssignmentRead, status_code=201)
async def prompt_device_purchase(
    patient_id: UUID, clinic_id: UUID, plan_id: UUID, device_type: str,
    db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    return await DeviceAssignmentService(db).prompt_purchase(
        patient_id=patient_id, clinic_id=clinic_id, plan_id=plan_id, device_type=device_type, assigned_by=UUID(ctx.user_id)
    )
