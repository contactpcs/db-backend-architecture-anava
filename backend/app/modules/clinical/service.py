from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.core.resolve import resolve_doctor_profile_id as _resolve_doctor_profile_id
from app.core.resolve import resolve_patient_profile_id as _resolve_patient_profile_id
from app.modules.clinical.repository import (
    ProtocolRequestRepository,
    TreatmentCycleRepository,
    TreatmentPlanRepository,
)


async def _resolve_patient_filter(session: AsyncSession, filters: dict) -> dict:
    """Turn an API patients.patient_id filter into the profiles.id actually stored.

    Every patient_id-shaped column in core stores profiles.id (NOTES.md), while
    the API accepts patients.patient_id for consistency with GET /patients/{id}.
    The create paths already resolve this via _resolve_patient_profile_id; the
    LIST paths did not, so filtering by patient matched nothing and returned an
    empty list with no error.

    That was not cosmetic: the wizard's resolveOrCreatePlanId reads
    GET /treatment-cycles?patient_id=..., saw no cycle, and POSTed a new one —
    which the one-active-cycle rule then rejected with 400. The fix belongs
    here rather than in the caller, because every list endpoint has the same
    hole.
    """
    if filters.get("patient_id"):
        filters = {**filters, "patient_id": await _resolve_patient_profile_id(session, filters["patient_id"])}
    return filters


class TreatmentCycleService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = TreatmentCycleRepository(session)

    async def create(self, data: dict) -> dict:
        patient_profile_id = await _resolve_patient_profile_id(self.session, data["patient_id"])
        doctor_profile_id = await _resolve_doctor_profile_id(self.session, data["doctor_id"])

        # One active block per patient at a time (Architecture Section 6 / Master Doc)
        existing = await self.repo.get_active_for_patient(patient_profile_id)
        if existing:
            raise BusinessRuleError("Patient already has an active treatment cycle", code="ACTIVE_CYCLE_EXISTS")

        payload = {
            "patient_id": str(patient_profile_id),
            "doctor_id": str(doctor_profile_id),
            "clinic_id": str(data["clinic_id"]),
            "cycle_type": data["cycle_type"],
            "cycle_number": data.get("cycle_number", 1),
        }
        cycle = await self.repo.create(payload)
        await emit_event(
            self.session,
            aggregate_type="treatment_cycle",
            aggregate_id=cycle["cycle_id"],
            event_type="treatment_cycle_created",
            payload={"cycle_id": str(cycle["cycle_id"]), "patient_id": str(data["patient_id"])},
        )
        return cycle

    async def get(self, cycle_id: UUID) -> dict:
        cycle = await self.repo.get(cycle_id)
        if not cycle:
            raise NotFoundError("Treatment cycle not found", code="CYCLE_NOT_FOUND")
        return cycle

    async def list(self, **filters) -> list[dict]:
        return await self.repo.list(**await _resolve_patient_filter(self.session, filters))

    async def set_status(self, cycle_id: UUID, status: str) -> dict:
        await self.get(cycle_id)
        updated = await self.repo.set_status(cycle_id, status)
        return updated  # type: ignore[return-value]


