from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.core.resolve import resolve_doctor_profile_id as _resolve_doctor_profile_id
from app.core.resolve import resolve_patient_profile_id as _resolve_patient_profile_id
from app.modules.clinical.repository import ProtocolRequestRepository


async def _resolve_patient_filter(session: AsyncSession, filters: dict) -> dict:
    """Turn an API patients.patient_id filter into the profiles.id actually stored.

    Every patient_id-shaped column in core stores profiles.id (NOTES.md), while
    the API accepts patients.patient_id for consistency with GET /patients/{id}.
    """
    if filters.get("patient_id"):
        filters = {**filters, "patient_id": await _resolve_patient_profile_id(session, filters["patient_id"])}
    return filters


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
            disease_id = (req.get("protocol_details") or {}).get("disease_id")
            # disease_id used to be resolved from the patient's registration-
            # time primary disease selection (patient_disease_selection) —
            # removed 27 Aug 2026 (70_remove_disease_selection.sql). The CA
            # now supplies it directly at submission time (protocol_details),
            # same free-form JSONB main_prs_scale_ids already lives in.
            if scale_ids and disease_id:
                # req["patient_id"] is already profiles.id here (assessment_protocol_requests
                # stores it that way) — PatientScaleAssignmentService.create expects
                # patients.patient_id, so go through the patients table the other way.
                from app.modules.patients.repository import PatientRepository
                from app.modules.prs.service import PatientScaleAssignmentService

                patient = await PatientRepository(self.session).get_by_profile_id(req["patient_id"])
                if patient:
                    assignment_service = PatientScaleAssignmentService(self.session)
                    for scale_id in scale_ids:
                        await assignment_service.create(
                            patient_id=patient["patient_id"],
                            scale_id=scale_id,
                            disease_id=disease_id,
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
