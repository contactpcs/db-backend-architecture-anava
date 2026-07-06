from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional, insert_returning


async def create_profile(session: AsyncSession, *, email: str, first_name: str, last_name: str,
                          phone: str | None, role: str, is_active: bool = True, consent_signed: bool = True,
                          gender: str | None = None, dob=None, address: str | None = None,
                          city: str | None = None, state: str | None = None, country: str | None = None,
                          pincode: str | None = None) -> dict:
    """Every staff/admin identity starts as a profiles row. cognito_sub is a
    placeholder ('pending-<uuid>') until Stage 13's real Cognito invite flow
    replaces it — this module owns the write today because no dedicated
    profiles/identity module exists (profiles is core/universal per
    Architecture Section 6, read by auth, not owned by any one module).

    is_active/consent_signed both default True for backward compatibility,
    but every caller in this codebase now passes both False for
    newly-registered people — they're gated until they sign the onboarding
    consent (see consent/service.py ConsentRecordService.sign, and
    SQL/28_consent_redesign.sql for why these are two separate columns)."""
    row = (
        await session.execute(
            text(
                "INSERT INTO profiles (cognito_sub, email, first_name, last_name, phone, role, is_active, "
                "consent_signed, gender, dob, address, city, state, country, pincode) "
                "VALUES ('pending-' || gen_random_uuid()::TEXT, :email, :first_name, :last_name, :phone, :role, "
                ":is_active, :consent_signed, :gender, :dob, :address, :city, :state, :country, :pincode) "
                "RETURNING *"
            ),
            {
                "email": email, "first_name": first_name, "last_name": last_name, "phone": phone, "role": role,
                "is_active": is_active, "consent_signed": consent_signed, "gender": gender, "dob": dob,
                "address": address, "city": city, "state": state, "country": country, "pincode": pincode,
            },
        )
    ).mappings().one()
    return dict(row)


