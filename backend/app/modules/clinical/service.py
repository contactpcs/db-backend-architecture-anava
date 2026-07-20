from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.core.resolve import resolve_ca_profile_id as _resolve_ca_profile_id
from app.core.resolve import resolve_doctor_profile_id as _resolve_doctor_profile_id
from app.core.resolve import resolve_patient_profile_id as _resolve_patient_profile_id
from app.modules.clinical.repository import (
    DoctorSessionNoteRepository,
    ProtocolRequestRepository,
    SessionRepository,
    TreatmentCycleRepository,
    TreatmentPlanRepository,
    TreatmentSessionRepository,
)


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
            raise BusinessRuleError(
                "Patient already has an active treatment cycle", code="ACTIVE_CYCLE_EXISTS"
            )

        payload = {
            "patient_id": str(patient_profile_id), "doctor_id": str(doctor_profile_id),
            "clinic_id": str(data["clinic_id"]), "cycle_type": data["cycle_type"], "cycle_number": data.get("cycle_number", 1),
        }
        cycle = await self.repo.create(payload)
        await emit_event(
            self.session, aggregate_type="treatment_cycle", aggregate_id=cycle["cycle_id"],
            event_type="treatment_cycle_created", payload={"cycle_id": str(cycle["cycle_id"]), "patient_id": str(data["patient_id"])},
        )
        return cycle

    async def get(self, cycle_id: UUID) -> dict:
        cycle = await self.repo.get(cycle_id)
        if not cycle:
            raise NotFoundError("Treatment cycle not found", code="CYCLE_NOT_FOUND")
        return cycle

    async def list(self, **filters) -> list[dict]:
        return await self.repo.list(**filters)

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
            "patient_id": str(patient_profile_id), "clinical_assistant_id": str(clinical_assistant_id),
            "doctor_id": str(doctor_profile_id), "clinic_id": str(data["clinic_id"]) if data.get("clinic_id") else None,
            "cycle_id": str(data["cycle_id"]) if data.get("cycle_id") else None,
            "protocol_details": data.get("protocol_details", {}),
        }
        request = await self.repo.create(payload)
        await emit_event(
            self.session, aggregate_type="assessment_protocol_request", aggregate_id=request["request_id"],
            event_type="protocol_submitted", payload={"request_id": str(request["request_id"])},
        )
        return request

    async def get(self, request_id: UUID) -> dict:
        req = await self.repo.get(request_id)
        if not req:
            raise NotFoundError("Protocol request not found", code="PROTOCOL_REQUEST_NOT_FOUND")
        return req

    async def list(self, **filters) -> list[dict]:
        return await self.repo.list(**filters)

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
                from app.modules.prs.service import PatientScaleAssignmentService

                # req["patient_id"] is already profiles.id here (assessment_protocol_requests
                # stores it that way) — PatientScaleAssignmentService.create expects
                # patients.patient_id, so go through the patients table the other way.
                from app.modules.patients.repository import DiseaseSelectionRepository, PatientRepository

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
                                patient_id=patient["patient_id"], scale_id=scale_id, disease_id=primary["disease_id"],
                                assessment_stage="main_clinical", assigned_by=req["doctor_id"], assignment_reason="ca_selected",
                            )

        await emit_event(
            self.session, aggregate_type="assessment_protocol_request", aggregate_id=request_id,
            event_type="protocol_authorized" if decision == "approved" else "protocol_decision",
            payload={"request_id": str(request_id), "decision": decision},
        )
        return updated  # type: ignore[return-value]


