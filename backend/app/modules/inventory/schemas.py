from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class StockTransferCreate(BaseModel):
    product_id: UUID
    from_type: str = Field(pattern="^(super_admin|main_branch)$")
    from_clinic_id: UUID | None = None
    to_clinic_id: UUID
    quantity: int = Field(gt=0)
    order_id: UUID | None = None
    notes: str | None = None


class StockTransferStatusUpdate(BaseModel):
    status: str = Field(pattern="^(dispatched|received)$")


class StockTransferRead(BaseModel):
    st_id: UUID
    product_id: UUID
    from_type: str
    from_clinic_id: UUID | None
    to_clinic_id: UUID
    quantity: int
    status: str
    created_at: datetime


class InventoryRead(BaseModel):
    inventory_id: UUID
    product_id: UUID
    clinic_id: UUID
    quantity: int
    updated_at: datetime
