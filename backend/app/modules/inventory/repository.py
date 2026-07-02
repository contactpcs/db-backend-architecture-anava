from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional, insert_returning


class InventoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(self, *, clinic_id: UUID | None = None, product_id: UUID | None = None) -> list[dict]:
        clauses, params = [], {}
        if clinic_id:
            clauses.append("clinic_id = :cid")
            params["cid"] = str(clinic_id)
        if product_id:
            clauses.append("product_id = :pid")
            params["pid"] = str(product_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (await self.session.execute(text(f"SELECT * FROM inventory {where}"), params)).mappings().all()
        return [dict(r) for r in rows]

    async def adjust(self, *, product_id: UUID, clinic_id: UUID, delta: int) -> dict:
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO inventory (product_id, clinic_id, quantity) VALUES (:pid, :cid, GREATEST(:delta, 0)) "
                "ON CONFLICT (product_id, clinic_id) DO UPDATE SET quantity = GREATEST(inventory.quantity + :delta, 0), "
                "updated_at = NOW() RETURNING *"
            ),
            {"pid": str(product_id), "cid": str(clinic_id), "delta": delta},
        )


class StockTransferRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("stock_transfers", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, st_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM stock_transfers WHERE st_id = :id"), {"id": str(st_id)})

    async def list(self, *, to_clinic_id: UUID | None = None, status: str | None = None) -> list[dict]:
        clauses, params = [], {}
        if to_clinic_id:
            clauses.append("to_clinic_id = :cid")
            params["cid"] = str(to_clinic_id)
        if status:
            clauses.append("status = :status")
            params["status"] = status
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (await self.session.execute(text(f"SELECT * FROM stock_transfers {where} ORDER BY created_at DESC"), params)).mappings().all()
        return [dict(r) for r in rows]

    async def set_status(self, st_id: UUID, *, status: str, received_by=None) -> dict | None:
        extra = ""
        params = {"status": status, "id": str(st_id)}
        if status == "dispatched":
            extra = ", dispatched_at = NOW()"
        elif status == "received":
            extra = ", received_at = NOW(), received_by = :received_by"
            params["received_by"] = str(received_by) if received_by else None
        return await fetch_optional(self.session, text(f"UPDATE stock_transfers SET status = :status {extra} WHERE st_id = :id RETURNING *"), params)
