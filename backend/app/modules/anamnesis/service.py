from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import NotFoundError, ValidationError
from app.core.resolve import resolve_patient_profile_id as _resolve_profile_id
from app.modules.anamnesis.repository import (
    AnamnesisAssessmentRepository,
    AnamnesisQuestionRepository,
    AnamnesisResponseRepository,
)
from app.modules.scheduling.repository import AppointmentRepository


class AnamnesisCatalogService:
    def __init__(self, session: AsyncSession):
        self.repo = AnamnesisQuestionRepository(session)

    async def list_questions(self, type: str | None = None) -> list[dict]:
        return await self.repo.list_with_options(type)


class AnamnesisService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.assessments = AnamnesisAssessmentRepository(session)
        self.responses = AnamnesisResponseRepository(session)

    async def start(
        self,
        patient_id: UUID,
        *,
        submitted_by: UUID,
        taken_by: str,
        assessment_stage: str = "registration",
        appointment_id: UUID | None = None,
    ) -> dict:
        profile_id = await _resolve_profile_id(self.session, patient_id)
        if appointment_id is not None:
            appt = await AppointmentRepository(self.session).get(appointment_id)
            if not appt:
                raise NotFoundError("Appointment not found", code="APPOINTMENT_NOT_FOUND")
            if str(appt["patient_id"]) != str(profile_id):
                raise ValidationError("Appointment belongs to a different patient", code="APPOINTMENT_PATIENT_MISMATCH")
        next_version = await self.assessments.latest_version(profile_id) + 1
        assessment = await self.assessments.create(
            patient_id=profile_id,
            submitted_by=submitted_by,
            taken_by=taken_by,
            version=next_version,
            assessment_stage=assessment_stage,
            appointment_id=appointment_id,
        )
        await emit_event(
            self.session,
            aggregate_type="anamnesis_assessment",
            aggregate_id=assessment["anamnesis_id"],
            event_type="anamnesis_started",
            payload={"anamnesis_id": assessment["anamnesis_id"], "patient_id": str(patient_id)},
        )
        return assessment

    async def get_current(self, patient_id: UUID, assessment_stage: str | None = None) -> dict:
        profile_id = await _resolve_profile_id(self.session, patient_id)
        assessment = await self.assessments.get_latest_for_patient(profile_id, assessment_stage)
        if not assessment:
            raise NotFoundError("No anamnesis assessment found for this patient", code="ANAMNESIS_NOT_FOUND")
        return assessment

    async def list_versions(self, patient_id: UUID, assessment_stage: str | None = None) -> list[dict]:
        """Every version ever started for this patient — start()/edit
        (doctor's handleStartOnBehalf) always creates a NEW row rather than
        overwriting the one being edited, so a prior version is never lost,
        just no longer the one get_current() returns. This is what lets the
        UI show past versions instead of only ever the latest."""
        profile_id = await _resolve_profile_id(self.session, patient_id)
        return await self.assessments.list_for_patient(profile_id, assessment_stage)

    async def get_by_id(self, anamnesis_id: str) -> dict:
        """Used by the router to resolve the owning profile_id for
        assert_owns_profile() before returning responses / accepting a
        submission — anamnesis_id alone doesn't reveal whose record it is."""
        assessment = await self.assessments.get(anamnesis_id)
        if not assessment:
            raise NotFoundError("Anamnesis assessment not found", code="ANAMNESIS_NOT_FOUND")
        return assessment

    async def get_responses(self, anamnesis_id: str) -> list[dict]:
        return await self.responses.list_for_assessment(anamnesis_id)

    async def submit_responses(self, anamnesis_id: str, *, items: list[dict], complete: bool) -> dict:
        assessment = await self.assessments.get(anamnesis_id)
        if not assessment:
            raise NotFoundError("Anamnesis assessment not found", code="ANAMNESIS_NOT_FOUND")

        for item in items:
            await self.responses.upsert(
                anamnesis_id=anamnesis_id,
                question_id=item["question_id"],
                response_value=item.get("response_value"),
                response_values=item.get("response_values"),
            )

        if complete:
            completed = await self.assessments.mark_complete(anamnesis_id)
            if not completed:
                raise NotFoundError("Anamnesis assessment not found", code="ANAMNESIS_NOT_FOUND")
            assessment = completed
            await emit_event(
                self.session,
                aggregate_type="anamnesis_assessment",
                aggregate_id=anamnesis_id,
                event_type="anamnesis_completed",
                payload={"anamnesis_id": anamnesis_id, "patient_id": str(assessment["patient_id"])},
            )
            from app.modules.patients.repository import PatientRepository
            from app.modules.patients.service import PatientService

            patient = await PatientRepository(self.session).get_by_profile_id(assessment["patient_id"])
            if patient:
                await PatientService(self.session).advance_registration_status(patient["patient_id"])
        return assessment
