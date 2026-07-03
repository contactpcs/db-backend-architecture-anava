from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.modules.admin.repository import (
    AdminsRepository,
    ClinicRepository,
    ClinicRequestRepository,
    RegionRepository,
    StaffAssignmentRepository,
)
from app.modules.staff.repository import create_profile

# Clinic status state machine (Master Doc Section 5.1 / Architecture Section 6).
# pending_closure -> active covers "changed our mind, cancel the closure" —
# without it, clicking Deactivate is a one-way trip with no way back short
# of a direct DB edit, which isn't a real admin workflow.
_VALID_CLINIC_TRANSITIONS = {
    "setup": {"active"},
    "active": {"pending_closure"},
    "pending_closure": {"closed", "active"},
    "closed": set(),  # terminal
}
_MIN_STAFF_ROLES_FOR_ACTIVE = {"doctor", "clinical_assistant", "receptionist"}


class RegionService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = RegionRepository(session)

    async def create(self, *, region_name: str, country: str, state: str) -> dict:
        try:
            return await self.repo.create(region_name=region_name, country=country, state=state)
        except IntegrityError as exc:
            raise ConflictError(
                f"Region already exists for country={country!r} state={state!r}",
                code="REGION_ALREADY_EXISTS",
            ) from exc

    async def get(self, region_id: UUID) -> dict:
        region = await self.repo.get(region_id)
        if not region:
            raise NotFoundError("Region not found", code="REGION_NOT_FOUND")
        return region

    async def list(self) -> list[dict]:
        return await self.repo.list()

    async def update(self, region_id: UUID, fields: dict) -> dict:
        await self.get(region_id)  # 404 if missing
        clean = {k: v for k, v in fields.items() if v is not None}
        updated = await self.repo.update(region_id, clean)
        return updated  # type: ignore[return-value]

    async def delete(self, region_id: UUID) -> None:
        await self.get(region_id)  # 404 if missing
        clinic_count = await self.repo.count_clinics(region_id)
        if clinic_count > 0:
            raise ConflictError(
                f"Region has {clinic_count} clinic(s) — remove or reassign them first",
                code="REGION_HAS_CLINICS",
            )
        await self.repo.delete(region_id)

    async def assign_admin(self, region_id: UUID, data: dict) -> dict:
        """Creates a new regional_admin profile and links them to the
        region. Mirrors ClinicService.assign_admin — same 2-step reasoning:
        there's no self-serve admin registration, so this always creates the
        person. This is the only way a region gets a regional_admin — a
        clinic's clinic_admin is never linked as their region's
        regional_admin, even for that region's first/main-branch clinic
        (Master Doc Section 5.2)."""
        region = await self.get(region_id)
        if region["regional_admin_id"] is not None:
            raise BusinessRuleError("This region already has a regional_admin assigned", code="REGIONAL_ADMIN_ALREADY_ASSIGNED")

        try:
            profile = await create_profile(
                self.session, email=data["email"], first_name=data["first_name"],
                last_name=data["last_name"], phone=data.get("phone"), role="regional_admin",
                is_active=False,
            )
        except IntegrityError as exc:
            raise ConflictError(f"Email {data['email']!r} already in use", code="EMAIL_ALREADY_EXISTS") from exc

        admins_repo = AdminsRepository(self.session)
        await admins_repo.create(profile_id=profile["id"], admin_type="regional_admin", region_id=region_id, clinic_id=None)
        updated = await self.repo.update(region_id, {"regional_admin_id": str(profile["id"])})

        from app.modules.consent.service import ConsentRecordService

        clinic_for_consent = await self.session.execute(
            text("SELECT clinic_id FROM clinics WHERE region_id = :rid LIMIT 1"), {"rid": str(region_id)}
        )
        clinic_row = clinic_for_consent.mappings().first()
        if clinic_row:
            await ConsentRecordService(self.session).create(
                consent_type="staff_onboarding", patient_id=None, staff_id=profile["id"], clinic_id=clinic_row["clinic_id"],
            )
        return updated  # type: ignore[return-value]


