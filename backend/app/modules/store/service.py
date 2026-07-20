from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.core.fsm import assert_transition
from app.core.resolve import resolve_patient_profile_id as _resolve_patient_profile_id
from app.modules.store.repository import DeviceAssignmentRepository, ProductRepository, StoreOrderRepository

# Device order flow (Master Doc Section 14.3): pending_doctor_approval -> doctor_approved
#   -> pending_dispatch -> dispatched_to_clinic -> received_at_clinic -> collected_by_patient
# Accessory order flow (Section 14.4): skips straight to pending_dispatch, same tail.
_DEVICE_TRANSITIONS = {
    "pending_doctor_approval": {"doctor_approved", "cancelled"},
    "doctor_approved": {"pending_dispatch", "cancelled"},
    "pending_dispatch": {"dispatched_to_clinic", "cancelled"},
    "dispatched_to_clinic": {"received_at_clinic"},
    "received_at_clinic": {"collected_by_patient"},
    "collected_by_patient": set(),
    "cancelled": set(),
}


class ProductService:
    def __init__(self, session: AsyncSession):
        self.repo = ProductRepository(session)

    async def create(self, data: dict) -> dict:
        return await self.repo.create(data)

    async def list(self, *, category: str | None = None) -> list[dict]:
        return await self.repo.list(category=category)


class StoreOrderService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = StoreOrderRepository(session)
        self.products = ProductRepository(session)

    async def create(self, data: dict, *, initiated_by: UUID) -> dict:
        patient_profile_id = await _resolve_patient_profile_id(self.session, data["patient_id"])

        if data["order_type"] == "device" and not data.get("treatment_plan_id"):
            raise BusinessRuleError("Device orders require a treatment_plan_id", code="TREATMENT_PLAN_REQUIRED")

        total = 0.0
        items_with_price = []
        for item in data["items"]:
            product = await self.products.get(item["product_id"])
            if not product:
                raise NotFoundError(f"Product {item['product_id']} not found", code="PRODUCT_NOT_FOUND")
            if data["order_type"] == "device" and product["category"] != "device":
                raise BusinessRuleError("Device orders can only contain device products", code="PRODUCT_CATEGORY_MISMATCH")
            line_total = float(product["price"]) * item["quantity"]
            total += line_total
            items_with_price.append({**item, "unit_price": product["price"]})

        payload = {
            "patient_id": str(patient_profile_id), "clinic_id": str(data["clinic_id"]), "initiated_by": str(initiated_by),
            "order_type": data["order_type"],
            "status": "pending_doctor_approval" if data["order_type"] == "device" else "pending_dispatch",
            "total_amount": total,
            "treatment_plan_id": str(data["treatment_plan_id"]) if data.get("treatment_plan_id") else None,
        }
        order = await self.repo.create(payload)
        for item in items_with_price:
            await self.repo.add_item(order_id=order["order_id"], product_id=item["product_id"], quantity=item["quantity"], unit_price=item["unit_price"])

        await emit_event(
            self.session, aggregate_type="store_order", aggregate_id=order["order_id"],
            event_type="order_created", payload={"order_id": str(order["order_id"]), "order_type": data["order_type"]},
        )
        return order

    async def get(self, order_id: UUID) -> dict:
        order = await self.repo.get(order_id)
        if not order:
            raise NotFoundError("Store order not found", code="ORDER_NOT_FOUND")
        return order

    async def list(self, **filters) -> list[dict]:
        return await self.repo.list(**filters)

    async def update_status(self, order_id: UUID, *, status: str, changed_by: UUID) -> dict:
        order = await self.get(order_id)
        assert_transition(order["status"], status, _DEVICE_TRANSITIONS, entity="order", code="INVALID_ORDER_TRANSITION")
        updated = await self.repo.set_status(order_id, status=status, approved_by=changed_by if status in ("doctor_approved", "cancelled") else None)

        event_map = {
            "doctor_approved": "device_order_approved", "dispatched_to_clinic": "stock_dispatched",
            "collected_by_patient": "order_collected", "cancelled": "order_cancelled",
        }
        await emit_event(
            self.session, aggregate_type="store_order", aggregate_id=order_id,
            event_type=event_map.get(status, "order_status_changed"), payload={"order_id": str(order_id), "status": status},
        )

        # Keep device_assignments.purchase_status in sync with the order lifecycle.
        if order["treatment_plan_id"] and status == "collected_by_patient":
            da_repo = DeviceAssignmentRepository(self.session)
            da = await da_repo.get_for_plan(order["treatment_plan_id"])
            if da:
                await da_repo.set_status(da["da_id"], purchase_status="collected")
        return updated  # type: ignore[return-value]


class DeviceAssignmentService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = DeviceAssignmentRepository(session)

    async def prompt_purchase(self, *, patient_id: UUID, clinic_id: UUID, plan_id: UUID, device_type: str, assigned_by: UUID) -> dict:
        patient_profile_id = await _resolve_patient_profile_id(self.session, patient_id)
        da = await self.repo.create({
            "patient_id": str(patient_profile_id), "clinic_id": str(clinic_id), "plan_id": str(plan_id),
            "assigned_by": str(assigned_by), "device_type": device_type,
        })
        await emit_event(
            self.session, aggregate_type="device_assignment", aggregate_id=da["da_id"],
            event_type="device_purchase_prompted", payload={"da_id": str(da["da_id"]), "patient_id": str(patient_id)},
        )
        return da
