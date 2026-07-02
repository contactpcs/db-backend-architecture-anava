from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, insert_returning


class RegionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, region_name: str, country: str, state: str) -> dict:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO regions (region_name, country, state) "
                    "VALUES (:region_name, :country, :state) RETURNING *"
                ),
                {"region_name": region_name, "country": country, "state": state},
            )
        ).mappings().one()
        return dict(row)

    async def get(self, region_id: UUID) -> dict | None:
        row = (
            await self.session.execute(
                text("SELECT * FROM regions WHERE region_id = :id"), {"id": str(region_id)}
            )
        ).mappings().first()
        return dict(row) if row else None

    async def list(self) -> list[dict]:
        rows = (await self.session.execute(text("SELECT * FROM regions ORDER BY created_at DESC"))).mappings().all()
        return [dict(r) for r in rows]

    async def update(self, region_id: UUID, fields: dict) -> dict | None:
        if not fields:
            return await self.get(region_id)
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        params = {**fields, "id": str(region_id)}
        row = (
            await self.session.execute(
                text(f"UPDATE regions SET {set_clause} WHERE region_id = :id RETURNING *"), params
            )
        ).mappings().first()
        return dict(row) if row else None


class ClinicRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("clinics", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, clinic_id: UUID) -> dict | None:
        row = (
            await self.session.execute(
                text("SELECT * FROM clinics WHERE clinic_id = :id"), {"id": str(clinic_id)}
            )
        ).mappings().first()
        return dict(row) if row else None

    async def list(self, *, region_id: UUID | None = None, status: str | None = None) -> list[dict]:
        clauses, params = [], {}
        if region_id:
            clauses.append("region_id = :region_id")
            params["region_id"] = str(region_id)
        if status:
            clauses.append("status = :status")
            params["status"] = status
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (
            await self.session.execute(
                text(f"SELECT * FROM clinics {where} ORDER BY created_at DESC"), params
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def update(self, clinic_id: UUID, fields: dict) -> dict | None:
        if not fields:
            return await self.get(clinic_id)
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        params = {**fields, "id": str(clinic_id)}
        row = (
            await self.session.execute(
                text(f"UPDATE clinics SET {set_clause} WHERE clinic_id = :id RETURNING *"), params
            )
        ).mappings().first()
        return dict(row) if row else None


class ClinicRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("clinic_requests", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, request_id: UUID) -> dict | None:
        row = (
            await self.session.execute(
                text("SELECT * FROM clinic_requests WHERE request_id = :id"), {"id": str(request_id)}
            )
        ).mappings().first()
        return dict(row) if row else None

    async def list(self, *, region_id: UUID | None = None, status: str | None = None) -> list[dict]:
        clauses, params = [], {}
        if region_id:
            clauses.append("region_id = :region_id")
            params["region_id"] = str(region_id)
        if status:
            clauses.append("status = :status")
            params["status"] = status
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (
            await self.session.execute(
                text(f"SELECT * FROM clinic_requests {where} ORDER BY created_at DESC"), params
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def decide(self, request_id: UUID, *, status: str, reviewed_by: UUID, review_notes: str | None) -> dict | None:
        row = (
            await self.session.execute(
                text(
                    "UPDATE clinic_requests SET status = :status, reviewed_by = :reviewed_by, "
                    "review_notes = :review_notes WHERE request_id = :id RETURNING *"
                ),
                {
                    "status": status,
                    "reviewed_by": str(reviewed_by),
                    "review_notes": review_notes,
                    "id": str(request_id),
                },
            )
        ).mappings().first()
        return dict(row) if row else None


class StaffAssignmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, clinic_id: UUID, profile_id: UUID, staff_role: str) -> dict:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO clinic_staff_assignments (clinic_id, profile_id, staff_role) "
                    "VALUES (:clinic_id, :profile_id, :staff_role) RETURNING *"
                ),
                {"clinic_id": str(clinic_id), "profile_id": str(profile_id), "staff_role": staff_role},
            )
        ).mappings().one()
        return dict(row)

    async def list_for_clinic(self, clinic_id: UUID) -> list[dict]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT * FROM clinic_staff_assignments WHERE clinic_id = :clinic_id "
                    "ORDER BY joined_at DESC"
                ),
                {"clinic_id": str(clinic_id)},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def remove(self, assignment_id: UUID) -> dict | None:
        row = (
            await self.session.execute(
                text(
                    "UPDATE clinic_staff_assignments SET is_active = FALSE, removed_at = NOW() "
                    "WHERE assignment_id = :id RETURNING *"
                ),
                {"id": str(assignment_id)},
            )
        ).mappings().first()
        return dict(row) if row else None