class ClinicService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ClinicRepository(session)
        self.region_repo = RegionRepository(session)
        self.admins_repo = AdminsRepository(session)
        self.staff_repo = StaffAssignmentRepository(session)

    async def create(self, data: dict) -> dict:
        # First clinic in a region is automatically its main branch — no
        # picker for this, it's a fact derived from creation order.
        existing = await self.repo.count_for_region(UUID(data["region_id"]))
        payload = {**data, "status": "setup", "is_main_branch": existing == 0}
        try:
            clinic = await self.repo.create(payload)
        except IntegrityError as exc:
            raise ConflictError(
                f"Clinic code {data.get('clinic_code')!r} already exists", code="CLINIC_CODE_TAKEN"
            ) from exc
        await emit_event(
            self.session,
            aggregate_type="clinic",
            aggregate_id=clinic["clinic_id"],
            event_type="clinic_created",
            payload={"clinic_id": str(clinic["clinic_id"]), "region_id": str(clinic["region_id"])},
        )
        return clinic

    async def assign_admin(self, clinic_id: UUID, data: dict) -> dict:
        """Creates the clinic's clinic_admin and completes the 2-step setup
        flow. Only allowed once per clinic (no clinic_admin_id yet) — to
        change admins afterward, use PATCH /clinics/{id} instead.

        Does NOT touch regions.regional_admin_id. A region's regional_admin
        is always a separate, independently-created person (Master Doc
        Section 5.2: "Regional Admin must be assigned before region's
        clinics can reach 'active' status", "One Regional Admin per region")
        — a clinic_admin can never double as their own region's
        regional_admin. Use RegionService.assign_admin for that."""
        clinic = await self.get(clinic_id)
        if clinic["clinic_admin_id"] is not None:
            raise BusinessRuleError("This clinic already has a clinic_admin assigned", code="CLINIC_ADMIN_ALREADY_ASSIGNED")
        if clinic["status"] in ("pending_closure", "closed"):
            raise BusinessRuleError("Cannot assign an admin to a clinic that is closing/closed", code="CLINIC_NOT_OPEN")
        region = await self.region_repo.get(clinic["region_id"])
        if not region or region["regional_admin_id"] is None:
            raise BusinessRuleError(
                "This clinic's region has no regional_admin yet — assign one before adding clinic staff",
                code="REGIONAL_ADMIN_NOT_ASSIGNED",
            )

        try:
            profile = await create_profile(
                self.session, email=data["email"], first_name=data["first_name"],
                last_name=data["last_name"], phone=data.get("phone"), role="clinic_admin",
                is_active=False,  # inactive until they sign the staff_onboarding consent
            )
        except IntegrityError as exc:
            raise ConflictError(f"Email {data['email']!r} already in use", code="EMAIL_ALREADY_EXISTS") from exc

        await self.admins_repo.create(profile_id=profile["id"], admin_type="clinic_admin", region_id=None, clinic_id=clinic_id)
        await self.staff_repo.create(clinic_id=clinic_id, profile_id=profile["id"], staff_role="clinic_admin")
        updated = await self.repo.update(clinic_id, {"clinic_admin_id": str(profile["id"])})

        # Local import — avoids a module-load-time circular import (consent doesn't import admin).
        from app.modules.consent.service import ConsentRecordService

        await ConsentRecordService(self.session).create(
            consent_type="staff_onboarding", patient_id=None, staff_id=profile["id"], clinic_id=clinic_id,
        )

        await emit_event(
            self.session, aggregate_type="clinic", aggregate_id=clinic_id,
            event_type="clinic_admin_assigned", payload={"clinic_id": str(clinic_id), "profile_id": str(profile["id"])},
        )
        return updated  # type: ignore[return-value]

    async def delete(self, clinic_id: UUID) -> None:
        await self.get(clinic_id)  # 404 if missing
        dependents = await self.repo.count_dependents(clinic_id)
        if dependents > 0:
            raise ConflictError(
                f"Clinic has {dependents} active staff/patient record(s) — close it instead of deleting",
                code="CLINIC_HAS_DEPENDENTS",
            )
        await self.repo.delete(clinic_id)

    async def get(self, clinic_id: UUID) -> dict:
        clinic = await self.repo.get(clinic_id)
        if not clinic:
            raise NotFoundError("Clinic not found", code="CLINIC_NOT_FOUND")
        return clinic

    async def list(self, *, region_id: UUID | None = None, status: str | None = None) -> list[dict]:
        return await self.repo.list(region_id=region_id, status=status)

    async def update(self, clinic_id: UUID, fields: dict) -> dict:
        await self.get(clinic_id)
        clean = {k: v for k, v in fields.items() if v is not None}
        updated = await self.repo.update(clinic_id, clean)
        return updated  # type: ignore[return-value]

    async def change_status(self, clinic_id: UUID, new_status: str) -> dict:
        clinic = await self.get(clinic_id)
        current = clinic["status"]
        if new_status not in _VALID_CLINIC_TRANSITIONS.get(current, set()):
            raise BusinessRuleError(
                f"Cannot transition clinic from '{current}' to '{new_status}'",
                code="INVALID_CLINIC_STATUS_TRANSITION",
            )
        if new_status == "active":
            region = await self.region_repo.get(clinic["region_id"])
            if not region or region["regional_admin_id"] is None:
                raise BusinessRuleError(
                    "This clinic's region has no regional_admin yet — assign one before the clinic can go active",
                    code="REGIONAL_ADMIN_NOT_ASSIGNED",
                )
            await self._check_minimum_staff(clinic_id)
        if new_status == "closed":
            # Patient handling (transfer/exit) is enforced once the patients +
            # consent modules exist (Stage 6/5 dependency) — this check only
            # covers what's verifiable at Stage 4. Do not treat this as the
            # full closure guard the Master Doc describes (Section 5.5).
            pass
        updated = await self.repo.update(clinic_id, {"status": new_status})
        await emit_event(
            self.session,
            aggregate_type="clinic",
            aggregate_id=clinic_id,
            event_type="clinic_status_changed",
            payload={"clinic_id": str(clinic_id), "from": current, "to": new_status},
        )
        return updated  # type: ignore[return-value]

    async def _check_minimum_staff(self, clinic_id: UUID) -> None:
        staff = await self.staff_repo.list_for_clinic(clinic_id)
        active_roles = {s["staff_role"] for s in staff if s["is_active"]}
        missing = _MIN_STAFF_ROLES_FOR_ACTIVE - active_roles
        if missing:
            raise BusinessRuleError(
                f"Clinic cannot become active — missing required staff roles: {sorted(missing)}",
                code="MINIMUM_STAFF_NOT_MET",
            )


