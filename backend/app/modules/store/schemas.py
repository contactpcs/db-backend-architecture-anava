from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    name: str
    description: str | None = None
    category: str = Field(pattern="^(device|accessory)$")
    price: float
    sku: str | None = None


class ProductRead(BaseModel):
    product_id: UUID
    name: str
    category: str
    price: float
    sku: str | None
    is_active: bool


class OrderItemCreate(BaseModel):
    product_id: UUID
    quantity: int = 1


class StoreOrderCreate(BaseModel):
    patient_id: UUID
    clinic_id: UUID
    order_type: str = Field(pattern="^(device|accessory)$")
    treatment_plan_id: UUID | None = None
    items: list[OrderItemCreate]


class StoreOrderStatusUpdate(BaseModel):
    status: str = Field(
        pattern="^(doctor_approved|pending_dispatch|dispatched_to_clinic|received_at_clinic|collected_by_patient|cancelled)$"
    )


class StoreOrderRead(BaseModel):
    order_id: UUID
    patient_id: UUID
    clinic_id: UUID
    order_type: str
    status: str
    total_amount: float | None
    treatment_plan_id: UUID | None
    created_at: datetime


class DeviceAssignmentRead(BaseModel):
    da_id: UUID
    patient_id: UUID
    clinic_id: UUID
    plan_id: UUID
    device_type: str
    purchase_status: str
    order_id: UUID | None
