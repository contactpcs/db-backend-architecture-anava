from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
from app.modules.inventory import schemas as s
from app.modules.inventory.service import InventoryService, StockTransferService

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")


@router.get("/inventory", response_model=list[s.InventoryRead])
async def list_inventory(
    clinic_id: UUID | None = None,
    product_id: UUID | None = None,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    return await InventoryService(db).list(clinic_id=clinic_id, product_id=product_id)


@router.post("/stock-transfers", response_model=s.StockTransferRead, status_code=201)
async def create_stock_transfer(
    body: s.StockTransferCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin")),
):
    return await StockTransferService(db).create(body.model_dump(), initiated_by=UUID(ctx.user_id))


@router.get("/stock-transfers", response_model=list[s.StockTransferRead])
async def list_stock_transfers(
    to_clinic_id: UUID | None = None,
    status: str | None = None,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    return await StockTransferService(db).list(to_clinic_id=to_clinic_id, status=status)


@router.patch("/stock-transfers/{st_id}/status", response_model=s.StockTransferRead)
async def update_stock_transfer_status(
    st_id: UUID,
    body: s.StockTransferStatusUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin")),
):
    return await StockTransferService(db).update_status(st_id, status=body.status, changed_by=UUID(ctx.user_id))
