from __future__ import annotations

import builtins
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_session import sign_out_profile
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
        registered_by: UUID | None = None,
        signup_ip: str | None = None,
        guardian_name: str | None = None,
        guardian_relationship: str | None = None,
        guardian_contact: str | None = None,
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
                "self_registered, approval_status, registered_by, signup_ip, guardian_name, guardian_relationship, guardian_contact) "
                "VALUES (:profile_id, :clinic_id, :ec_name, :ec_phone, :self_registered, :approval_status, :registered_by, "
                "CAST(:signup_ip AS INET), :guardian_name, :guardian_relationship, :guardian_contact) RETURNING *"
            ),
            {
                "profile_id": profile["id"],
                "clinic_id": str(primary_clinic_id),
                "ec_name": emergency_contact_name,
                "ec_phone": emergency_contact_phone,
                "self_registered": self_registered,
                "approval_status": approval_status,
                "registered_by": str(registered_by) if registered_by else None,
                "signup_ip": signup_ip,
                "guardian_name": guardian_name,
                "guardian_relationship": guardian_relationship,
                "guardian_contact": guardian_contact,
            },
        )
        # Merge in the profile fields we already have in hand — avoids a
        # second round-trip just to get what create() already fetched.
        # cognito_sub included so callers (e.g. the public self-registration
        # endpoint) can mint a login token immediately without a re-query.
        clinic = await fetch_optional(
            self.session,
            text("SELECT clinic_name, city FROM clinics WHERE clinic_id = :cid"),
            {"cid": str(primary_clinic_id)},
        )
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
            "clinic_name": clinic["clinic_name"] if clinic else None,
            "clinic_city": clinic["city"] if clinic else None,
        }

    _SELECT_WITH_PROFILE = (
        "SELECT pt.*, p.first_name, p.last_name, p.email, p.phone, p.gender, p.dob, p.address, "
        "p.city, p.state, p.country, p.pincode, p.language_pref, "
        "p.email_verified, p.phone_verified, "
        "p.is_active AS profile_is_active, "
        "dp.first_name AS doctor_first_name, dp.last_name AS doctor_last_name, "
        "dp.first_name || ' ' || dp.last_name AS doctor_name, "
        "dp.phone AS doctor_phone, dd.specialization AS doctor_specialization, "
        "cl.clinic_name AS clinic_name, cl.city AS clinic_city, "
        # 'System' = approved by the registration checks, not a person (105).
        "CASE WHEN pt.approval_method = 'auto' THEN 'System' ELSE ap.first_name || ' ' || ap.last_name END AS approved_by_name, "
        # Real-time, not the daily-batch patients.last_clinical_contact_at
        # (app/workers/retention_purge.py) — a doctor needs today's completed
        # visit to show up immediately, not after tomorrow's worker run.
        "(SELECT max(a.appointment_date) FROM appointments a "
        " WHERE a.patient_id = pt.profile_id AND a.status = 'completed') AS last_visit_date "
        "FROM patients pt JOIN profiles p ON p.id = pt.profile_id "
        "LEFT JOIN profiles dp ON dp.id = pt.primary_doctor_id "
        "LEFT JOIN doctors dd ON dd.profile_id = pt.primary_doctor_id "
        "LEFT JOIN clinics cl ON cl.clinic_id = pt.primary_clinic_id "
        "LEFT JOIN profiles ap ON ap.id = pt.approved_by"
    )

    async def get(self, patient_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text(f"{self._SELECT_WITH_PROFILE} WHERE pt.patient_id = :id"), {"id": str(patient_id)})

    async def get_by_profile_id(self, profile_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text(f"{self._SELECT_WITH_PROFILE} WHERE pt.profile_id = :pid"), {"pid": str(profile_id)})

    async def get_clinic(self, patient_id: UUID) -> dict | None:
        """The patient's primary clinic row. c.* rather than named columns so
        this still works on a database without 109's columns (the response
        schema defaults them to null)."""
        return await fetch_optional(
            self.session,
            text("SELECT c.* FROM patients pt JOIN clinics c ON c.clinic_id = pt.primary_clinic_id WHERE pt.patient_id = :id"),
            {"id": str(patient_id)},
        )

    def _list_where(
        self,
        *,
        registration_status: str | None = None,
        approval_status: str | None = None,
        clinic_id: UUID | None = None,
        profile_id: UUID | None = None,
        include_unapproved: bool = False,
        self_registered: bool | None = None,
        search: str | None = None,
        created_today: bool = False,
        gender: str | None = None,
        doctor_name: str | None = None,
        hide_unfinished_pending: bool = False,
    ) -> tuple[str, dict]:
        """WHERE clause shared by list(), list_page() and count() so paged
        and full reads can never disagree on which patients exist."""
        # pt.deleted_at IS NULL — soft-deleted patients (see delete() below)
        # never show up in the active list, but the row is never removed.
        clauses: builtins.list[str] = ["pt.deleted_at IS NULL"]
        params: dict = {}
        # A self-registration still awaiting a receptionist ('pending') or
        # turned down ('rejected') is a request, not a patient — it belongs in
        # the approvals queue, not in every patient list (doctor, CA, reception,
        # admin). Shown only when the caller asks for an approval status
        # explicitly, looks up its own record (a patient mid-wizard is still
        # 'pending'), or opts in (the approvals queue's "all" view).
        if not approval_status and not profile_id and not include_unapproved:
            clauses.append("pt.approval_status NOT IN ('pending', 'rejected')")
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
        if self_registered is not None:
            clauses.append("pt.self_registered = :self_registered")
            params["self_registered"] = self_registered
        if search:
            # name / phone / email / MRN / assigned doctor — what the reception
            # list and booking-modal pickers used to match client-side.
            clauses.append(
                "((p.first_name || ' ' || p.last_name) ILIKE :search OR p.phone ILIKE :search OR p.email ILIKE :search "
                "OR pt.mrn ILIKE :search OR (dp.first_name || ' ' || dp.last_name) ILIKE :search)"
            )
            params["search"] = f"%{search.strip()}%"
        if gender:
            clauses.append("lower(p.gender) = lower(:gender)")
            params["gender"] = gender
        if doctor_name:
            clauses.append("(dp.first_name || ' ' || dp.last_name) = :doctor_name")
            params["doctor_name"] = doctor_name
        if hide_unfinished_pending:
            # Self-registration still mid-wizard: not yet a request anyone can act on.
            clauses.append("NOT (pt.approval_status = 'pending' AND pt.registration_status <> 'registration_complete')")
        if created_today:
            # Clinic day = IST, same as scheduling's _now_ist_naive.
            clauses.append("(pt.created_at AT TIME ZONE 'Asia/Kolkata')::date = (now() AT TIME ZONE 'Asia/Kolkata')::date")
        return f"WHERE {' AND '.join(clauses)}", params

    async def list(self, **filters) -> list[dict]:
        where, params = self._list_where(**filters)
        rows = (
            (await self.session.execute(text(f"{self._SELECT_WITH_PROFILE} {where} ORDER BY pt.created_at DESC"), params)).mappings().all()
        )
        return [dict(r) for r in rows]

    async def count(self, **filters) -> int:
        where, params = self._list_where(**filters)
        sql = (
            "SELECT count(*) FROM patients pt JOIN profiles p ON p.id = pt.profile_id "
            f"LEFT JOIN profiles dp ON dp.id = pt.primary_doctor_id {where}"
        )
        return int((await self.session.execute(text(sql), params)).scalar() or 0)

    async def list_page(self, *, limit: int, offset: int, **filters) -> tuple[builtins.list[dict], int]:
        """One page in SQL plus the total — list() reads every row, which the
        reception lists used to do on every page request (API audit F-020)."""
        total = await self.count(**filters)
        if total == 0 or limit <= 0 or offset >= total:
            return [], total
        where, params = self._list_where(**filters)
        rows = (
            (
                await self.session.execute(
                    text(f"{self._SELECT_WITH_PROFILE} {where} ORDER BY pt.created_at DESC LIMIT :limit OFFSET :offset"),
                    {**params, "limit": limit, "offset": offset},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows], total

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
        # A removed patient must not be able to mint new tokens either.
        await sign_out_profile(self.session, patient["profile_id"])
        return patient

    async def set_status(self, patient_id: UUID, status: str) -> dict | None:
        return await fetch_optional(
            self.session,
            text("UPDATE patients SET registration_status = :status WHERE patient_id = :id RETURNING *"),
            {"status": status, "id": str(patient_id)},
        )

    async def set_approval(
        self,
        patient_id: UUID,
        *,
        approval_status: str,
        decided_by: UUID | None,
        rejection_reason: str | None,
        method: str | None = None,
    ) -> dict | None:
        """approved_by/approved_at and rejected_by/rejected_at (94) are each
        written only by their own decision — approving never touches the
        reject columns and vice versa — so a patient rejected once and later
        approved keeps both halves of that history instead of one
        overwriting the other. rejection_reason is cleared on approval: it
        describes the CURRENT rejection, not a past one a re-approval just
        superseded. method (105) says who approved: 'manual' = decided_by,
        'auto' = the registration checks, with decided_by empty."""
        if approval_status == "approved":
            return await fetch_optional(
                self.session,
                text(
                    "UPDATE patients SET approval_status = 'approved', approval_method = :method, approved_by = :decided_by, "
                    "approved_at = NOW(), rejection_reason = NULL WHERE patient_id = :id RETURNING *"
                ),
                {"method": method, "decided_by": str(decided_by) if decided_by else None, "id": str(patient_id)},
            )
        return await fetch_optional(
            self.session,
            text(
                "UPDATE patients SET approval_status = 'rejected', rejected_by = :decided_by, "
                "rejected_at = NOW(), rejection_reason = :reason WHERE patient_id = :id RETURNING *"
            ),
            {
                "decided_by": str(decided_by) if decided_by else None,
                "reason": rejection_reason,
                "id": str(patient_id),
            },
        )

    async def set_risk_flags(self, patient_id: UUID, flags: builtins.list[str]) -> None:
        await self.session.execute(
            text("UPDATE patients SET risk_flags = CAST(:flags AS JSONB) WHERE patient_id = :id"),
            {"flags": json.dumps(flags), "id": str(patient_id)},
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


class PrescribedMedicineRepository:
    """core.prescribed_medicines (96). Stopped, never deleted."""

    _SELECT = (
        "SELECT m.*, pp.first_name || ' ' || pp.last_name AS prescribed_by_name "
        "FROM prescribed_medicines m LEFT JOIN profiles pp ON pp.id = m.prescribed_by "
    )

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_patient(self, patient_profile_id: UUID) -> list[dict]:
        rows = (
            (
                await self.session.execute(
                    text(self._SELECT + "WHERE m.patient_id = :pid ORDER BY m.status, m.started_at DESC"),
                    {"pid": str(patient_profile_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def get(self, medicine_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text(self._SELECT + "WHERE m.medicine_id = :id"), {"id": str(medicine_id)})

    async def create(self, data: dict) -> dict:
        row = await fetch_one(
            self.session,
            text(
                "INSERT INTO prescribed_medicines (patient_id, clinic_id, prescribed_by, appointment_id, medicine_name, "
                "dose, timing, meal_instruction, duration, note) VALUES (:patient_id, :clinic_id, :prescribed_by, "
                ":appointment_id, :medicine_name, :dose, :timing, :meal_instruction, :duration, :note) RETURNING medicine_id"
            ),
            data,
        )
        return await self.get(row["medicine_id"])  # type: ignore[return-value]

    async def set_status(self, medicine_id: UUID, *, status: str, changed_by: UUID) -> dict | None:
        await self.session.execute(
            text(
                "UPDATE prescribed_medicines SET status = :status, "
                "stopped_at = CASE WHEN :status = 'stopped' THEN now() ELSE NULL END, "
                "stopped_by = CASE WHEN :status = 'stopped' THEN CAST(:by AS uuid) ELSE NULL END "
                "WHERE medicine_id = :id"
            ),
            {"status": status, "by": str(changed_by), "id": str(medicine_id)},
        )
        return await self.get(medicine_id)


class PatientClinicalNoteRepository:
    """core.patient_clinical_notes (99). Append-only — no update/delete."""

    _SELECT = (
        "SELECT n.*, dp.first_name || ' ' || dp.last_name AS doctor_name "
        "FROM patient_clinical_notes n LEFT JOIN profiles dp ON dp.id = n.doctor_id "
    )

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_patient(self, patient_profile_id: UUID) -> list[dict]:
        rows = (
            (
                await self.session.execute(
                    text(self._SELECT + "WHERE n.patient_id = :pid ORDER BY n.created_at DESC"),
                    {"pid": str(patient_profile_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def get(self, note_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text(self._SELECT + "WHERE n.note_id = :id"), {"id": str(note_id)})

    async def create(self, data: dict) -> dict:
        row = await fetch_one(
            self.session,
            text(
                "INSERT INTO patient_clinical_notes (patient_id, doctor_id, appointment_id, category, note_text) "
                "VALUES (:patient_id, :doctor_id, :appointment_id, :category, :note_text) RETURNING note_id"
            ),
            data,
        )
        return await self.get(row["note_id"])  # type: ignore[return-value]
