from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional, insert_returning


class ProtocolRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("assessment_protocol_requests", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, request_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM assessment_protocol_requests WHERE request_id = :id"),
            {"id": str(request_id)},
        )

    async def list(self, *, patient_id: UUID | None = None, status: str | None = None) -> list[dict]:
        clauses, params = [], {}
        if patient_id:
            clauses.append("patient_id = :pid")
            params["pid"] = str(patient_id)
        if status:
            clauses.append("status = :status")
            params["status"] = status
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (
            (await self.session.execute(text(f"SELECT * FROM assessment_protocol_requests {where} ORDER BY submitted_at DESC"), params))
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def decide(self, request_id: UUID, *, status: str, doctor_notes: str | None) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE assessment_protocol_requests SET status = :status, doctor_notes = :notes, "
                "reviewed_at = NOW() WHERE request_id = :id RETURNING *"
            ),
            {"status": status, "notes": doctor_notes, "id": str(request_id)},
        )