async def update_profile(session: AsyncSession, profile_id: UUID, fields: dict) -> None:
    """Shared PATCH for the profile-level fields every staff *Update schema
    accepts (first_name/last_name/phone/gender/dob/address) — role-specific
    fields still go through that role's own repository.update()."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    await session.execute(text(f"UPDATE profiles SET {set_clause} WHERE id = :pid"), {**fields, "pid": str(profile_id)})


async def soft_delete_profile(session: AsyncSession, profile_id: UUID, *, deleted_by: UUID) -> None:
    """Never a real DELETE — matches the same soft-delete convention as
    patients (deactivate, keep the record for audit/history)."""
    await session.execute(
        text("UPDATE profiles SET deleted_by = :by, deleted_at = NOW(), is_active = FALSE WHERE id = :pid"),
        {"by": str(deleted_by), "pid": str(profile_id)},
    )


class DoctorRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, profile_id: UUID, clinic_id: UUID, specialization, license_number, hospital_affiliation,
                      max_patient_count: int) -> dict:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO doctors (profile_id, clinic_id, specialization, license_number, "
                    "hospital_affiliation, max_patient_count) VALUES "
                    "(:profile_id, :clinic_id, :specialization, :license_number, :hospital_affiliation, :max_patient_count) "
                    "RETURNING *"
                ),
                {
                    "profile_id": str(profile_id),
                    "clinic_id": str(clinic_id),
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
                text(
                    "SELECT d.*, p.first_name, p.last_name, p.email, p.phone, p.is_active AS profile_is_active "
                    "FROM doctors d JOIN profiles p ON p.id = d.profile_id WHERE d.doctor_id = :id"
                ),
                {"id": str(doctor_id)},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def list(self, *, clinic_id: UUID | None = None) -> list[dict]:
        # profile_is_active is joined in from profiles — doctors has no
        # is_active column of its own (availability_status covers a
        # different concept), and this is the real consent-gate signal the
        # frontend's staff list needs, not something derivable from `doctors` alone.
        # d.deleted_at IS NULL excludes soft-deleted doctors from the active list.
        base = (
            "SELECT d.*, p.first_name, p.last_name, p.email, p.phone, p.is_active AS profile_is_active "
            "FROM doctors d JOIN profiles p ON p.id = d.profile_id WHERE d.deleted_at IS NULL"
        )
        if clinic_id:
            rows = (
                await self.session.execute(text(f"{base} AND d.clinic_id = :clinic_id"), {"clinic_id": str(clinic_id)})
            ).mappings().all()
        else:
            rows = (await self.session.execute(text(base))).mappings().all()
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

    async def soft_delete(self, doctor_id: UUID, *, deleted_by: UUID) -> dict | None:
        doctor = await self.get(doctor_id)
        if not doctor:
            return None
        await self.session.execute(
            text("UPDATE doctors SET deleted_by = :by, deleted_at = NOW() WHERE doctor_id = :id"),
            {"by": str(deleted_by), "id": str(doctor_id)},
        )
        await self.session.execute(
            text("UPDATE clinic_staff_assignments SET is_active = FALSE, removed_at = NOW() WHERE profile_id = :pid AND staff_role = 'doctor' AND is_active = TRUE"),
            {"pid": str(doctor["profile_id"])},
        )
        return doctor

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
        return await fetch_optional(
            self.session,
            text(
                "SELECT ca.*, p.first_name, p.last_name, p.email, p.phone, p.is_active AS profile_is_active FROM clinical_assistants ca "
                "JOIN profiles p ON p.id = ca.profile_id WHERE ca.ca_id = :id"
            ),
            {"id": str(ca_id)},
        )

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
        base = (
            "SELECT ca.*, p.first_name, p.last_name, p.email, p.phone, p.is_active AS profile_is_active "
            "FROM clinical_assistants ca JOIN profiles p ON p.id = ca.profile_id WHERE ca.deleted_at IS NULL"
        )
        if clinic_id:
            rows = (
                await self.session.execute(text(f"{base} AND ca.clinic_id = :clinic_id"), {"clinic_id": str(clinic_id)})
            ).mappings().all()
        else:
            rows = (await self.session.execute(text(base))).mappings().all()
        return [dict(r) for r in rows]

    async def update(self, ca_id: UUID, fields: dict) -> dict | None:
        if not fields:
            return await self.get(ca_id)
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        row = (
            await self.session.execute(
                text(f"UPDATE clinical_assistants SET {set_clause} WHERE ca_id = :id RETURNING *"),
                {**fields, "id": str(ca_id)},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def soft_delete(self, ca_id: UUID, *, deleted_by: UUID) -> dict | None:
        ca = await self.get(ca_id)
        if not ca:
            return None
        await self.session.execute(
            text("UPDATE clinical_assistants SET deleted_by = :by, deleted_at = NOW(), is_active = FALSE WHERE ca_id = :id"),
            {"by": str(deleted_by), "id": str(ca_id)},
        )
        await self.session.execute(
            text("UPDATE clinic_staff_assignments SET is_active = FALSE, removed_at = NOW() WHERE profile_id = :pid AND staff_role = 'clinical_assistant' AND is_active = TRUE"),
            {"pid": str(ca["profile_id"])},
        )
        return ca


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
        base = (
            "SELECT r.*, p.first_name, p.last_name, p.email, p.phone, p.is_active AS profile_is_active "
            "FROM receptionists r JOIN profiles p ON p.id = r.profile_id WHERE r.deleted_at IS NULL"
        )
        if clinic_id:
            rows = (
                await self.session.execute(text(f"{base} AND r.clinic_id = :clinic_id"), {"clinic_id": str(clinic_id)})
            ).mappings().all()
        else:
            rows = (await self.session.execute(text(base))).mappings().all()
        return [dict(r) for r in rows]

    async def get(self, receptionist_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "SELECT r.*, p.first_name, p.last_name, p.email, p.phone, p.is_active AS profile_is_active FROM receptionists r "
                "JOIN profiles p ON p.id = r.profile_id WHERE r.receptionist_id = :id"
            ),
            {"id": str(receptionist_id)},
        )

    async def update(self, receptionist_id: UUID, fields: dict) -> dict | None:
        if not fields:
            return await self.get(receptionist_id)
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        row = (
            await self.session.execute(
                text(f"UPDATE receptionists SET {set_clause} WHERE receptionist_id = :id RETURNING *"),
                {**fields, "id": str(receptionist_id)},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def soft_delete(self, receptionist_id: UUID, *, deleted_by: UUID) -> dict | None:
        receptionist = await self.get(receptionist_id)
        if not receptionist:
            return None
        await self.session.execute(
            text("UPDATE receptionists SET deleted_by = :by, deleted_at = NOW(), is_active = FALSE WHERE receptionist_id = :id"),
            {"by": str(deleted_by), "id": str(receptionist_id)},
        )
        await self.session.execute(
            text("UPDATE clinic_staff_assignments SET is_active = FALSE, removed_at = NOW() WHERE profile_id = :pid AND staff_role = 'receptionist' AND is_active = TRUE"),
            {"pid": str(receptionist["profile_id"])},
        )
        return receptionist


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

    async def list_by_region(self, region_id: str, *, status: str | None = None) -> list[dict]:
        clauses, params = ["c.region_id = :region_id"], {"region_id": region_id}
        if status:
            clauses.append("sr.status = :status")
            params["status"] = status
        where = " AND ".join(clauses)
        rows = (
            await self.session.execute(
                text(
                    f"SELECT sr.* FROM staff_requests sr JOIN clinics c ON c.clinic_id = sr.clinic_id "
                    f"WHERE {where} ORDER BY sr.created_at DESC"
                ),
                params,
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

    async def fulfill(self, request_id: UUID, *, profile_id: UUID) -> None:
        """Links the just-created doctor/CA/receptionist profile back to the
        staff_request it fulfills. status stays 'approved' forever —
        fulfilled_profile_id IS NOT NULL is the real fulfillment signal."""
        await self.session.execute(
            text("UPDATE staff_requests SET fulfilled_profile_id = :pid, fulfilled_at = NOW() WHERE request_id = :id"),
            {"pid": str(profile_id), "id": str(request_id)},
        )
