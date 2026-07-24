from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import NotFoundError
from app.core.resolve import resolve_patient_profile_id as _resolve_profile_id
from app.modules.anamnesis.repository import (
    AnamnesisAssessmentRepository,
    AnamnesisQuestionRepository,
    AnamnesisResponseRepository,
)


class AnamnesisCatalogService:
    def __init__(self, session: AsyncSession):
        self.repo = AnamnesisQuestionRepository(session)

    async def list_questions(self) -> list[dict]:
        return await self.repo.list_with_options()


class AnamnesisService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.assessments = AnamnesisAssessmentRepository(session)
        self.responses = AnamnesisResponseRepository(session)

    async def start(self, patient_id: UUID, *, submitted_by: UUID, taken_by: str, cycle_id=None) -> dict:
        profile_id = await _resolve_profile_id(self.session, patient_id)
        next_version = await self.assessments.latest_version(profile_id) + 1
        assessment = await self.assessments.create(
            patient_id=profile_id, submitted_by=submitted_by, taken_by=taken_by, cycle_id=cycle_id, version=next_version
        )
        await emit_event(
            self.session,
            aggregate_type="anamnesis_assessment",
            aggregate_id=assessment["anamnesis_id"],
            event_type="anamnesis_started",
            payload={"anamnesis_id": assessment["anamnesis_id"], "patient_id": str(patient_id)},
        )
        return assessment

    async def get_current(self, patient_id: UUID) -> dict:
        profile_id = await _resolve_profile_id(self.session, patient_id)
        assessment = await self.assessments.get_latest_for_patient(profile_id)
        if not assessment:
            raise NotFoundError("No anamnesis assessment found for this patient", code="ANAMNESIS_NOT_FOUND")
        return assessment

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
