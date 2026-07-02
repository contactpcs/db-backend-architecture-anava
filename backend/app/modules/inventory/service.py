from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.modules.inventory.repository import InventoryRepository, StockTransferRepository

_TRANSITIONS = {"pending": {"dispatched"}, "dispatched": {"received"}, "received": set()}


class InventoryService:
    def __init__(self, session: AsyncSession):
        self.repo = InventoryRepository(session)

    async def list(self, **filters) -> list[dict]:
        return await self.repo.list(**filters)


class StockTransferService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = StockTransferRepository(session)
        self.inventory = InventoryRepository(session)

    async def create(self, data: dict, *, initiated_by: UUID) -> dict:
        if data["from_type"] == "super_admin" and data.get("from_clinic_id"):
            raise BusinessRuleError("from_clinic_id must be null when from_type='super_admin'", code="INVALID_TRANSFER_SOURCE")
        if data["from_type"] == "main_branch" and not data.get("from_clinic_id"):
            raise BusinessRuleError("from_clinic_id is required when from_type='main_branch'", code="INVALID_TRANSFER_SOURCE")

        payload = {
            "product_id": str(data["product_id"]), "from_type": data["from_type"],
            "from_clinic_id": str(data["from_clinic_id"]) if data.get("from_clinic_id") else None,
            "to_clinic_id": str(data["to_clinic_id"]), "quantity": data["quantity"],
            "order_id": str(data["order_id"]) if data.get("order_id") else None,
            "initiated_by": str(initiated_by), "notes": data.get("notes"),
        }
        transfer = await self.repo.create(payload)
        await emit_event(
            self.session, aggregate_type="stock_transfer", aggregate_id=transfer["st_id"],
            event_type="stock_transfer_initiated", payload={"st_id": str(transfer["st_id"])},
        )
        return transfer

    async def get(self, st_id: UUID) -> dict:
        transfer = await self.repo.get(st_id)
        if not transfer:
            raise NotFoundError("Stock transfer not found", code="STOCK_TRANSFER_NOT_FOUND")
        return transfer

    async def list(self, **filters) -> list[dict]:
        return await self.repo.list(**filters)

    async def update_status(self, st_id: UUID, *, status: str, changed_by: UUID) -> dict:
        transfer = await self.get(st_id)
        if status not in _TRANSITIONS.get(transfer["status"], set()):
            raise BusinessRuleError(f"Cannot transition stock transfer from '{transfer['status']}' to '{status}'", code="INVALID_TRANSFER_TRANSITION")
        updated = await self.repo.set_status(st_id, status=status, received_by=changed_by if status == "received" else None)

        if status == "received":
            # Move stock: increment destination, decrement source (if any —
            # super_admin central stock has no clinics-table row to decrement).
            await self.inventory.adjust(product_id=transfer["product_id"], clinic_id=transfer["to_clinic_id"], delta=transfer["quantity"])
            if transfer["from_clinic_id"]:
                await self.inventory.adjust(product_id=transfer["product_id"], clinic_id=transfer["from_clinic_id"], delta=-transfer["quantity"])

        await emit_event(
            self.session, aggregate_type="stock_transfer", aggregate_id=st_id,
            event_type="stock_dispatched" if status == "dispatched" else "stock_received",
            payload={"st_id": str(st_id), "status": status},
        )
        return updated  # type: ignore[return-value]