class SessionService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = SessionRepository(session)
        self.protocol_repo = ProtocolRequestRepository(session)

    async def create(self, data: dict) -> dict:
        patient_profile_id = await _resolve_patient_profile_id(self.session, data["patient_id"])

        # Session 1 (clinical_assistant phase) cannot be booked until the CA's
        # protocol has been authorized by the Doctor (Master Doc Section 6.3 /
        # 7.3 — "Session 1 blocked until assessment_protocol_requests.status='approved'").
        if data.get("session_phase") == "clinical_assistant" and data.get("cycle_id"):
            protocols = await self.protocol_repo.list(patient_id=patient_profile_id, status="approved")
            if not any(str(p.get("cycle_id")) == str(data["cycle_id"]) or p.get("cycle_id") is None for p in protocols):
                raise BusinessRuleError(
                    "Session 1 cannot be booked until the assessment protocol is approved", code="PROTOCOL_NOT_APPROVED"
                )

        doctor_profile_id = None
        if data.get("doctor_id"):
            doctor_profile_id = await _resolve_doctor_profile_id(self.session, data["doctor_id"])
        ca_profile_id = None
        if data.get("ca_id"):
            ca_profile_id = await _resolve_ca_profile_id(self.session, data["ca_id"])

        payload = {
            "patient_id": str(patient_profile_id),
            "doctor_id": str(doctor_profile_id) if doctor_profile_id else None,
            "session_date": data["session_date"], "session_type": data.get("session_type", "in_person"),
            "cycle_id": str(data["cycle_id"]) if data.get("cycle_id") else None,
            "clinic_id": str(data["clinic_id"]) if data.get("clinic_id") else None,
            "ca_id": str(ca_profile_id) if ca_profile_id else None,
            "session_phase": data.get("session_phase"),
            "session_number_in_cycle": data.get("session_number_in_cycle"),
        }
        record = await self.repo.create(payload)
        await emit_event(
            self.session, aggregate_type="session", aggregate_id=record["session_id"],
            event_type="session_created", payload={"session_id": str(record["session_id"]), "phase": record["session_phase"]},
        )
        return record

    async def get(self, session_id: UUID) -> dict:
        record = await self.repo.get(session_id)
        if not record:
            raise NotFoundError("Session not found", code="SESSION_NOT_FOUND")
        return record

    async def list(self, **filters) -> list[dict]:
        return await self.repo.list(**filters)

    async def update_status(self, session_id: UUID, *, status: str, outcome: str | None) -> dict:
        await self.get(session_id)
        updated = await self.repo.update_status(session_id, status=status, outcome=outcome)
        await emit_event(
            self.session, aggregate_type="session", aggregate_id=session_id,
            event_type="session_completed" if status == "completed" else "session_status_changed",
            payload={"session_id": str(session_id), "status": status, "outcome": outcome},
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
            "patient_id": str(patient_profile_id), "doctor_id": str(doctor_profile_id),
            "cycle_id": str(data["cycle_id"]), "device_type": data["device_type"],
            "protocol_details": data.get("protocol_details", {}),
            "sessions_prescribed": data.get("sessions_prescribed", 5),
            "standard_sessions": data.get("standard_sessions", 5),
            "parent_plan_id": str(data["parent_plan_id"]) if data.get("parent_plan_id") else None,
        }
        plan = await self.repo.create(payload)
        await emit_event(
            self.session, aggregate_type="treatment_plan", aggregate_id=plan["plan_id"],
            event_type="treatment_plan_created", payload={"plan_id": str(plan["plan_id"]), "extended_sessions": plan["extended_sessions"]},
        )
        return plan

    async def get(self, plan_id: UUID) -> dict:
        plan = await self.repo.get(plan_id)
        if not plan:
            raise NotFoundError("Treatment plan not found", code="PLAN_NOT_FOUND")
        return plan

    async def list(self, **filters) -> list[dict]:
        return await self.repo.list(**filters)

    async def update(self, plan_id: UUID, fields: dict) -> dict:
        await self.get(plan_id)
        clean = {k: v for k, v in fields.items() if v is not None}
        updated = await self.repo.update(plan_id, clean)
        return updated  # type: ignore[return-value]


class TreatmentSessionService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = TreatmentSessionRepository(session)

    async def create(self, data: dict) -> dict:
        patient_profile_id = await _resolve_patient_profile_id(self.session, data["patient_id"])
        ca_profile_id = await _resolve_ca_profile_id(self.session, data["ca_id"])
        payload = {
            "plan_id": str(data["plan_id"]), "session_id": str(data["session_id"]),
            "patient_id": str(patient_profile_id), "ca_id": str(ca_profile_id),
            "session_number": data["session_number"], "billing_type": data["billing_type"],
            # Extended sessions require payment before they can start (Master
            # Doc Section 7.8 / 13.2) — standard sessions follow clinic config,
            # not gated here (that's the payments module's concern once billed).
            "payment_status": "pending" if data["billing_type"] == "extended" else "not_required",
        }
        ts = await self.repo.create(payload)
        await emit_event(
            self.session, aggregate_type="treatment_session", aggregate_id=ts["ts_id"],
            event_type="treatment_session_created", payload={"ts_id": str(ts["ts_id"]), "billing_type": ts["billing_type"]},
        )
        return ts

    async def get(self, ts_id: UUID) -> dict:
        ts = await self.repo.get(ts_id)
        if not ts:
            raise NotFoundError("Treatment session not found", code="TREATMENT_SESSION_NOT_FOUND")
        return ts

    async def list(self, **filters) -> list[dict]:
        return await self.repo.list(**filters)

    async def update_status(self, ts_id: UUID, *, status: str, session_notes, patient_feedback) -> dict:
        ts = await self.get(ts_id)
        if status == "in_progress" and ts["billing_type"] == "extended" and ts["payment_status"] not in ("paid", "waived"):
            raise BusinessRuleError(
                "Extended treatment session cannot start until payment is paid or waived", code="PAYMENT_REQUIRED"
            )
        updated = await self.repo.update_status(ts_id, status=status, session_notes=session_notes, patient_feedback=patient_feedback)
        await emit_event(
            self.session, aggregate_type="treatment_session", aggregate_id=ts_id,
            event_type="treatment_session_completed" if status == "completed" else "treatment_session_status_changed",
            payload={"ts_id": str(ts_id), "status": status},
        )
        return updated  # type: ignore[return-value]


class DoctorSessionNoteService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = DoctorSessionNoteRepository(session)

    async def create(self, data: dict) -> dict:
        patient_profile_id = await _resolve_patient_profile_id(self.session, data["patient_id"])
        doctor_profile_id = await _resolve_doctor_profile_id(self.session, data["doctor_id"])
        payload = {**data, "patient_id": str(patient_profile_id), "doctor_id": str(doctor_profile_id)}
        for key in ("session_id", "cycle_id"):
            payload[key] = str(payload[key])
        return await self.repo.create(payload)

    async def get(self, note_id: UUID) -> dict:
        note = await self.repo.get(note_id)
        if not note:
            raise NotFoundError("Doctor session note not found", code="NOTE_NOT_FOUND")
        return note
