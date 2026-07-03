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

    async def count_clinics(self, region_id: UUID) -> int:
        return (
            await self.session.execute(
                text("SELECT count(*) FROM clinics WHERE region_id = :id"), {"id": str(region_id)}
            )
        ).scalar_one()

    async def delete(self, region_id: UUID) -> None:
        await self.session.execute(text("DELETE FROM regions WHERE region_id = :id"), {"id": str(region_id)})


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

    async def count_for_region(self, region_id: UUID) -> int:
        return (
            await self.session.execute(
                text("SELECT count(*) FROM clinics WHERE region_id = :id"), {"id": str(region_id)}
            )
        ).scalar_one()

    async def count_dependents(self, clinic_id: UUID) -> int:
        """Active staff assignments + patients — anything that would orphan
        on delete. `patients` isn't owned by this module but is read-only
        here purely for the delete-safety count."""
        staff = (
            await self.session.execute(
                text("SELECT count(*) FROM clinic_staff_assignments WHERE clinic_id = :id AND is_active = TRUE"),
                {"id": str(clinic_id)},
            )
        ).scalar_one()
        patients = (
            await self.session.execute(
                text("SELECT count(*) FROM patients WHERE primary_clinic_id = :id"), {"id": str(clinic_id)}
            )
        ).scalar_one()
        return staff + patients

    async def delete(self, clinic_id: UUID) -> None:
        await self.session.execute(text("DELETE FROM clinics WHERE clinic_id = :id"), {"id": str(clinic_id)})


class AdminsRepository:
    """`admins` table has no owning module yet — role-detail row for
    super_admin/regional_admin/clinic_admin, mirrors doctors/CAs/receptionists."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, profile_id: UUID, admin_type: str, region_id: UUID | None, clinic_id: UUID | None) -> dict:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO admins (profile_id, admin_type, region_id, clinic_id) "
                    "VALUES (:profile_id, :admin_type, :region_id, :clinic_id) RETURNING *"
                ),
                {
                    "profile_id": str(profile_id),
                    "admin_type": admin_type,
                    "region_id": str(region_id) if region_id else None,
                    "clinic_id": str(clinic_id) if clinic_id else None,
                },
            )
        ).mappings().one()
        return dict(row)

    # Joined with profiles/regions/clinics — this is a purpose-built admin
    # management view, unlike the doctor/CA/receptionist/patient list
    # endpoints (which deliberately return bare rows with no profile join).
    async def list(self, *, admin_type: str | None = None, region_id: UUID | None = None, clinic_id: UUID | None = None) -> list[dict]:
        clauses, params = [], {}
        if admin_type:
            clauses.append("a.admin_type = :admin_type")
            params["admin_type"] = admin_type
        if region_id:
            clauses.append("a.region_id = :region_id")
            params["region_id"] = str(region_id)
        if clinic_id:
            clauses.append("a.clinic_id = :clinic_id")
            params["clinic_id"] = str(clinic_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (
            await self.session.execute(
                text(
                    "SELECT a.admin_id, a.profile_id, a.admin_type, a.region_id, a.clinic_id, a.created_at, "
                    "p.first_name, p.last_name, p.email, p.phone, p.is_active, "
                    "r.region_name, c.clinic_name "
                    "FROM admins a "
                    "JOIN profiles p ON p.id = a.profile_id "
                    "LEFT JOIN regions r ON r.region_id = a.region_id "
                    "LEFT JOIN clinics c ON c.clinic_id = a.clinic_id "
                    f"{where} ORDER BY a.created_at DESC"
                ),
                params,
            )
        ).mappings().all()
        return [dict(r) for r in rows]


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
