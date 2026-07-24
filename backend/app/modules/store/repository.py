from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional, insert_returning


class ProductRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("products", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, product_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM products WHERE product_id = :id"), {"id": str(product_id)})

    async def list(self, *, category: str | None = None) -> list[dict]:
        params = {"cat": category} if category else {}
        rows = (
            (
                await self.session.execute(
                    text(f"SELECT * FROM products WHERE is_active = TRUE {'AND category = :cat' if category else ''}"), params
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


class StoreOrderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("store_orders", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, order_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM store_orders WHERE order_id = :id"), {"id": str(order_id)})

    async def list(self, *, patient_id: UUID | None = None, clinic_id: UUID | None = None, status: str | None = None) -> list[dict]:
        clauses, params = [], {}
        if patient_id:
            clauses.append("patient_id = :pid")
            params["pid"] = str(patient_id)
        if clinic_id:
            clauses.append("clinic_id = :cid")
            params["cid"] = str(clinic_id)
        if status:
            clauses.append("status = :status")
            params["status"] = status
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (await self.session.execute(text(f"SELECT * FROM store_orders {where} ORDER BY created_at DESC"), params)).mappings().all()
        return [dict(r) for r in rows]

    async def set_status(self, order_id: UUID, *, status: str, approved_by=None) -> dict | None:
        extra = ""
        params: dict[str, Any] = {"status": status, "id": str(order_id)}
        if status == "doctor_approved":
            extra = ", approved_by = :approved_by"
            params["approved_by"] = str(approved_by) if approved_by else None
        elif status == "cancelled":
            extra = ", cancelled_by = :approved_by, cancelled_at = NOW()"
            params["approved_by"] = str(approved_by) if approved_by else None
        return await fetch_optional(
            self.session, text(f"UPDATE store_orders SET status = :status {extra} WHERE order_id = :id RETURNING *"), params
        )

    async def add_item(self, *, order_id: UUID, product_id: UUID, quantity: int, unit_price: float) -> dict:
        return await fetch_one(
            self.session,
            text("INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (:oid, :pid, :qty, :price) RETURNING *"),
            {"oid": str(order_id), "pid": str(product_id), "qty": quantity, "price": unit_price},
        )


class DeviceAssignmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("device_assignments", data)
        return await fetch_one(self.session, sql, params)

    async def get_for_plan(self, plan_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM device_assignments WHERE plan_id = :pid ORDER BY created_at DESC LIMIT 1"),
            {"pid": str(plan_id)},
        )

    async def set_status(self, da_id: UUID, *, purchase_status: str, order_id=None) -> dict | None:
        timestamps = {
            "purchased": ", purchased_at = NOW()",
            "collected": ", collected_at = NOW()",
        }.get(purchase_status, "")
        return await fetch_optional(
            self.session,
            text(
                "UPDATE device_assignments SET purchase_status = :status, "
                f"order_id = COALESCE(:order_id, order_id) {timestamps} WHERE da_id = :id RETURNING *"
            ),
            {"status": purchase_status, "order_id": str(order_id) if order_id else None, "id": str(da_id)},
        )
