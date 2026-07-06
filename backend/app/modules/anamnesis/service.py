from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import NotFoundError
from app.modules.anamnesis.repository import (
    AnamnesisAssessmentRepository,
    AnamnesisQuestionRepository,
    AnamnesisResponseRepository,
)


async def _resolve_profile_id(session: AsyncSession, patient_id: UUID) -> UUID:
    """anamnesis_assessments.patient_id (like most 'patient_id' FK columns in
    this schema) actually references profiles(id), NOT patients.patient_id —
    two different UUIDs for the same person. The API accepts patients.patient_id
    everywhere for consistency with GET /patients/{id}; this resolves it to the
    profiles.id the DB column actually wants. (Found as a real bug during Stage
    6 testing — was passing patients.patient_id straight through before this.)"""
    from app.modules.patients.repository import PatientRepository

    patient = await PatientRepository(session).get(patient_id)
    if not patient:
        raise NotFoundError("Patient not found", code="PATIENT_NOT_FOUND")
    return patient["profile_id"]


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
            self.session, aggregate_type="anamnesis_assessment", aggregate_id=assessment["anamnesis_id"],
            event_type="anamnesis_started", payload={"anamnesis_id": assessment["anamnesis_id"], "patient_id": str(patient_id)},
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
                anamnesis_id=anamnesis_id, question_id=item["question_id"],
                response_value=item.get("response_value"), response_values=item.get("response_values"),
            )

        if complete:
            assessment = await self.assessments.mark_complete(anamnesis_id)  # type: ignore[assignment]
            await emit_event(
                self.session, aggregate_type="anamnesis_assessment", aggregate_id=anamnesis_id,
                event_type="anamnesis_completed", payload={"anamnesis_id": anamnesis_id, "patient_id": str(assessment["patient_id"])},
            )
            from app.modules.patients.repository import PatientRepository
            from app.modules.patients.service import PatientService

            patient = await PatientRepository(self.session).get_by_profile_id(assessment["patient_id"])
            if patient:
                await PatientService(self.session).advance_registration_status(patient["patient_id"])
        return assessment  # type: ignore[return-value]
