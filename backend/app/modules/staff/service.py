from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.modules.admin.repository import StaffAssignmentRepository
from app.modules.staff.repository import (
    CaDoctorAssignmentRepository,
    ClinicalAssistantRepository,
    DoctorRepository,
    ReceptionistRepository,
    StaffRequestRepository,
    create_profile,
)


class DoctorService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = DoctorRepository(session)
        self.assignments = StaffAssignmentRepository(session)

    async def create(self, data: dict) -> dict:
        try:
            profile = await create_profile(
                self.session, email=data["email"], first_name=data["first_name"],
                last_name=data["last_name"], phone=data.get("phone"), role="doctor",
            )
        except IntegrityError as exc:
            raise ConflictError(f"Email {data['email']!r} already in use", code="EMAIL_ALREADY_EXISTS") from exc

        doctor = await self.repo.create(
            profile_id=profile["id"], specialization=data.get("specialization"),
            license_number=data.get("license_number"), hospital_affiliation=data.get("hospital_affiliation"),
            max_patient_count=data.get("max_patient_count", 30),
        )
        await self.assignments.create(clinic_id=data["clinic_id"], profile_id=profile["id"], staff_role="doctor")
        await emit_event(
            self.session, aggregate_type="doctor", aggregate_id=doctor["doctor_id"],
            event_type="doctor_onboarded", payload={"doctor_id": str(doctor["doctor_id"]), "clinic_id": str(data["clinic_id"])},
        )
        return doctor

    async def get(self, doctor_id: UUID) -> dict:
        doctor = await self.repo.get(doctor_id)
        if not doctor:
            raise NotFoundError("Doctor not found", code="DOCTOR_NOT_FOUND")
        return doctor

    async def list(self, *, clinic_id: UUID | None = None) -> list[dict]:
        return await self.repo.list(clinic_id=clinic_id)

    async def update(self, doctor_id: UUID, fields: dict) -> dict:
        await self.get(doctor_id)
        clean = {k: v for k, v in fields.items() if v is not None}
        updated = await self.repo.update(doctor_id, clean)
        return updated  # type: ignore[return-value]

    async def pick_least_loaded(self, clinic_id: UUID) -> dict | None:
        """Load-balanced doctor allocation (Master Doc Flow M): picks the
        active doctor at this clinic with the fewest active assignments, via
        the view (not a counter column — avoids the read-modify-write race a
        counter would have under concurrent allocation, see SQL/03_staff_role_tables.sql)."""
        candidates = await self.repo.list(clinic_id=clinic_id)
        candidates = [d for d in candidates if d["availability_status"] == "available"]
        if not candidates:
            return None
        counts = await self.repo.active_patient_counts([UUID(str(d["doctor_id"])) for d in candidates])
        return min(candidates, key=lambda d: counts.get(str(d["doctor_id"]), 0))


class ClinicalAssistantService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ClinicalAssistantRepository(session)
        self.assignments = StaffAssignmentRepository(session)

    async def create(self, data: dict) -> dict:
        try:
            profile = await create_profile(
                self.session, email=data["email"], first_name=data["first_name"],
                last_name=data["last_name"], phone=data.get("phone"), role="clinical_assistant",
            )
        except IntegrityError as exc:
            raise ConflictError(f"Email {data['email']!r} already in use", code="EMAIL_ALREADY_EXISTS") from exc

        ca = await self.repo.create(profile_id=profile["id"], clinic_id=data["clinic_id"], qualification=data.get("qualification"))
        await self.assignments.create(clinic_id=data["clinic_id"], profile_id=profile["id"], staff_role="clinical_assistant")
        await emit_event(
            self.session, aggregate_type="clinical_assistant", aggregate_id=ca["ca_id"],
            event_type="staff_onboarded", payload={"ca_id": str(ca["ca_id"]), "clinic_id": str(data["clinic_id"])},
        )
        return ca

    async def list(self, *, clinic_id: UUID | None = None) -> list[dict]:
        return await self.repo.list(clinic_id=clinic_id)


