from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.modules.patients.repository import (
    DiseaseSelectionRepository,
    DoctorPatientAssignmentRepository,
    PatientRepository,
    PatientTransferRepository,
)
from app.modules.staff.service import DoctorService

# Registration status machine (Master Doc Section 6.2 / SQL/04_patient_tables.sql CHECK constraint)
_REGISTRATION_STEPS = [
    "demographics_complete", "disease_selected", "consent_signed",
    "anamnesis_complete", "general_prs_complete", "registration_complete",
]


class PatientService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = PatientRepository(session)
        self.disease_repo = DiseaseSelectionRepository(session)
        self.assignments = DoctorPatientAssignmentRepository(session)

    async def register(self, data: dict) -> dict:
        try:
            patient = await self.repo.create_profile_and_patient(
                email=data["email"], first_name=data["first_name"], last_name=data["last_name"],
                phone=data.get("phone"), gender=data.get("gender"), dob=data.get("dob"),
                address=data.get("address"), primary_clinic_id=data["primary_clinic_id"],
                emergency_contact_name=data.get("emergency_contact_name"),
                emergency_contact_phone=data.get("emergency_contact_phone"),
            )
        except IntegrityError as exc:
            raise ConflictError(f"Email {data['email']!r} already in use", code="EMAIL_ALREADY_EXISTS") from exc
        await emit_event(
            self.session, aggregate_type="patient", aggregate_id=patient["patient_id"],
            event_type="patient_registered", payload={"patient_id": str(patient["patient_id"]), "mrn": patient["mrn"]},
        )
        return patient

    async def get(self, patient_id: UUID) -> dict:
        patient = await self.repo.get(patient_id)
        if not patient:
            raise NotFoundError("Patient not found", code="PATIENT_NOT_FOUND")
        return patient

    async def list(self, **filters) -> list[dict]:
        return await self.repo.list(**filters)

    async def select_disease(self, patient_id: UUID, *, disease_id, disease_unknown: bool, is_primary: bool) -> dict:
        patient = await self.get(patient_id)
        if not disease_id and not disease_unknown:
            raise BusinessRuleError("Either disease_id or disease_unknown must be set", code="DISEASE_SELECTION_REQUIRED")
        selection = await self.disease_repo.create(
            patient_profile_id=patient["profile_id"], disease_id=disease_id,
            disease_unknown=disease_unknown, is_primary=is_primary,
        )
        await emit_event(
            self.session, aggregate_type="patient", aggregate_id=patient_id,
            event_type="disease_selected", payload={"patient_id": str(patient_id), "disease_id": disease_id},
        )
        await self.advance_registration_status(patient_id)
        return selection

    async def advance_registration_status(self, patient_id: UUID) -> dict:
        """Re-derives registration_status from scratch by checking every
        dependency (Master Doc table 8, step 6: 'System validates all steps
        complete'). Idempotent and self-healing — safe to call after any step,
        in any order, rather than requiring each module to know what status
        string to push next."""
        patient = await self.get(patient_id)
        profile_id = patient["profile_id"]

        has_disease = bool(await self.disease_repo.list_for_patient(profile_id))
        has_signed_onboarding_consent = await self._exists(
            "SELECT 1 FROM consent_records WHERE patient_id = :pid AND consent_type = 'patient_onboarding' AND status = 'signed'",
            {"pid": str(profile_id)},
        )
        has_completed_anamnesis = await self._exists(
            "SELECT 1 FROM anamnesis_assessments WHERE patient_id = :pid AND status = 'completed'", {"pid": str(profile_id)}
        )
        has_completed_general_prs = await self._exists(
            "SELECT 1 FROM prs_assessment_instances WHERE patient_id = :pid AND assessment_stage = 'general_registration' AND status = 'completed'",
            {"pid": str(profile_id)},
        )

        # general_prs_complete has no distinct observable window: per Master Doc
        # step 6 ("System validates all steps complete"), registration_complete
        # fires automatically and immediately once PRS finishes — there's no
        # separate gate between the two, so this status value is skipped over
        # rather than persisted as its own row state. Not a bug; there's simply
        # nothing that would ever read a patient sitting in that state.
        if has_completed_general_prs and has_completed_anamnesis and has_signed_onboarding_consent and has_disease:
            return await self._complete_registration(patient_id, patient)
        if has_completed_anamnesis and has_signed_onboarding_consent and has_disease:
            new_status = "anamnesis_complete"
        elif has_signed_onboarding_consent and has_disease:
            new_status = "consent_signed"
        elif has_disease:
            new_status = "disease_selected"
        else:
            new_status = "demographics_complete"

        if new_status != patient["registration_status"]:
            patient = await self.repo.set_status(patient_id, new_status)  # type: ignore[assignment]
        return patient

    async def _exists(self, sql: str, params: dict) -> bool:
        row = (await self.session.execute(text(f"SELECT EXISTS ({sql}) AS e"), params)).mappings().one()
        return row["e"]

    async def _complete_registration(self, patient_id: UUID, patient: dict) -> dict:
        if patient["registration_status"] == "registration_complete":
            return patient
        # Doctor auto-allocation (Master Doc Flow M) — load-balanced pick via
        # the view-based query (staff module), not a counter column.
        doctors = DoctorService(self.session)
        doctor = await doctors.pick_least_loaded(patient["primary_clinic_id"])
        if not doctor:
            raise BusinessRuleError(
                "No available doctor at this clinic to auto-allocate", code="NO_AVAILABLE_DOCTOR"
            )
        # doctors.profile_id, not doctors.doctor_id — every FK to "doctor"
        # elsewhere in the schema (patients.primary_doctor_id,
        # doctor_patient_assignments.doctor_id) points at profiles(id) via
        # doctors.profile_id, not the doctors table's own PK. Real bug hit
        # during Stage 6 testing (ForeignKeyViolationError) — same class of
        # mistake as the patients.patient_id vs profiles.id confusion above.
        await self.repo.complete_registration(patient_id, doctor["profile_id"])
        await self.assignments.create(doctor_id=doctor["profile_id"], patient_id=patient["profile_id"], clinic_id=patient["primary_clinic_id"])
        updated = await self.repo.get(patient_id)
        await emit_event(
            self.session, aggregate_type="patient", aggregate_id=patient_id,
            event_type="registration_completed", payload={"patient_id": str(patient_id)},
        )
        await emit_event(
            self.session, aggregate_type="patient", aggregate_id=patient_id,
            event_type="doctor_auto_allocated", payload={"patient_id": str(patient_id), "doctor_id": str(doctor["doctor_id"])},
        )
        return updated  # type: ignore[return-value]