class ProtocolRequestService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ProtocolRequestRepository(session)

    async def create(self, data: dict, *, clinical_assistant_id: UUID) -> dict:
        patient_profile_id = await _resolve_patient_profile_id(self.session, data["patient_id"])
        doctor_profile_id = await _resolve_doctor_profile_id(self.session, data["doctor_id"])
        payload = {
            "patient_id": str(patient_profile_id),
            "clinical_assistant_id": str(clinical_assistant_id),
            "doctor_id": str(doctor_profile_id),
            "clinic_id": str(data["clinic_id"]) if data.get("clinic_id") else None,
            "cycle_id": str(data["cycle_id"]) if data.get("cycle_id") else None,
            "protocol_details": data.get("protocol_details", {}),
        }
        request = await self.repo.create(payload)
        await emit_event(
            self.session,
            aggregate_type="assessment_protocol_request",
            aggregate_id=request["request_id"],
            event_type="protocol_submitted",
            payload={"request_id": str(request["request_id"])},
        )
        return request

    async def get(self, request_id: UUID) -> dict:
        req = await self.repo.get(request_id)
        if not req:
            raise NotFoundError("Protocol request not found", code="PROTOCOL_REQUEST_NOT_FOUND")
        return req

    async def list(self, **filters) -> list[dict]:
        return await self.repo.list(**await _resolve_patient_filter(self.session, filters))

    async def decide(self, request_id: UUID, *, decision: str, doctor_notes: str | None) -> dict:
        req = await self.get(request_id)
        if req["status"] == "approved":
            raise BusinessRuleError("Protocol request already approved", code="PROTOCOL_ALREADY_APPROVED")
        updated = await self.repo.decide(request_id, status=decision, doctor_notes=doctor_notes)

        if decision == "approved":
            # Master Doc Section 9.3: on Doctor authorization, the CA's
            # selected main_clinical scales (protocol_details.main_prs_scale_ids)
            # become patient_scale_assignments — this is what Session 1's PRS
            # administration reads to know which scales to present.
            scale_ids = (req.get("protocol_details") or {}).get("main_prs_scale_ids") or []
            if scale_ids:
                # req["patient_id"] is already profiles.id here (assessment_protocol_requests
                # stores it that way) — PatientScaleAssignmentService.create expects
                # patients.patient_id, so go through the patients table the other way.
                from app.modules.patients.repository import (
                    DiseaseSelectionRepository,
                    PatientRepository,
                )
                from app.modules.prs.service import PatientScaleAssignmentService

                patient = await PatientRepository(self.session).get_by_profile_id(req["patient_id"])
                if patient:
                    # assessment_protocol_requests carries no disease_id of its own —
                    # patient_scale_assignments.disease_id (SQL/48) needs one, so this
                    # resolves the patient's primary disease selection the same way
                    # Session 1's Main PRS is actually about their registered condition.
                    selections = await DiseaseSelectionRepository(self.session).list_for_patient(req["patient_id"])
                    primary = next((sel for sel in selections if sel["is_primary"]), None) or (selections[0] if selections else None)
                    if primary and primary["disease_id"]:
                        assignment_service = PatientScaleAssignmentService(self.session)
                        for scale_id in scale_ids:
                            await assignment_service.create(
                                patient_id=patient["patient_id"],
                                scale_id=scale_id,
                                disease_id=primary["disease_id"],
                                assessment_stage="main_clinical",
                                assigned_by=req["doctor_id"],
                                assignment_reason="ca_selected",
                            )

        await emit_event(
            self.session,
            aggregate_type="assessment_protocol_request",
            aggregate_id=request_id,
            event_type="protocol_authorized" if decision == "approved" else "protocol_decision",
            payload={"request_id": str(request_id), "decision": decision},
        )
        return updated  # type: ignore[return-value]


class TreatmentPlanService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = TreatmentPlanRepository(session)

    async def create(self, data: dict) -> dict:
        patient_profile_id = await _resolve_patient_profile_id(self.session, data["patient_id"])
        doctor_profile_id = await _resolve_doctor_profile_id(self.session, data["doctor_id"])

        if data.get("parent_plan_id"):
            from app.modules.clinical.repository import TreatmentPlanRepository as _TPR

            await _TPR(self.session).supersede(data["parent_plan_id"])

        payload = {
            "patient_id": str(patient_profile_id),
            "doctor_id": str(doctor_profile_id),
            "cycle_id": str(data["cycle_id"]),
            "device_type": data["device_type"],
            "protocol_details": data.get("protocol_details", {}),
            "sessions_prescribed": data.get("sessions_prescribed", 5),
            "standard_sessions": data.get("standard_sessions", 5),
            "parent_plan_id": str(data["parent_plan_id"]) if data.get("parent_plan_id") else None,
        }
        plan = await self.repo.create(payload)
        await emit_event(
            self.session,
            aggregate_type="treatment_plan",
            aggregate_id=plan["plan_id"],
            event_type="treatment_plan_created",
            payload={"plan_id": str(plan["plan_id"]), "extended_sessions": plan["extended_sessions"]},
        )
        return plan

    async def get(self, plan_id: UUID) -> dict:
        plan = await self.repo.get(plan_id)
        if not plan:
            raise NotFoundError("Treatment plan not found", code="PLAN_NOT_FOUND")
        return plan

    async def list(self, **filters) -> list[dict]:
        return await self.repo.list(**await _resolve_patient_filter(self.session, filters))

    async def update(self, plan_id: UUID, fields: dict) -> dict:
        await self.get(plan_id)
        clean = {k: v for k, v in fields.items() if v is not None}
        updated = await self.repo.update(plan_id, clean)
        return updated  # type: ignore[return-value]
