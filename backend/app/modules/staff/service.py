from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
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
    soft_delete_profile,
    update_profile,
)

_PROFILE_UPDATE_KEYS = {"first_name", "last_name", "phone", "gender", "dob", "address"}


def _split_profile_fields(fields: dict) -> tuple[dict, dict]:
    """Every staff *Update payload mixes profile-level fields (name/phone/...)
    with role-specific ones (specialization, qualification, ...) — this
    splits them so each half goes to the table that actually owns it."""
    clean = {k: v for k, v in fields.items() if v is not None}
    profile_fields = {k: v for k, v in clean.items() if k in _PROFILE_UPDATE_KEYS}
    role_fields = {k: v for k, v in clean.items() if k not in _PROFILE_UPDATE_KEYS}
    return profile_fields, role_fields


async def _ensure_clinic_ready_for_staff(session: AsyncSession, clinic_id) -> None:
    """A clinic's region must have its regional_admin assigned, the clinic
    must have its clinic_admin assigned (2-step setup flow), and it must not
    be winding down before any doctor/CA/receptionist can be attached to it
    (Master Doc Section 5.2: no staff/patients in a region until it has a
    regional_admin; clinic_admin is always the first clinic-level staff
    member, nobody else can be assigned before them)."""
    clinic = (
        await session.execute(
            text(
                "SELECT c.clinic_admin_id, c.status, r.regional_admin_id "
                "FROM clinics c JOIN regions r ON r.region_id = c.region_id WHERE c.clinic_id = :id"
            ),
            {"id": str(clinic_id)},
        )
    ).mappings().first()
    if clinic is None:
        raise NotFoundError("Clinic not found", code="CLINIC_NOT_FOUND")
    if clinic["regional_admin_id"] is None:
        raise BusinessRuleError(
            "This clinic's region has no regional_admin yet — assign one before adding staff",
            code="REGIONAL_ADMIN_NOT_ASSIGNED",
        )
    if clinic["clinic_admin_id"] is None:
        raise BusinessRuleError(
            "This clinic has no clinic_admin yet — assign one before adding other staff",
            code="CLINIC_ADMIN_NOT_ASSIGNED",
        )
    if clinic["status"] in ("pending_closure", "closed"):
        raise BusinessRuleError("Cannot assign staff to a clinic that is closing/closed", code="CLINIC_NOT_OPEN")



def _merge_profile(row: dict, profile: dict) -> dict:
    """create_profile() already has first_name/last_name/email/phone/is_active
    in hand — merge them into the just-created role row so the create
    response matches what list()/get() return (both joined to profiles),
    instead of a second round-trip query."""
    return {
        **row, "first_name": profile["first_name"], "last_name": profile["last_name"],
        "email": profile["email"], "phone": profile["phone"], "profile_is_active": profile["is_active"],
    }


def _assert_staff_email_domain(email: str) -> None:
    """Clinical staff (doctor/CA/receptionist) log in with an official org
    email only — never a personal one. Patients are untouched (their own
    service never calls this)."""
    from app.config import get_settings

    domain = email.rsplit("@", 1)[-1].lower()
    allowed = {d.lower() for d in get_settings().staff_allowed_email_domains}
    if domain not in allowed:
        raise BusinessRuleError(
            f"Staff email must use an official organization domain ({', '.join(sorted(allowed))})",
            code="INVALID_STAFF_EMAIL_DOMAIN",
        )