class FollowUpService:
    """Master Doc Section 6.6 — a follow-up is a new treatment_cycles record
    (cycle_type='followup', cycle_number incremented), same S1-S4 machinery as
    the initial block (built in clinical/, Stage 8) — reused here, not
    reimplemented. Requires the patient's previous cycle to be completed."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = PatientRepository(session)

    async def start(self, patient_id: UUID, *, doctor_id: UUID | None) -> dict:
        from app.modules.clinical.repository import TreatmentCycleRepository
        from app.modules.clinical.service import TreatmentCycleService

        patient = await self.repo.get(patient_id)
        if not patient:
            raise NotFoundError("Patient not found", code="PATIENT_NOT_FOUND")
        if patient["registration_status"] != "registration_complete":
            raise BusinessRuleError("Patient has not completed initial registration", code="REGISTRATION_INCOMPLETE")

        cycle_repo = TreatmentCycleRepository(self.session)
        previous_cycles = await cycle_repo.list(patient_id=patient["profile_id"])
        if any(c["status"] == "in_progress" for c in previous_cycles):
            raise BusinessRuleError("Patient already has an active treatment cycle", code="ACTIVE_CYCLE_EXISTS")

        next_number = max((c["cycle_number"] for c in previous_cycles), default=0) + 1
        # doctor_id here is doctors.doctor_id (or None to keep the current
        # primary doctor) — TreatmentCycleService.create resolves it the same
        # way an initial cycle does, no special-casing needed for follow-up.
        doctor_arg = doctor_id
        if doctor_arg is None:
            doctor_row = await self._doctor_by_profile_id(patient["primary_doctor_id"])
            doctor_arg = doctor_row["doctor_id"] if doctor_row else None
            if doctor_arg is None:
                raise BusinessRuleError("No doctor_id provided and patient has no primary doctor on file", code="DOCTOR_REQUIRED")

        cycle = await TreatmentCycleService(self.session).create({
            "patient_id": patient_id, "doctor_id": doctor_arg, "clinic_id": patient["primary_clinic_id"],
            "cycle_type": "followup", "cycle_number": next_number,
        })
        await emit_event(
            self.session, aggregate_type="treatment_cycle", aggregate_id=cycle["cycle_id"],
            event_type="followup_block_created", payload={"cycle_id": str(cycle["cycle_id"]), "patient_id": str(patient_id)},
        )
        return cycle

    async def _doctor_by_profile_id(self, profile_id):
        from sqlalchemy import text as _text

        row = (await self.session.execute(_text("SELECT * FROM doctors WHERE profile_id = :pid"), {"pid": str(profile_id)})).mappings().first()
        return dict(row) if row else None


class PatientTransferService:
    """Master Doc Section 6.8 / 5.5 — clinic closure transfers and patient
    relocation transfers. Both share the same mechanics: consent required,
    auto doctor allocation at the new clinic, active cycle carries over
    without restart (updated in place, never recreated)."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = PatientTransferRepository(session)
        self.patients = PatientRepository(session)

    async def initiate(self, patient_id: UUID, data: dict, *, initiated_by: UUID) -> dict:
        patient = await self.patients.get(patient_id)
        if not patient:
            raise NotFoundError("Patient not found", code="PATIENT_NOT_FOUND")

        from app.modules.clinical.repository import TreatmentCycleRepository

        active_cycle = await TreatmentCycleRepository(self.session).get_active_for_patient(patient["profile_id"])

        payload = {
            "patient_id": str(patient["profile_id"]), "from_clinic_id": str(patient["primary_clinic_id"]),
            "to_clinic_id": str(data["to_clinic_id"]), "from_doctor_id": str(patient["primary_doctor_id"]) if patient["primary_doctor_id"] else None,
            "transfer_reason": data["transfer_reason"],
            "active_cycle_id": str(active_cycle["cycle_id"]) if active_cycle else None,
            "initiated_by": str(initiated_by), "notes": data.get("notes"),
        }
        transfer = await self.repo.create(payload)
        await emit_event(
            self.session, aggregate_type="patient_clinic_transfer", aggregate_id=transfer["pct_id"],
            event_type="relocation_initiated" if data["transfer_reason"] == "patient_relocation" else "patient_transfer_initiated",
            payload={"pct_id": str(transfer["pct_id"]), "patient_id": str(patient_id)},
        )
        return transfer

    async def get(self, pct_id: UUID) -> dict:
        transfer = await self.repo.get(pct_id)
        if not transfer:
            raise NotFoundError("Transfer not found", code="TRANSFER_NOT_FOUND")
        return transfer

    async def complete(self, pct_id: UUID, *, consent_id: UUID) -> dict:
        """Requires the relocation/transfer consent to already be signed
        (Master Doc: 'patient_relocation_transfer consent must be signed
        before any records transfer' — checked here, not assumed)."""
        transfer = await self.get(pct_id)
        if transfer["status"] not in ("pending", "consented"):
            raise BusinessRuleError(f"Transfer already {transfer['status']}", code="TRANSFER_ALREADY_DECIDED")

        from sqlalchemy import text as _text

        consent = (await self.session.execute(_text("SELECT * FROM consent_records WHERE consent_id = :id"), {"id": str(consent_id)})).mappings().first()
        if not consent or consent["status"] != "signed":
            raise BusinessRuleError("Transfer requires a signed consent record", code="CONSENT_NOT_SIGNED")

        # Auto doctor allocation at the new clinic — mandatory, no exceptions
        # (Master Doc: "Auto doctor allocation at new clinic is mandatory").
        doctor = await DoctorService(self.session).pick_least_loaded(transfer["to_clinic_id"])
        if not doctor:
            raise BusinessRuleError("No available doctor at the receiving clinic", code="NO_AVAILABLE_DOCTOR")

        patients_by_profile = await self.session.execute(
            _text("SELECT patient_id FROM patients WHERE profile_id = :pid"), {"pid": transfer["patient_id"]}
        )
        patient_row = patients_by_profile.mappings().first()

        await self.session.execute(
            _text("UPDATE patients SET primary_clinic_id = :clinic_id, primary_doctor_id = :doctor_id WHERE profile_id = :pid"),
            {"clinic_id": transfer["to_clinic_id"], "doctor_id": doctor["profile_id"], "pid": transfer["patient_id"]},
        )

        if transfer["active_cycle_id"]:
            # Active block carries over WITHOUT restart — same cycle row,
            # just repointed to the new clinic/doctor (Master Doc: "Block
            # resumes from current session. NO RESTART.").
            await self.session.execute(
                _text("UPDATE treatment_cycles SET clinic_id = :clinic_id, doctor_id = :doctor_id WHERE cycle_id = :cycle_id"),
                {"clinic_id": transfer["to_clinic_id"], "doctor_id": doctor["profile_id"], "cycle_id": transfer["active_cycle_id"]},
            )

        updated = await self.repo.set_status(pct_id, status="completed", to_doctor_id=doctor["profile_id"], consent_id=consent_id)
        await emit_event(
            self.session, aggregate_type="patient_clinic_transfer", aggregate_id=pct_id,
            event_type="relocation_completed", payload={"pct_id": str(pct_id), "new_doctor_id": str(doctor["profile_id"])},
        )
        return updated  # type: ignore[return-value]


