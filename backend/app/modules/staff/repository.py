from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional, insert_returning


async def create_profile(session: AsyncSession, *, email: str, first_name: str, last_name: str,
                          phone: str | None, role: str) -> dict:
    """Every staff/admin identity starts as a profiles row. cognito_sub is a
    placeholder ('pending-<uuid>') until Stage 13's real Cognito invite flow
    replaces it — this module owns the write today because no dedicated
    profiles/identity module exists (profiles is core/universal per
    Architecture Section 6, read by auth, not owned by any one module)."""
    row = (
        await session.execute(
            text(
                "INSERT INTO profiles (cognito_sub, email, first_name, last_name, phone, role) "
                "VALUES ('pending-' || gen_random_uuid()::TEXT, :email, :first_name, :last_name, :phone, :role) "
                "RETURNING *"
            ),
            {"email": email, "first_name": first_name, "last_name": last_name, "phone": phone, "role": role},
        )
    ).mappings().one()
    return dict(row)


class DoctorRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, profile_id: UUID, specialization, license_number, hospital_affiliation,
                      max_patient_count: int) -> dict:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO doctors (profile_id, specialization, license_number, "
                    "hospital_affiliation, max_patient_count) VALUES "
                    "(:profile_id, :specialization, :license_number, :hospital_affiliation, :max_patient_count) "
                    "RETURNING *"
                ),
                {
                    "profile_id": str(profile_id),
                    "specialization": specialization,
                    "license_number": license_number,
                    "hospital_affiliation": hospital_affiliation,
                    "max_patient_count": max_patient_count,
                },
            )
        ).mappings().one()
        return dict(row)

    async def get(self, doctor_id: UUID) -> dict | None:
        row = (
            await self.session.execute(
                text("SELECT * FROM doctors WHERE doctor_id = :id"), {"id": str(doctor_id)}
            )
        ).mappings().first()
        return dict(row) if row else None

    async def list(self, *, clinic_id: UUID | None = None) -> list[dict]:
        if clinic_id:
            rows = (
                await self.session.execute(
                    text(
                        "SELECT d.* FROM doctors d "
                        "JOIN clinic_staff_assignments csa ON csa.profile_id = d.profile_id "
                        "WHERE csa.clinic_id = :clinic_id AND csa.is_active = TRUE"
                    ),
                    {"clinic_id": str(clinic_id)},
                )
            ).mappings().all()
        else:
            rows = (await self.session.execute(text("SELECT * FROM doctors"))).mappings().all()
        return [dict(r) for r in rows]

    async def update(self, doctor_id: UUID, fields: dict) -> dict | None:
        if not fields:
            return await self.get(doctor_id)
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        row = (
            await self.session.execute(
                text(f"UPDATE doctors SET {set_clause} WHERE doctor_id = :id RETURNING *"),
                {**fields, "id": str(doctor_id)},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def active_patient_counts(self, doctor_ids: list[UUID]) -> dict[str, int]:
        if not doctor_ids:
            return {}
        rows = (
            await self.session.execute(
                text(
                    "SELECT doctor_id, active_patient_count FROM v_doctor_active_patient_counts "
                    "WHERE doctor_id = ANY(:ids)"
                ),
                {"ids": [str(d) for d in doctor_ids]},
            )
        ).mappings().all()
        return {str(r["doctor_id"]): r["active_patient_count"] for r in rows}


class ClinicalAssistantRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, ca_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM clinical_assistants WHERE ca_id = :id"), {"id": str(ca_id)})

    async def create(self, *, profile_id: UUID, clinic_id: UUID, qualification: str | None) -> dict:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO clinical_assistants (profile_id, clinic_id, qualification) "
                    "VALUES (:profile_id, :clinic_id, :qualification) RETURNING *"
                ),
                {"profile_id": str(profile_id), "clinic_id": str(clinic_id), "qualification": qualification},
            )
        ).mappings().one()
        return dict(row)

    async def list(self, *, clinic_id: UUID | None = None) -> list[dict]:
        if clinic_id:
            rows = (
                await self.session.execute(
                    text("SELECT * FROM clinical_assistants WHERE clinic_id = :clinic_id"),
                    {"clinic_id": str(clinic_id)},
                )
            ).mappings().all()
        else:
            rows = (await self.session.execute(text("SELECT * FROM clinical_assistants"))).mappings().all()
        return [dict(r) for r in rows]


class ReceptionistRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, profile_id: UUID, clinic_id: UUID) -> dict:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO receptionists (profile_id, clinic_id) VALUES (:profile_id, :clinic_id) "
                    "RETURNING *"
                ),
                {"profile_id": str(profile_id), "clinic_id": str(clinic_id)},
            )
        ).mappings().one()
        return dict(row)

    async def list(self, *, clinic_id: UUID | None = None) -> list[dict]:
        if clinic_id:
            rows = (
                await self.session.execute(
                    text("SELECT * FROM receptionists WHERE clinic_id = :clinic_id"),
                    {"clinic_id": str(clinic_id)},
                )
            ).mappings().all()
        else:
            rows = (await self.session.execute(text("SELECT * FROM receptionists"))).mappings().all()
        return [dict(r) for r in rows]


class CaDoctorAssignmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, ca_id: UUID, doctor_id: UUID, clinic_id: UUID, is_primary: bool) -> dict:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO ca_doctor_assignments (ca_id, doctor_id, clinic_id, is_primary) "
                    "VALUES (:ca_id, :doctor_id, :clinic_id, :is_primary) RETURNING *"
                ),
                {"ca_id": str(ca_id), "doctor_id": str(doctor_id), "clinic_id": str(clinic_id), "is_primary": is_primary},
            )
        ).mappings().one()
        return dict(row)

    async def list(self, *, ca_id: UUID | None = None, doctor_id: UUID | None = None) -> list[dict]:
        clauses, params = ["removed_at IS NULL"], {}
        if ca_id:
            clauses.append("ca_id = :ca_id")
            params["ca_id"] = str(ca_id)
        if doctor_id:
            clauses.append("doctor_id = :doctor_id")
            params["doctor_id"] = str(doctor_id)
        rows = (
            await self.session.execute(
                text(f"SELECT * FROM ca_doctor_assignments WHERE {' AND '.join(clauses)}"), params
            )
        ).mappings().all()
        return [dict(r) for r in rows]


class StaffRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("staff_requests", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, request_id: UUID) -> dict | None:
        row = (
            await self.session.execute(
                text("SELECT * FROM staff_requests WHERE request_id = :id"), {"id": str(request_id)}
            )
        ).mappings().first()
        return dict(row) if row else None

    async def list(self, *, clinic_id: UUID | None = None, status: str | None = None) -> list[dict]:
        clauses, params = [], {}
        if clinic_id:
            clauses.append("clinic_id = :clinic_id")
            params["clinic_id"] = str(clinic_id)
        if status:
            clauses.append("status = :status")
            params["status"] = status
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (
            await self.session.execute(
                text(f"SELECT * FROM staff_requests {where} ORDER BY created_at DESC"), params
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def decide(self, request_id: UUID, *, status: str, reviewed_by: UUID, review_notes: str | None) -> dict | None:
        row = (
            await self.session.execute(
                text(
                    "UPDATE staff_requests SET status = :status, reviewed_by = :reviewed_by, "
                    "review_notes = :review_notes WHERE request_id = :id RETURNING *"
                ),
                {"status": status, "reviewed_by": str(reviewed_by), "review_notes": review_notes, "id": str(request_id)},
            )
        ).mappings().first()
        return dict(row) if row else None
