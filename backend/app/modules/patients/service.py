from __future__ import annotations

import builtins
from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import RequestContext
from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from app.core.resolve import resolve_patient_profile_id as _resolve_profile_id
from app.modules.patients.repository import (
    DoctorPatientAssignmentRepository,
    PatientRepository,
    PatientTransferRepository,
)
from app.modules.staff.service import DoctorService


def _is_minor(dob: date | None) -> bool:
    if dob is None:
        return False
    today = date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return age < 18


_GUARDIAN_FIELDS = ("guardian_name", "guardian_relationship", "guardian_contact")


def _guardian_fields_provided(data: dict) -> bool:
    return any(data.get(f) for f in _GUARDIAN_FIELDS)


# Registration status machine (Master Doc Section 6.2 / SQL/04_patient_tables.sql CHECK constraint)
# Disease selection removed from registration (70_remove_disease_selection.sql,
# 27 Aug 2026) — patients no longer self-diagnose before seeing a doctor.
_REGISTRATION_STEPS = [
    "demographics_complete",
    "consent_signed",
    "anamnesis_complete",
    "general_prs_complete",
    "registration_complete",
]


class PatientService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = PatientRepository(session)
        self.assignments = DoctorPatientAssignmentRepository(session)

    async def register(
        self, data: dict, *, self_registered: bool = False, cognito_sub: str | None = None, registered_by: UUID | None = None
    ) -> dict:
        clinic = (
            (
                await self.session.execute(
                    text(
                        "SELECT c.clinic_admin_id, c.status, r.regional_admin_id "
                        "FROM clinics c JOIN regions r ON r.region_id = c.region_id WHERE c.clinic_id = :id"
                    ),
                    {"id": str(data["primary_clinic_id"])},
                )
            )
            .mappings()
            .first()
        )
        if clinic is None:
            raise NotFoundError("Clinic not found", code="CLINIC_NOT_FOUND")
        if clinic["regional_admin_id"] is None:
            raise BusinessRuleError(
                "This clinic's region has no regional_admin yet — assign one before registering patients",
                code="REGIONAL_ADMIN_NOT_ASSIGNED",
            )
        if clinic["clinic_admin_id"] is None:
            raise BusinessRuleError(
                "This clinic has no clinic_admin yet — assign one before registering patients",
                code="CLINIC_ADMIN_NOT_ASSIGNED",
            )
        if clinic["status"] in ("pending_closure", "closed"):
            raise BusinessRuleError("Cannot register a patient at a clinic that is closing/closed", code="CLINIC_NOT_OPEN")
        # Under-18: guardian details (name, relationship, contact) are always
        # required — same columns for every route (self-service,
        # receptionist), no separate guardian identity/table. 18+: optional,
        # but once any one guardian field is provided, all three are —
        # no half-filled guardian record either way. Enforced here, not just
        # as an optional schema field, so every caller is covered by one
        # check rather than trusting each to remember it.
        if _is_minor(data.get("dob")) or _guardian_fields_provided(data):
            missing = [f for f in _GUARDIAN_FIELDS if not data.get(f)]
            if missing:
                raise BusinessRuleError(f"Guardian details incomplete: {', '.join(missing)} required", code="GUARDIAN_REQUIRED")
        try:
            patient = await self.repo.create_profile_and_patient(
                email=data["email"],
                first_name=data["first_name"],
                last_name=data["last_name"],
                phone=data.get("phone"),
                gender=data.get("gender"),
                dob=data.get("dob"),
                address=data.get("address"),
                primary_clinic_id=data["primary_clinic_id"],
                emergency_contact_name=data.get("emergency_contact_name"),
                emergency_contact_phone=data.get("emergency_contact_phone"),
                city=data.get("city"),
                state=data.get("state"),
                country=data.get("country"),
                pincode=data.get("pincode"),
                self_registered=self_registered,
                approval_status="pending" if self_registered else "not_required",
                cognito_sub=cognito_sub,
                registered_by=registered_by,
                guardian_name=data.get("guardian_name"),
                guardian_relationship=data.get("guardian_relationship"),
                guardian_contact=data.get("guardian_contact"),
            )
        except IntegrityError as exc:
            raise ConflictError(f"Email {data['email']!r} already in use", code="EMAIL_ALREADY_EXISTS") from exc

        # Local import — avoids a module-load-time circular import (consent doesn't import patients).
        from app.modules.consent.service import create_onboarding_consent

        await create_onboarding_consent(
            self.session,
            role="patient",
            profile_id=patient["patient_id"],
            clinic_id=patient["primary_clinic_id"],
        )
        await emit_event(
            self.session,
            aggregate_type="patient",
            aggregate_id=patient["patient_id"],
            event_type="patient_registered",
            payload={"patient_id": str(patient["patient_id"]), "mrn": patient["mrn"]},
            # Only fills a gap for anonymous self-registration; no-op when a
            # staff member (already authenticated) registered this patient.
            actor_role="patient",
        )
        return patient

    async def get(self, patient_id: UUID) -> dict:
        patient = await self.repo.get(patient_id)
        if not patient:
            raise NotFoundError("Patient not found", code="PATIENT_NOT_FOUND")
        return patient

    async def list(self, **filters) -> builtins.list[dict]:
        return await self.repo.list(**filters)

    async def update(self, patient_id: UUID, fields: dict) -> dict:
        await self.get(patient_id)  # 404 if missing
        profile_keys = {
            "first_name",
            "last_name",
            "email",
            "phone",
            "gender",
            "dob",
            "address",
            "city",
            "state",
            "country",
            "pincode",
            "is_active",
        }
        patient_keys = {"emergency_contact_name", "emergency_contact_phone"}
        clean = {k: v for k, v in fields.items() if v is not None}
        profile_fields = {k: v for k, v in clean.items() if k in profile_keys}
        patient_fields = {k: v for k, v in clean.items() if k in patient_keys}
        try:
            updated = await self.repo.update(patient_id, profile_fields=profile_fields, patient_fields=patient_fields)
        except IntegrityError as exc:
            raise ConflictError(f"Email {profile_fields.get('email')!r} already in use", code="EMAIL_ALREADY_EXISTS") from exc
        return updated  # type: ignore[return-value]

    async def decide_approval(self, patient_id: UUID, *, decision: str, decided_by: UUID, rejection_reason: str | None) -> dict:
        """Receptionist review gate for self-registered patients — only
        reachable once the patient has finished the whole 6-step wizard
        themselves (Master Doc per this feature's design: receptionist only
        sees the request after registration_complete, not partway through)."""
        patient = await self.get(patient_id)
        if patient["registration_status"] != "registration_complete":
            raise BusinessRuleError("Patient hasn't completed registration yet", code="REGISTRATION_INCOMPLETE")
        if patient["approval_status"] != "pending":
            raise BusinessRuleError(f"Approval already {patient['approval_status']}", code="APPROVAL_ALREADY_DECIDED")

        await self.repo.set_approval(
            patient_id,
            approval_status=decision,
            approved_by=decided_by,
            rejection_reason=rejection_reason,
        )
        if decision == "approved":
            await self.session.execute(text("UPDATE profiles SET is_active = TRUE WHERE id = :id"), {"id": str(patient["profile_id"])})
        await emit_event(
            self.session,
            aggregate_type="patient",
            aggregate_id=patient_id,
            event_type="patient_registration_decided",
            payload={"patient_id": str(patient_id), "decision": decision},
        )
        return await self.get(patient_id)

    async def allocate_doctor(self, patient_id: UUID, doctor_id: UUID, *, allocated_by: UUID) -> dict:
        """Manual (re)allocation — receptionist/clinic_admin explicitly
        picking a doctor, as opposed to _complete_registration's automatic
        least-loaded pick. Ends any existing active assignment before
        creating the new one (doctor_patient_assignments has no unique
        constraint stopping two 'active' rows for the same patient
        otherwise)."""
        patient = await self.get(patient_id)
        doctor = await DoctorService(self.session).get(doctor_id)
        if not doctor:
            raise NotFoundError("Doctor not found", code="DOCTOR_NOT_FOUND")
        if str(doctor["clinic_id"]) != str(patient["primary_clinic_id"]):
            raise BusinessRuleError("Doctor must be at the patient's own clinic", code="DOCTOR_CLINIC_MISMATCH")

        await self.assignments.end_active(patient_id=patient["profile_id"], clinic_id=patient["primary_clinic_id"])
        await self.assignments.create(
            doctor_id=doctor["profile_id"], patient_id=patient["profile_id"], clinic_id=patient["primary_clinic_id"]
        )
        await self.repo.update(patient_id, profile_fields={}, patient_fields={"primary_doctor_id": str(doctor["profile_id"])})
        await emit_event(
            self.session,
            aggregate_type="patient",
            aggregate_id=patient_id,
            event_type="doctor_allocated",
            payload={
                "patient_id": str(patient_id),
                "doctor_id": str(doctor["profile_id"]),
                "allocated_by": str(allocated_by),
            },
        )
        return await self.get(patient_id)

    async def delete(self, patient_id: UUID, *, deleted_by: UUID) -> None:
        await self.get(patient_id)  # 404 if missing
        await self.repo.soft_delete(patient_id, deleted_by=deleted_by)
        await emit_event(
            self.session,
            aggregate_type="patient",
            aggregate_id=patient_id,
            event_type="patient_deleted",
            payload={"patient_id": str(patient_id)},
        )

    async def advance_registration_status(self, patient_id: UUID) -> dict:
        """Re-derives registration_status from scratch by checking every
        dependency (Master Doc table 8, step 6: 'System validates all steps
        complete'). Idempotent and self-healing — safe to call after any step,
        in any order, rather than requiring each module to know what status
        string to push next."""
        patient = await self.get(patient_id)
        profile_id = patient["profile_id"]

        has_signed_onboarding_consent = await self._exists(
            "SELECT 1 FROM consent_records WHERE patient_id = :pid AND consent_type = 'patient_onboarding' AND status = 'signed'",
            {"pid": str(profile_id)},
        )
        has_completed_anamnesis = await self._exists(
            "SELECT 1 FROM anamnesis_assessments WHERE patient_id = :pid AND status = 'completed'", {"pid": str(profile_id)}
        )
        has_completed_general_prs = await self._exists(
            "SELECT 1 FROM prs_assessment_instances WHERE patient_id = :pid "
            "AND assessment_stage = 'general_registration' AND status = 'completed'",
            {"pid": str(profile_id)},
        )

        # general_prs_complete has no distinct observable window: per Master Doc
        # step 6 ("System validates all steps complete"), registration_complete
        # fires automatically and immediately once PRS finishes — there's no
        # separate gate between the two, so this status value is skipped over
        # rather than persisted as its own row state. Not a bug; there's simply
        # nothing that would ever read a patient sitting in that state.
        if has_completed_general_prs and has_completed_anamnesis and has_signed_onboarding_consent:
            return await self._complete_registration(patient_id, patient)
        if has_completed_anamnesis and has_signed_onboarding_consent:
            new_status = "anamnesis_complete"
        elif has_signed_onboarding_consent:
            new_status = "consent_signed"
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
            raise BusinessRuleError("No available doctor at this clinic to auto-allocate", code="NO_AVAILABLE_DOCTOR")
        # doctors.profile_id, not doctors.doctor_id — every FK to "doctor"
        # elsewhere in the schema (patients.primary_doctor_id,
        # doctor_patient_assignments.doctor_id) points at profiles(id) via
        # doctors.profile_id, not the doctors table's own PK. Real bug hit
        # during Stage 6 testing (ForeignKeyViolationError) — same class of
        # mistake as the patients.patient_id vs profiles.id confusion above.
        await self.repo.complete_registration(patient_id, doctor["profile_id"])
        await self.assignments.create(
            doctor_id=doctor["profile_id"], patient_id=patient["profile_id"], clinic_id=patient["primary_clinic_id"]
        )
        # Staff-registered patients (approval_status='not_required') have no
        # receptionist approval gate to wait on — activate them the moment
        # they finish the same registration-test sequence self-registered
        # patients go through (disease selection, anamnesis, general PRS).
        # Self-registered patients (approval_status='pending') stay inactive
        # here; decide_approval() is what activates them.
        if patient["approval_status"] == "not_required":
            await self.session.execute(text("UPDATE profiles SET is_active = TRUE WHERE id = :id"), {"id": str(patient["profile_id"])})
        updated = await self.repo.get(patient_id)
        await emit_event(
            self.session,
            aggregate_type="patient",
            aggregate_id=patient_id,
            event_type="registration_completed",
            payload={"patient_id": str(patient_id)},
        )
        await emit_event(
            self.session,
            aggregate_type="patient",
            aggregate_id=patient_id,
            event_type="doctor_auto_allocated",
            payload={"patient_id": str(patient_id), "doctor_id": str(doctor["doctor_id"])},
        )
        return updated  # type: ignore[return-value]