async def _resolve_staff_request(session: AsyncSession, staff_request_id, *, expected_role: str, clinic_id) -> dict | None:
    """Validates an optional staff_request_id passed into a Doctor/CA/
    Receptionist create() call — the request must be approved, unfulfilled,
    for the same clinic, and for the role actually being created. Returns
    the request dict (caller fulfills it after the profile is created) or
    None if no staff_request_id was given."""
    if not staff_request_id:
        return None
    req = await StaffRequestRepository(session).get(staff_request_id)
    if not req:
        raise NotFoundError("Staff request not found", code="STAFF_REQUEST_NOT_FOUND")
    if req["status"] != "approved":
        raise BusinessRuleError("Staff request is not approved", code="STAFF_REQUEST_NOT_APPROVED")
    if req["fulfilled_profile_id"]:
        raise ConflictError("Staff request already fulfilled", code="STAFF_REQUEST_ALREADY_FULFILLED")
    if req["position_role"] != expected_role:
        raise BusinessRuleError("Staff request position_role does not match", code="STAFF_REQUEST_ROLE_MISMATCH")
    if str(req["clinic_id"]) != str(clinic_id):
        raise BusinessRuleError("Staff request is for a different clinic", code="STAFF_REQUEST_CLINIC_MISMATCH")
    return req


async def _create_onboarding_consent(session: AsyncSession, *, staff_id, clinic_id) -> None:
    """Every new staff profile is created is_active=FALSE (see create_profile)
    — this generates the pending staff_onboarding consent_record they'll see
    and sign on first login, which is what flips them back to active (see
    consent/service.py ConsentRecordService.sign). Local import to avoid a
    module-load-time circular import (consent doesn't import staff)."""
    from app.modules.consent.service import ConsentRecordService

    await ConsentRecordService(session).create(
        consent_type="staff_onboarding", patient_id=None, staff_id=staff_id, clinic_id=clinic_id,
    )