class ClinicRequestService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ClinicRequestRepository(session)

    async def create(self, data: dict, submitted_by: UUID) -> dict:
        payload = {**data, "submitted_by": str(submitted_by), "status": "pending"}
        req = await self.repo.create(payload)
        await emit_event(
            self.session,
            aggregate_type="clinic_request",
            aggregate_id=req["request_id"],
            event_type="clinic_request_submitted",
            payload={"request_id": str(req["request_id"]), "request_type": req["request_type"]},
        )
        return req

    async def get(self, request_id: UUID) -> dict:
        req = await self.repo.get(request_id)
        if not req:
            raise NotFoundError("Clinic request not found", code="CLINIC_REQUEST_NOT_FOUND")
        return req

    async def list(self, *, region_id: UUID | None = None, status: str | None = None) -> list[dict]:
        return await self.repo.list(region_id=region_id, status=status)

    async def decide(self, request_id: UUID, *, decision: str, reviewed_by: UUID, review_notes: str | None) -> dict:
        req = await self.get(request_id)
        if req["status"] != "pending":
            raise BusinessRuleError(
                f"Clinic request already {req['status']}", code="CLINIC_REQUEST_ALREADY_DECIDED"
            )
        updated = await self.repo.decide(
            request_id, status=decision, reviewed_by=reviewed_by, review_notes=review_notes
        )
        await emit_event(
            self.session,
            aggregate_type="clinic_request",
            aggregate_id=request_id,
            event_type="clinic_request_decided",
            payload={"request_id": str(request_id), "decision": decision},
        )
        return updated  # type: ignore[return-value]


class StaffAssignmentService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = StaffAssignmentRepository(session)

    async def add(self, clinic_id: UUID, *, profile_id: UUID, staff_role: str) -> dict:
        try:
            assignment = await self.repo.create(clinic_id=clinic_id, profile_id=profile_id, staff_role=staff_role)
        except IntegrityError as exc:
            raise ConflictError(
                "This profile already has an assignment at this clinic", code="ASSIGNMENT_ALREADY_EXISTS"
            ) from exc
        await emit_event(
            self.session,
            aggregate_type="clinic_staff_assignment",
            aggregate_id=assignment["assignment_id"],
            event_type="staff_assigned",
            payload={"clinic_id": str(clinic_id), "profile_id": str(profile_id), "staff_role": staff_role},
        )
        return assignment

    async def list_for_clinic(self, clinic_id: UUID) -> list[dict]:
        return await self.repo.list_for_clinic(clinic_id)

    async def remove(self, assignment_id: UUID) -> dict:
        removed = await self.repo.remove(assignment_id)
        if not removed:
            raise NotFoundError("Staff assignment not found", code="ASSIGNMENT_NOT_FOUND")
        return removed


class AdminAccountsService:
    """Read-side for the admins table — lists regional_admin/clinic_admin
    accounts with their name/email/scope. super_admin accounts are excluded
    by the router's default filter (they're not clinic/region-scoped, so
    they don't belong on this management view)."""

    def __init__(self, session: AsyncSession):
        self.repo = AdminsRepository(session)

    async def list(self, *, admin_type: str | None = None, region_id: UUID | None = None, clinic_id: UUID | None = None) -> list[dict]:
        return await self.repo.list(admin_type=admin_type, region_id=region_id, clinic_id=clinic_id)