class FollowUpService:
    """Master Doc Section 6.6 — a follow-up opens a new protocol_instances
    episode (instance_type='followup'), reusing ProtocolInstanceService
    directly rather than duplicating its create/one-active-episode logic.
    Requires the patient's previous episode to be closed out."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = PatientRepository(session)

    async def start(self, patient_id: UUID, *, doctor_id: UUID | None, ctx: RequestContext) -> dict:
        from app.modules.treatment_protocols.schemas import ProtocolInstanceCreate
        from app.modules.treatment_protocols.service import ProtocolInstanceService

        patient = await self.repo.get(patient_id)
        if not patient:
            raise NotFoundError("Patient not found", code="PATIENT_NOT_FOUND")
        if patient["registration_status"] != "registration_complete":
            raise BusinessRuleError("Patient has not completed initial registration", code="REGISTRATION_INCOMPLETE")

        # doctor_id here is doctors.doctor_id (or None to keep the current
        # primary doctor) — ProtocolInstanceService.create resolves it the
        # same way an initial episode does, no special-casing for follow-up.
        doctor_arg = doctor_id
        if doctor_arg is None:
            doctor_row = await self._doctor_by_profile_id(patient["primary_doctor_id"])
            doctor_arg = doctor_row["doctor_id"] if doctor_row else None
            if doctor_arg is None:
                raise BusinessRuleError("No doctor_id provided and patient has no primary doctor on file", code="DOCTOR_REQUIRED")

        instance = await ProtocolInstanceService(self.session).create(
            ProtocolInstanceCreate(
                patient_id=patient_id,
                doctor_id=doctor_arg,
                clinic_id=patient["primary_clinic_id"],
                instance_type="followup",
            ),
            ctx,
        )
        await emit_event(
            self.session,
            aggregate_type="protocol_instance",
            aggregate_id=instance["instance_id"],
            event_type="followup_episode_created",
            payload={"instance_id": str(instance["instance_id"]), "patient_id": str(patient_id)},
        )
        return instance

    async def _doctor_by_profile_id(self, profile_id):
        from sqlalchemy import text as _text

        row = (
            (await self.session.execute(_text("SELECT * FROM doctors WHERE profile_id = :pid"), {"pid": str(profile_id)}))
            .mappings()
            .first()
        )
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

        from app.modules.treatment_protocols.repository import ProtocolInstanceRepository

        active_instance = await ProtocolInstanceRepository(self.session).get_open_for_patient(patient["profile_id"])

        payload = {
            "patient_id": str(patient["profile_id"]),
            "from_clinic_id": str(patient["primary_clinic_id"]),
            "to_clinic_id": str(data["to_clinic_id"]),
            "from_doctor_id": str(patient["primary_doctor_id"]) if patient["primary_doctor_id"] else None,
            "transfer_reason": data["transfer_reason"],
            "active_instance_id": str(active_instance["instance_id"]) if active_instance else None,
            "initiated_by": str(initiated_by),
            "notes": data.get("notes"),
        }
        transfer = await self.repo.create(payload)
        await emit_event(
            self.session,
            aggregate_type="patient_clinic_transfer",
            aggregate_id=transfer["pct_id"],
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

        consent = (
            (await self.session.execute(_text("SELECT * FROM consent_records WHERE consent_id = :id"), {"id": str(consent_id)}))
            .mappings()
            .first()
        )
        if not consent or consent["status"] != "signed":
            raise BusinessRuleError("Transfer requires a signed consent record", code="CONSENT_NOT_SIGNED")

        # Auto doctor allocation at the new clinic — mandatory, no exceptions
        # (Master Doc: "Auto doctor allocation at new clinic is mandatory").
        doctor = await DoctorService(self.session).pick_least_loaded(transfer["to_clinic_id"])
        if not doctor:
            raise BusinessRuleError("No available doctor at the receiving clinic", code="NO_AVAILABLE_DOCTOR")

        await self.session.execute(
            _text("UPDATE patients SET primary_clinic_id = :clinic_id, primary_doctor_id = :doctor_id WHERE profile_id = :pid"),
            {"clinic_id": transfer["to_clinic_id"], "doctor_id": doctor["profile_id"], "pid": transfer["patient_id"]},
        )

        if transfer["active_instance_id"]:
            # Active episode carries over WITHOUT restart — same
            # protocol_instances row, just repointed to the new clinic/doctor
            # (Master Doc: "Block resumes from current session. NO RESTART.").
            await self.session.execute(
                _text("UPDATE protocol_instances SET clinic_id = :clinic_id, doctor_id = :doctor_id WHERE instance_id = :instance_id"),
                {"clinic_id": transfer["to_clinic_id"], "doctor_id": doctor["profile_id"], "instance_id": transfer["active_instance_id"]},
            )

        updated = await self.repo.set_status(pct_id, status="completed", to_doctor_id=doctor["profile_id"], consent_id=consent_id)
        await emit_event(
            self.session,
            aggregate_type="patient_clinic_transfer",
            aggregate_id=pct_id,
            event_type="relocation_completed",
            payload={"pct_id": str(pct_id), "new_doctor_id": str(doctor["profile_id"])},
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

        consent = (
            (await self.session.execute(_text("SELECT * FROM consent_records WHERE consent_id = :id"), {"id": str(consent_id)}))
            .mappings()
            .first()
        )
        if not consent or consent["status"] != "signed" or consent["consent_type"] != "patient_clinic_exit":
            raise BusinessRuleError("Exit requires a signed patient_clinic_exit consent", code="EXIT_CONSENT_REQUIRED")

        from app.modules.treatment_protocols.repository import ProtocolInstanceRepository

        instance_repo = ProtocolInstanceRepository(self.session)
        active_instance = await instance_repo.get_open_for_patient(patient["profile_id"])
        if active_instance:
            await instance_repo.set_status(active_instance["instance_id"], "completed")

        await emit_event(
            self.session,
            aggregate_type="patient",
            aggregate_id=patient_id,
            event_type="patient_exited",
            payload={"patient_id": str(patient_id), "consent_id": str(consent_id)},
        )
        return {"patient_id": str(patient_id), "status": "exited", "consent_id": str(consent_id)}


class PatientVisitService:
    """Composes the doctor portal's per-visit bundle (registration/anamnesis/
    PRS/protocol) out of each owning module's own reads — no new storage,
    no new abstraction, just the joins a single visit toggle needs. Cross-
    module imports stay local to the method, matching this file's existing
    convention (PatientExitService.exit() above)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_visit_summary(self, patient_id: UUID, appointment_id: UUID) -> dict:
        from app.modules.anamnesis.repository import AnamnesisAssessmentRepository
        from app.modules.prs.repository import AssessmentInstanceRepository
        from app.modules.scheduling.repository import AppointmentRepository
        from app.modules.treatment_protocols.repository import ProtocolInstanceRepository, ProtocolRepository

        profile_id = await _resolve_profile_id(self.session, patient_id)

        appt = await AppointmentRepository(self.session).get(appointment_id)
        if not appt:
            raise NotFoundError("Appointment not found", code="APPOINTMENT_NOT_FOUND")
        if str(appt["patient_id"]) != str(profile_id):
            raise ValidationError("Appointment belongs to a different patient", code="APPOINTMENT_PATIENT_MISMATCH")

        is_initial = appt["appointment_type"] == "initial"
        cutoff = appt["appointment_date"]

        registration = await PatientService(self.session).get(patient_id) if is_initial else None

        # Anamnesis is genuinely per-visit, never inherited across visits: a
        # follow-up only has one if the doctor actually took it there —
        # otherwise this stays None ("no anamnesis taken"), not a copy of an
        # earlier visit's. The one exception is the initial visit itself:
        # self-registration creates version 1 before any appointment_id
        # exists to link it to (that flow has no visit context at all), so
        # without this fallback the initial visit would wrongly show "no
        # anamnesis taken" for a patient who has one. Not "inheritance" in
        # the PRS/protocol sense — version 1 IS the initial visit's record,
        # just not linked by column for historical reasons.
        anamnesis_repo = AnamnesisAssessmentRepository(self.session)
        anamnesis = await anamnesis_repo.get_by_appointment(appointment_id)
        if not anamnesis and is_initial:
            anamnesis = await anamnesis_repo.get_by_version(profile_id, 1)

        # PRS and protocol DO carry forward: a visit with nothing recorded
        # specifically for it inherits whatever was current as of its date,
        # changing only what the doctor actually changes there. "inherited"
        # on each record marks whether it's this visit's own or carried
        # forward, so the frontend can label it accordingly.
        prs_repo = AssessmentInstanceRepository(self.session)
        prs_rows = await prs_repo.list_by_appointment(appointment_id)
        if prs_rows:
            prs_instances = [{**r, "inherited": False} for r in prs_rows]
        else:
            prs_instances = [{**r, "inherited": True} for r in await prs_repo.list_latest_as_of(profile_id, cutoff)]

        protocol_repo = ProtocolRepository(self.session)
        protocol_rows = await protocol_repo.list(authored_in_appointment_id=appointment_id, limit=50)
        inherited_protocol = not protocol_rows
        if inherited_protocol:
            instance = await ProtocolInstanceRepository(self.session).get_open_for_patient(profile_id)
            if instance:
                latest = await protocol_repo.get_latest_as_of(instance["instance_id"], cutoff)
                protocol_rows = [latest] if latest else []
        protocols = []
        for row in protocol_rows:
            lineage: list[dict] = []
            supersedes_id = row.get("supersedes_protocol_id")
            while supersedes_id:
                parent = await protocol_repo.get(supersedes_id)
                if not parent:
                    break
                lineage.append(parent)
                supersedes_id = parent.get("supersedes_protocol_id")
            protocols.append({**row, "lineage": lineage, "inherited": inherited_protocol})

        return {
            "appointment_id": appt["appointment_id"],
            "appointment_type": appt["appointment_type"],
            "appointment_date": appt["appointment_date"],
            "registration": registration,
            "anamnesis": anamnesis,
            "prs_instances": prs_instances,
            "protocols": protocols,
        }