class DoctorService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = DoctorRepository(session)
        self.assignments = StaffAssignmentRepository(session)

    async def create(self, data: dict) -> dict:
        _assert_staff_email_domain(data["email"])
        await _ensure_clinic_ready_for_staff(self.session, data["clinic_id"])
        staff_request = await _resolve_staff_request(
            self.session, data.get("staff_request_id"), expected_role="doctor", clinic_id=data["clinic_id"],
        )
        try:
            profile = await create_profile(
                self.session, email=data["email"], first_name=data["first_name"],
                last_name=data["last_name"], phone=data.get("phone"), role="doctor", is_active=False,
                gender=data.get("gender"), dob=data.get("dob"), address=data.get("address"),
                city=data.get("city"), state=data.get("state"), country=data.get("country"),
                pincode=data.get("pincode"),
            )
        except IntegrityError as exc:
            raise ConflictError(f"Email {data['email']!r} already in use", code="EMAIL_ALREADY_EXISTS") from exc

        doctor = await self.repo.create(
            profile_id=profile["id"], clinic_id=data["clinic_id"], specialization=data.get("specialization"),
            license_number=data.get("license_number"), hospital_affiliation=data.get("hospital_affiliation"),
            max_patient_count=data.get("max_patient_count", 30),
        )
        await self.assignments.create(clinic_id=data["clinic_id"], profile_id=profile["id"], staff_role="doctor")
        await _create_onboarding_consent(self.session, staff_id=profile["id"], clinic_id=data["clinic_id"])
        if staff_request:
            await StaffRequestRepository(self.session).fulfill(staff_request["request_id"], profile_id=profile["id"])
        await emit_event(
            self.session, aggregate_type="doctor", aggregate_id=doctor["doctor_id"],
            event_type="doctor_onboarded", payload={"doctor_id": str(doctor["doctor_id"]), "clinic_id": str(data["clinic_id"])},
        )
        return _merge_profile(doctor, profile)

    async def get(self, doctor_id: UUID) -> dict:
        doctor = await self.repo.get(doctor_id)
        if not doctor:
            raise NotFoundError("Doctor not found", code="DOCTOR_NOT_FOUND")
        return doctor

    async def list(self, *, clinic_id: UUID | None = None) -> list[dict]:
        return await self.repo.list(clinic_id=clinic_id)

    async def update(self, doctor_id: UUID, fields: dict, *, updated_by: UUID) -> dict:
        doctor = await self.get(doctor_id)
        profile_fields, role_fields = _split_profile_fields(fields)
        if profile_fields:
            await update_profile(self.session, doctor["profile_id"], profile_fields)
        if role_fields:
            await self.repo.update(doctor_id, role_fields)
        await emit_event(
            self.session, aggregate_type="doctor", aggregate_id=doctor_id, event_type="doctor_updated",
            payload={"doctor_id": str(doctor_id), "updated_by": str(updated_by), "changed_fields": sorted(profile_fields | role_fields)},
        )
        return await self.get(doctor_id)  # re-fetch via the joined query — UPDATE...RETURNING * has no profile columns

    async def delete(self, doctor_id: UUID, *, deleted_by: UUID) -> None:
        doctor = await self.get(doctor_id)
        await self.repo.soft_delete(doctor_id, deleted_by=deleted_by)
        await soft_delete_profile(self.session, doctor["profile_id"], deleted_by=deleted_by)

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
        _assert_staff_email_domain(data["email"])
        await _ensure_clinic_ready_for_staff(self.session, data["clinic_id"])
        staff_request = await _resolve_staff_request(
            self.session, data.get("staff_request_id"), expected_role="clinical_assistant", clinic_id=data["clinic_id"],
        )
        try:
            profile = await create_profile(
                self.session, email=data["email"], first_name=data["first_name"],
                last_name=data["last_name"], phone=data.get("phone"), role="clinical_assistant", is_active=False,
                gender=data.get("gender"), dob=data.get("dob"), address=data.get("address"),
                city=data.get("city"), state=data.get("state"), country=data.get("country"),
                pincode=data.get("pincode"),
            )
        except IntegrityError as exc:
            raise ConflictError(f"Email {data['email']!r} already in use", code="EMAIL_ALREADY_EXISTS") from exc

        ca = await self.repo.create(profile_id=profile["id"], clinic_id=data["clinic_id"], qualification=data.get("qualification"))
        await self.assignments.create(clinic_id=data["clinic_id"], profile_id=profile["id"], staff_role="clinical_assistant")
        await _create_onboarding_consent(self.session, staff_id=profile["id"], clinic_id=data["clinic_id"])
        if staff_request:
            await StaffRequestRepository(self.session).fulfill(staff_request["request_id"], profile_id=profile["id"])
        await emit_event(
            self.session, aggregate_type="clinical_assistant", aggregate_id=ca["ca_id"],
            event_type="staff_onboarded", payload={"ca_id": str(ca["ca_id"]), "clinic_id": str(data["clinic_id"])},
        )
        return _merge_profile(ca, profile)

    async def get(self, ca_id: UUID) -> dict:
        ca = await self.repo.get(ca_id)
        if not ca:
            raise NotFoundError("Clinical assistant not found", code="CA_NOT_FOUND")
        return ca

    async def list(self, *, clinic_id: UUID | None = None) -> list[dict]:
        return await self.repo.list(clinic_id=clinic_id)

    async def update(self, ca_id: UUID, fields: dict, *, updated_by: UUID) -> dict:
        ca = await self.get(ca_id)
        profile_fields, role_fields = _split_profile_fields(fields)
        if profile_fields:
            await update_profile(self.session, ca["profile_id"], profile_fields)
        if role_fields:
            await self.repo.update(ca_id, role_fields)
        await emit_event(
            self.session, aggregate_type="clinical_assistant", aggregate_id=ca_id, event_type="staff_updated",
            payload={"ca_id": str(ca_id), "updated_by": str(updated_by), "changed_fields": sorted(profile_fields | role_fields)},
        )
        return await self.get(ca_id)

    async def delete(self, ca_id: UUID, *, deleted_by: UUID) -> None:
        ca = await self.get(ca_id)
        await self.repo.soft_delete(ca_id, deleted_by=deleted_by)
        await soft_delete_profile(self.session, ca["profile_id"], deleted_by=deleted_by)


