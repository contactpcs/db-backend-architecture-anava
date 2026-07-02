from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.modules.admin.repository import (
    ClinicRepository,
    ClinicRequestRepository,
    RegionRepository,
    StaffAssignmentRepository,
)

# Clinic status state machine (Master Doc Section 5.1 / Architecture Section 6)
_VALID_CLINIC_TRANSITIONS = {
    "setup": {"active"},
    "active": {"pending_closure"},
    "pending_closure": {"closed"},
    "closed": set(),  # terminal
}
_MIN_STAFF_ROLES_FOR_ACTIVE = {"doctor", "clinical_assistant", "receptionist"}


class RegionService:
    def __init__(self, session: AsyncSession):
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


class ClinicService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ClinicRepository(session)
        self.staff_repo = StaffAssignmentRepository(session)

    async def create(self, data: dict) -> dict:
        payload = {**data, "status": "setup"}
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