class PatientExitService:
    """Master Doc Section 6.7 — exit/discharge. Requires patient_clinic_exit
    consent already signed. No dedicated 'exited' status column exists on
    patients (schema only tracks the 6-step registration machine) — exit is
    represented by the signed consent record itself plus closing out the
    active treatment cycle, not a new patients column."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.patients = PatientRepository(session)

    async def exit(self, patient_id: UUID, *, consent_id: UUID) -> dict:
        patient = await self.patients.get(patient_id)
        if not patient:
            raise NotFoundError("Patient not found", code="PATIENT_NOT_FOUND")

        from sqlalchemy import text as _text

        consent = (await self.session.execute(_text("SELECT * FROM consent_records WHERE consent_id = :id"), {"id": str(consent_id)})).mappings().first()
        if not consent or consent["status"] != "signed" or consent["consent_type"] != "patient_clinic_exit":
            raise BusinessRuleError("Exit requires a signed patient_clinic_exit consent", code="EXIT_CONSENT_REQUIRED")

        from app.modules.clinical.repository import TreatmentCycleRepository

        cycle_repo = TreatmentCycleRepository(self.session)
        active_cycle = await cycle_repo.get_active_for_patient(patient["profile_id"])
        if active_cycle:
            await cycle_repo.set_status(active_cycle["cycle_id"], "completed")

        await emit_event(
            self.session, aggregate_type="patient", aggregate_id=patient_id,
            event_type="patient_exited", payload={"patient_id": str(patient_id), "consent_id": str(consent_id)},
        )
        return {"patient_id": str(patient_id), "status": "exited", "consent_id": str(consent_id)}