class ReceptionistService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ReceptionistRepository(session)
        self.assignments = StaffAssignmentRepository(session)

    async def create(self, data: dict) -> dict:
        try:
            profile = await create_profile(
                self.session, email=data["email"], first_name=data["first_name"],
                last_name=data["last_name"], phone=data.get("phone"), role="receptionist",
            )
        except IntegrityError as exc:
            raise ConflictError(f"Email {data['email']!r} already in use", code="EMAIL_ALREADY_EXISTS") from exc

        receptionist = await self.repo.create(profile_id=profile["id"], clinic_id=data["clinic_id"])
        await self.assignments.create(clinic_id=data["clinic_id"], profile_id=profile["id"], staff_role="receptionist")
        await emit_event(
            self.session, aggregate_type="receptionist", aggregate_id=receptionist["receptionist_id"],
            event_type="staff_onboarded",
            payload={"receptionist_id": str(receptionist["receptionist_id"]), "clinic_id": str(data["clinic_id"])},
        )
        return receptionist

    async def list(self, *, clinic_id: UUID | None = None) -> list[dict]:
        return await self.repo.list(clinic_id=clinic_id)


class CaDoctorAssignmentService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = CaDoctorAssignmentRepository(session)

    async def create(self, *, ca_id: UUID, doctor_id: UUID, clinic_id: UUID, is_primary: bool) -> dict:
        try:
            return await self.repo.create(ca_id=ca_id, doctor_id=doctor_id, clinic_id=clinic_id, is_primary=is_primary)
        except IntegrityError as exc:
            raise ConflictError("This CA is already assigned to this doctor", code="ASSIGNMENT_ALREADY_EXISTS") from exc

    async def list(self, *, ca_id: UUID | None = None, doctor_id: UUID | None = None) -> list[dict]:
        return await self.repo.list(ca_id=ca_id, doctor_id=doctor_id)


class StaffRequestService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = StaffRequestRepository(session)
        self.doctors = DoctorService(session)
        self.cas = ClinicalAssistantService(session)
        self.receptionists = ReceptionistService(session)

    async def create(self, data: dict, submitted_by: UUID) -> dict:
        payload = {
            **{k: v for k, v in data.items() if k != "target_staff_id" or v is not None},
            "submitted_by": str(submitted_by),
            "status": "pending",
        }
        req = await self.repo.create(payload)
        await emit_event(
            self.session, aggregate_type="staff_request", aggregate_id=req["request_id"],
            event_type="staff_request_submitted", payload={"request_id": str(req["request_id"])},
        )
        return req

    async def get(self, request_id: UUID) -> dict:
        req = await self.repo.get(request_id)
        if not req:
            raise NotFoundError("Staff request not found", code="STAFF_REQUEST_NOT_FOUND")
        return req

    async def list(self, *, clinic_id: UUID | None = None, status: str | None = None) -> list[dict]:
        return await self.repo.list(clinic_id=clinic_id, status=status)

    async def decide(self, request_id: UUID, *, decision: str, reviewed_by: UUID, review_notes: str | None) -> dict:
        req = await self.get(request_id)
        if req["status"] in ("approved", "rejected", "withdrawn"):
            raise BusinessRuleError(f"Staff request already {req['status']}", code="STAFF_REQUEST_ALREADY_DECIDED")

        updated = await self.repo.decide(request_id, status=decision, reviewed_by=reviewed_by, review_notes=review_notes)

        # On approval of a candidate_referral, onboard the staff member now.
        # Real deployment gates this on a signed staff_onboarding consent
        # (Master Doc Flow H step 7-8) — deferred until the consent module
        # exists (Stage 5), tracked here rather than silently skipped.
        if decision == "approved" and req["request_type"] == "candidate_referral" and req["candidate_email"]:
            person = {
                "email": req["candidate_email"], "first_name": req["candidate_name"] or "Unknown",
                "last_name": "", "phone": req["candidate_phone"], "clinic_id": req["clinic_id"],
            }
            if req["position_role"] == "doctor":
                await self.doctors.create(person)
            elif req["position_role"] == "clinical_assistant":
                await self.cas.create(person)
            elif req["position_role"] == "receptionist":
                await self.receptionists.create(person)

        await emit_event(
            self.session, aggregate_type="staff_request", aggregate_id=request_id,
            event_type="staff_request_decided", payload={"request_id": str(request_id), "decision": decision},
        )
        return updated  # type: ignore[return-value]
