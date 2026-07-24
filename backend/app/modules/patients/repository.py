from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional


class PatientRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_profile_and_patient(
        self,
        *,
        email: str,
        first_name: str,
        last_name: str,
        phone,
        gender,
        dob,
        address,
        primary_clinic_id: UUID,
        emergency_contact_name,
        emergency_contact_phone,
        city=None,
        state=None,
        country=None,
        pincode=None,
        self_registered: bool = False,
        approval_status: str = "not_required",
        cognito_sub: str | None = None,
    ) -> dict:
        # is_active = FALSE — gated until the patient signs the
        # patient_onboarding consent (see consent/service.py ConsentRecordService.sign),
        # or (self-registered) until a receptionist approves (patients.approval_status).
        # consent_signed = FALSE alongside it — separate column, see
        # SQL/28_consent_redesign.sql; sign() flips this one directly but
        # is_active for patients stays gated behind the rest of registration.
        # Anonymous self-registration has no RLS context at all — the INSERT's
        # own WITH CHECK allows it (see SQL/38), but INSERT ... RETURNING also
        # needs the SELECT policy to allow seeing the new row, and
        # rls_profiles_select has nothing to match an anonymous caller against
        # yet (no id/cognito_sub/email GUC was ever set for this flow). Same
        # self-lookup-right-before-the-query pattern as the login-by-email fix
        # (SQL/33) — set app.current_email to the row we're about to create,
        # which rls_profiles_select's existing `email = rls_email()` clause
        # already covers.
        # cognito_sub: real value already resolved by the caller (the OTP
        # signup wizard, patients/router.py) when auth_mode == "cognito";
        # None here falls back to the local-dev placeholder below.
        await self.session.execute(text("SELECT set_config('app.current_email', :email, true)"), {"email": email})
        profile = await fetch_one(
            self.session,
            text(
                "INSERT INTO profiles (cognito_sub, email, first_name, last_name, phone, role, gender, dob, address, "
                "city, state, country, pincode, is_active, consent_signed) "
                "VALUES (COALESCE(:cognito_sub, 'pending-' || gen_random_uuid()::TEXT), :email, :first_name, :last_name, :phone, "
                "'patient', :gender, :dob, :address, :city, :state, :country, :pincode, FALSE, FALSE) RETURNING *"
            ),
            {
                "cognito_sub": cognito_sub,
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "phone": phone,
                "gender": gender,
                "dob": dob,
                "address": address,
                "city": city,
                "state": state,
                "country": country,
                "pincode": pincode,
            },
        )
        # Same reason, for the patients insert's own RETURNING (rls_patients_
        # select's `profile_id = rls_user_id()` clause) and for
        # create_onboarding_consent's consent_records insert right after this
        # method returns, still the same transaction (rls_cr_select's
        # `patient_id = rls_user_id()` clause) — one GUC, set once, covers both.
        await self.session.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": str(profile["id"])})
        # mrn is set by the fn_generate_mrn() trigger (SQL/14_triggers.sql) — not passed here.
        patient = await fetch_one(
            self.session,
            text(
                "INSERT INTO patients (profile_id, primary_clinic_id, emergency_contact_name, emergency_contact_phone, "
                "self_registered, approval_status) "
                "VALUES (:profile_id, :clinic_id, :ec_name, :ec_phone, :self_registered, :approval_status) RETURNING *"
            ),
            {
                "profile_id": profile["id"],
                "clinic_id": str(primary_clinic_id),
                "ec_name": emergency_contact_name,
                "ec_phone": emergency_contact_phone,
                "self_registered": self_registered,
                "approval_status": approval_status,
            },
        )
        # Merge in the profile fields we already have in hand — avoids a
        # second round-trip just to get what create() already fetched.
        # cognito_sub included so callers (e.g. the public self-registration
        # endpoint) can mint a login token immediately without a re-query.
        return {
            **patient,
            "first_name": profile["first_name"],
            "last_name": profile["last_name"],
            "email": profile["email"],
            "phone": profile["phone"],
            "gender": profile["gender"],
            "dob": profile["dob"],
            "address": profile["address"],
            "profile_is_active": profile["is_active"],
            "cognito_sub": profile["cognito_sub"],
        }

    _SELECT_WITH_PROFILE = (
        "SELECT pt.*, p.first_name, p.last_name, p.email, p.phone, p.gender, p.dob, p.address, "
        "p.city, p.state, p.country, p.pincode, "
        "p.is_active AS profile_is_active, "
        "dp.first_name AS doctor_first_name, dp.last_name AS doctor_last_name, "
        "dp.first_name || ' ' || dp.last_name AS doctor_name, "
        "dp.phone AS doctor_phone, dd.specialization AS doctor_specialization "
        "FROM patients pt JOIN profiles p ON p.id = pt.profile_id "
        "LEFT JOIN profiles dp ON dp.id = pt.primary_doctor_id "
        "LEFT JOIN doctors dd ON dd.profile_id = pt.primary_doctor_id"
    )

    async def get(self, patient_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text(f"{self._SELECT_WITH_PROFILE} WHERE pt.patient_id = :id"), {"id": str(patient_id)})

    async def get_by_profile_id(self, profile_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text(f"{self._SELECT_WITH_PROFILE} WHERE pt.profile_id = :pid"), {"pid": str(profile_id)})

    async def list(
        self,
        *,
        registration_status: str | None = None,
        approval_status: str | None = None,
        clinic_id: UUID | None = None,
        profile_id: UUID | None = None,
    ) -> list[dict]:
        # pt.deleted_at IS NULL — soft-deleted patients (see delete() below)
        # never show up in the active list, but the row is never removed.
        clauses, params = ["pt.deleted_at IS NULL"], {}
        if registration_status:
            clauses.append("pt.registration_status = :status")
            params["status"] = registration_status
        if approval_status:
            clauses.append("pt.approval_status = :approval_status")
            params["approval_status"] = approval_status
        if clinic_id:
            clauses.append("pt.primary_clinic_id = :clinic_id")
            params["clinic_id"] = str(clinic_id)
        if profile_id:
            clauses.append("pt.profile_id = :profile_id")
            params["profile_id"] = str(profile_id)
        where = f"WHERE {' AND '.join(clauses)}"
        rows = (
            (await self.session.execute(text(f"{self._SELECT_WITH_PROFILE} {where} ORDER BY pt.created_at DESC"), params)).mappings().all()
        )
        return [dict(r) for r in rows]

    async def update(self, patient_id: UUID, *, profile_fields: dict, patient_fields: dict) -> dict | None:
        patient = await self.get(patient_id)
        if not patient:
            return None
        if profile_fields:
            set_clause = ", ".join(f"{k} = :{k}" for k in profile_fields)
            await self.session.execute(
                text(f"UPDATE profiles SET {set_clause} WHERE id = :pid"),
                {**profile_fields, "pid": str(patient["profile_id"])},
            )
        if patient_fields:
            set_clause = ", ".join(f"{k} = :{k}" for k in patient_fields)
            await self.session.execute(
                text(f"UPDATE patients SET {set_clause} WHERE patient_id = :id"),
                {**patient_fields, "id": str(patient_id)},
            )
        return await self.get(patient_id)

    async def soft_delete(self, patient_id: UUID, *, deleted_by: UUID) -> dict | None:
        """Never a real DELETE — PHI records are retained permanently (see
        the table comment in SQL/04_patient_tables.sql). Marks both the
        patient row and the profile deleted/inactive so they disappear from
        active lists and can't log in, without losing any clinical history."""
        patient = await self.get(patient_id)
        if not patient:
            return None
        await self.session.execute(
            text("UPDATE patients SET deleted_by = :by, deleted_at = NOW() WHERE patient_id = :id"),
            {"by": str(deleted_by), "id": str(patient_id)},
        )
        await self.session.execute(
            text("UPDATE profiles SET deleted_by = :by, deleted_at = NOW(), is_active = FALSE WHERE id = :pid"),
            {"by": str(deleted_by), "pid": str(patient["profile_id"])},
        )
        return patient

    async def set_status(self, patient_id: UUID, status: str) -> dict | None:
        return await fetch_optional(
            self.session,
            text("UPDATE patients SET registration_status = :status WHERE patient_id = :id RETURNING *"),
            {"status": status, "id": str(patient_id)},
        )

    async def set_approval(
        self, patient_id: UUID, *, approval_status: str, approved_by: UUID | None, rejection_reason: str | None
    ) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE patients SET approval_status = :status, approved_by = :approved_by, "
                "approved_at = NOW(), rejection_reason = :reason WHERE patient_id = :id RETURNING *"
            ),
            {
                "status": approval_status,
                "approved_by": str(approved_by) if approved_by else None,
                "reason": rejection_reason,
                "id": str(patient_id),
            },
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
            {"patient_id": str(patient_profile_id), "disease_id": disease_id, "disease_unknown": disease_unknown, "is_primary": is_primary},
        )

    async def list_for_patient(self, patient_profile_id: UUID) -> list[dict]:
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM patient_disease_selection WHERE patient_id = :pid"), {"pid": str(patient_profile_id)}
                )
            )
            .mappings()
            .all()
        )
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
            {
                "status": status,
                "to_doctor_id": str(to_doctor_id) if to_doctor_id else None,
                "consent_id": str(consent_id) if consent_id else None,
                "id": str(pct_id),
            },
        )
