from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional


class PatientRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_profile_and_patient(self, *, email: str, first_name: str, last_name: str, phone,
                                           gender, dob, address, primary_clinic_id: UUID,
                                           emergency_contact_name, emergency_contact_phone) -> dict:
        profile = await fetch_one(
            self.session,
            text(
                "INSERT INTO profiles (cognito_sub, email, first_name, last_name, phone, role, gender, dob, address) "
                "VALUES ('pending-' || gen_random_uuid()::TEXT, :email, :first_name, :last_name, :phone, "
                "'patient', :gender, :dob, :address) RETURNING *"
            ),
            {"email": email, "first_name": first_name, "last_name": last_name, "phone": phone,
             "gender": gender, "dob": dob, "address": address},
        )
        # mrn is set by the fn_generate_mrn() trigger (SQL/14_triggers.sql) — not passed here.
        patient = await fetch_one(
            self.session,
            text(
                "INSERT INTO patients (profile_id, primary_clinic_id, emergency_contact_name, emergency_contact_phone) "
                "VALUES (:profile_id, :clinic_id, :ec_name, :ec_phone) RETURNING *"
            ),
            {"profile_id": profile["id"], "clinic_id": str(primary_clinic_id),
             "ec_name": emergency_contact_name, "ec_phone": emergency_contact_phone},
        )
        return patient

    async def get(self, patient_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM patients WHERE patient_id = :id"), {"id": str(patient_id)})

    async def get_by_profile_id(self, profile_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM patients WHERE profile_id = :pid"), {"pid": str(profile_id)})

    async def list(self, *, registration_status: str | None = None, clinic_id: UUID | None = None) -> list[dict]:
        clauses, params = [], {}
        if registration_status:
            clauses.append("registration_status = :status")
            params["status"] = registration_status
        if clinic_id:
            clauses.append("primary_clinic_id = :clinic_id")
            params["clinic_id"] = str(clinic_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (await self.session.execute(text(f"SELECT * FROM patients {where} ORDER BY created_at DESC"), params)).mappings().all()
        return [dict(r) for r in rows]

    async def set_status(self, patient_id: UUID, status: str) -> dict | None:
        return await fetch_optional(
            self.session,
            text("UPDATE patients SET registration_status = :status WHERE patient_id = :id RETURNING *"),
            {"status": status, "id": str(patient_id)},
        )

    async def complete_registration(self, patient_id: UUID, doctor_id: UUID | None) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE patients SET registration_status = 'registration_complete', "
                "registration_completed_at = NOW(), primary_doctor_id = :doctor_id "
                "WHERE patient_id = :id RETURNING *"
            ),
            {"doctor_id": str(doctor_id) if doctor_id else None, "id": str(patient_id)},
        )


class DiseaseSelectionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, patient_profile_id: UUID, disease_id, disease_unknown: bool, is_primary: bool) -> dict:
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO patient_disease_selection (patient_id, disease_id, disease_unknown, is_primary) "
                "VALUES (:patient_id, :disease_id, :disease_unknown, :is_primary) RETURNING *"
            ),
            {"patient_id": str(patient_profile_id), "disease_id": disease_id,
             "disease_unknown": disease_unknown, "is_primary": is_primary},
        )

    async def list_for_patient(self, patient_profile_id: UUID) -> list[dict]:
        rows = (
            await self.session.execute(
                text("SELECT * FROM patient_disease_selection WHERE patient_id = :pid"), {"pid": str(patient_profile_id)}
            )
        ).mappings().all()
        return [dict(r) for r in rows]


class DoctorPatientAssignmentRepository:
    """Owned by `clinical` module once it exists (Stage 8) — created here early
    because doctor auto-allocation (Master Doc Flow M) is triggered at the
    moment registration completes, which is this module's responsibility.
    Don't duplicate this repository in clinical/ later; import from here or
    move it wholesale when clinical/ lands."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, doctor_id: UUID, patient_id: UUID, clinic_id: UUID) -> dict:
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO doctor_patient_assignments (doctor_id, patient_id, clinic_id) "
                "VALUES (:doctor_id, :patient_id, :clinic_id) RETURNING *"
            ),
            {"doctor_id": str(doctor_id), "patient_id": str(patient_id), "clinic_id": str(clinic_id)},
        )

    async def end_active(self, *, patient_id: UUID, clinic_id: UUID) -> None:
        await self.session.execute(
            text(
                "UPDATE doctor_patient_assignments SET status = 'transferred', ended_at = NOW() "
                "WHERE patient_id = :pid AND clinic_id = :cid AND status = 'active'"
            ),
            {"pid": str(patient_id), "cid": str(clinic_id)},
        )


class PatientTransferRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        from app.core.sql_helpers import insert_returning

        sql, params = insert_returning("patient_clinic_transfers", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, pct_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM patient_clinic_transfers WHERE pct_id = :id"), {"id": str(pct_id)})

    async def set_status(self, pct_id: UUID, *, status: str, to_doctor_id=None, consent_id=None) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE patient_clinic_transfers SET status = :status, "
                "to_doctor_id = COALESCE(:to_doctor_id, to_doctor_id), consent_id = COALESCE(:consent_id, consent_id) "
                "WHERE pct_id = :id RETURNING *"
            ),
            {"status": status, "to_doctor_id": str(to_doctor_id) if to_doctor_id else None,
             "consent_id": str(consent_id) if consent_id else None, "id": str(pct_id)},
        )