class ReceptionistService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ReceptionistRepository(session)
        self.assignments = StaffAssignmentRepository(session)

    async def create(self, data: dict) -> dict:
        _assert_staff_email_domain(data["email"])
        await _ensure_clinic_ready_for_staff(self.session, data["clinic_id"])
        staff_request = await _resolve_staff_request(
            self.session, data.get("staff_request_id"), expected_role="receptionist", clinic_id=data["clinic_id"],
        )
        try:
            profile = await create_profile(
                self.session, email=data["email"], first_name=data["first_name"],
                last_name=data["last_name"], phone=data.get("phone"), role="receptionist", is_active=False,
                gender=data.get("gender"), dob=data.get("dob"), address=data.get("address"),
                city=data.get("city"), state=data.get("state"), country=data.get("country"),
                pincode=data.get("pincode"),
            )
        except IntegrityError as exc:
            raise ConflictError(f"Email {data['email']!r} already in use", code="EMAIL_ALREADY_EXISTS") from exc

        receptionist = await self.repo.create(profile_id=profile["id"], clinic_id=data["clinic_id"])
        await self.assignments.create(clinic_id=data["clinic_id"], profile_id=profile["id"], staff_role="receptionist")
        await _create_onboarding_consent(self.session, staff_id=profile["id"], clinic_id=data["clinic_id"])
        if staff_request:
            await StaffRequestRepository(self.session).fulfill(staff_request["request_id"], profile_id=profile["id"])
        await emit_event(
            self.session, aggregate_type="receptionist", aggregate_id=receptionist["receptionist_id"],
            event_type="staff_onboarded",
            payload={"receptionist_id": str(receptionist["receptionist_id"]), "clinic_id": str(data["clinic_id"])},
        )
        return _merge_profile(receptionist, profile)

    async def get(self, receptionist_id: UUID) -> dict:
        receptionist = await self.repo.get(receptionist_id)
        if not receptionist:
            raise NotFoundError("Receptionist not found", code="RECEPTIONIST_NOT_FOUND")
        return receptionist

    async def list(self, *, clinic_id: UUID | None = None) -> list[dict]:
        return await self.repo.list(clinic_id=clinic_id)

    async def update(self, receptionist_id: UUID, fields: dict, *, updated_by: UUID) -> dict:
        receptionist = await self.get(receptionist_id)
        profile_fields, role_fields = _split_profile_fields(fields)
        if profile_fields:
            await update_profile(self.session, receptionist["profile_id"], profile_fields)
        if role_fields:
            await self.repo.update(receptionist_id, role_fields)
        await emit_event(
            self.session, aggregate_type="receptionist", aggregate_id=receptionist_id, event_type="staff_updated",
            payload={"receptionist_id": str(receptionist_id), "updated_by": str(updated_by), "changed_fields": sorted(profile_fields | role_fields)},
        )
        return await self.get(receptionist_id)

    async def delete(self, receptionist_id: UUID, *, deleted_by: UUID) -> None:
        receptionist = await self.get(receptionist_id)
        await self.repo.soft_delete(receptionist_id, deleted_by=deleted_by)
        await soft_delete_profile(self.session, receptionist["profile_id"], deleted_by=deleted_by)


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

    async def list_by_region(self, region_id: str, *, status: str | None = None) -> list[dict]:
        return await self.repo.list_by_region(region_id, status=status)

    async def decide(self, request_id: UUID, *, decision: str, reviewed_by: UUID, review_notes: str | None) -> dict:
        req = await self.get(request_id)
        if req["status"] in ("approved", "rejected", "withdrawn"):
            raise BusinessRuleError(f"Staff request already {req['status']}", code="STAFF_REQUEST_ALREADY_DECIDED")

        # Approval no longer auto-creates the profile. clinic_admin only
        # requests/refers; regional_admin reviews and, on approval, creates
        # the actual staff profile themselves as a separate manual step
        # (POST /doctors|/clinical-assistants|/receptionists, now open to
        # regional_admin/super_admin — see _STAFF_CREATE_ROLES) using an
        # official org email they choose (see _assert_staff_email_domain).
        updated = await self.repo.decide(request_id, status=decision, reviewed_by=reviewed_by, review_notes=review_notes)

        await emit_event(
            self.session, aggregate_type="staff_request", aggregate_id=request_id,
            event_type="staff_request_decided", payload={"request_id": str(request_id), "decision": decision},
        )
        return updated  # type: ignore[return-value]
