from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, NotFoundError, ValidationError
from app.core.resolve import resolve_patient_profile_id as _resolve_profile_id
from app.modules.anamnesis.repository import (
    AnamnesisAssessmentRepository,
    AnamnesisQuestionRepository,
    AnamnesisResponseRepository,
)
from app.modules.scheduling.repository import AppointmentRepository

# Consultations carry an anamnesis; device sessions do not.
CONSULTATION_TYPES = {"initial", "follow_up", "protocol_followup"}
# Once the doctor marks the consultation completed, its anamnesis is frozen.
LOCKED_APPOINTMENT_STATUSES = {"completed"}


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
        """Get-or-create — never a new version (91).

        registration: the patient's one intake anamnesis.
        main: one per consultation (initial / follow_up / protocol_followup
        appointment); a new one starts pre-filled with the patient's previous
        consultation's answers, since most history doesn't change visit to
        visit.
        """
        profile_id = await _resolve_profile_id(self.session, patient_id)

        if assessment_stage == "main":
            if appointment_id is None:
                raise ValidationError("A consultation anamnesis needs its appointment_id", code="ANAMNESIS_APPOINTMENT_REQUIRED")
            appt = await AppointmentRepository(self.session).get(appointment_id)
            if not appt:
                raise NotFoundError("Appointment not found", code="APPOINTMENT_NOT_FOUND")
            if str(appt["patient_id"]) != str(profile_id):
                raise ValidationError("Appointment belongs to a different patient", code="APPOINTMENT_PATIENT_MISMATCH")
            if appt["appointment_type"] not in CONSULTATION_TYPES:
                raise ValidationError("Anamnesis is only recorded for consultations", code="ANAMNESIS_NOT_A_CONSULTATION")
            existing = await self.assessments.get_by_appointment(appointment_id)
            if existing:
                return existing
            if appt["status"] in LOCKED_APPOINTMENT_STATUSES:
                raise BusinessRuleError("This consultation is completed — its anamnesis can no longer be changed", code="ANAMNESIS_LOCKED")
        else:
            appointment_id = None
            existing = await self.assessments.get_registration(profile_id)
            if existing:
                return existing

        try:
            # Savepoint: losing the race on uq_anamnesis_one_per_appointment /
            # uq_anamnesis_one_registration_per_patient must not abort the
            # request's transaction — the winner's row is simply returned.
            async with self.session.begin_nested():
                assessment = await self.assessments.create(
                    patient_id=profile_id,
                    submitted_by=submitted_by,
                    taken_by=taken_by,
                    assessment_stage=assessment_stage,
                    appointment_id=appointment_id,
                )
        except IntegrityError:
            winner = (
                await self.assessments.get_by_appointment(appointment_id)
                if appointment_id
                else await self.assessments.get_registration(profile_id)
            )
            if winner:
                return winner
            raise

        if assessment_stage == "main":
            previous = await self.assessments.get_previous_consultation(profile_id, exclude_id=assessment["anamnesis_id"])
            if previous:
                await self.responses.copy_from(source_id=previous["anamnesis_id"], target_id=assessment["anamnesis_id"])

        await emit_event(
            self.session,
            aggregate_type="anamnesis_assessment",
            aggregate_id=assessment["anamnesis_id"],
            event_type="anamnesis_started",
            payload={"anamnesis_id": assessment["anamnesis_id"], "patient_id": str(patient_id)},
        )
        return assessment

    async def get_current(self, patient_id: UUID, assessment_stage: str | None = None) -> dict:
        """The patient's latest anamnesis (of this stage) — the registration
        intake, or their most recent consultation's."""
        profile_id = await _resolve_profile_id(self.session, patient_id)
        assessment = await self.assessments.get_latest_for_patient(profile_id, assessment_stage)
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
        # Edited in place until its consultation is completed — the server
        # enforces the lock, not the browser.
        if assessment["status"] == "superseded":
            raise BusinessRuleError("This is an old anamnesis version and cannot be changed", code="ANAMNESIS_SUPERSEDED")
        if assessment["appointment_id"] and (
            await self.assessments.appointment_status(assessment["appointment_id"]) in LOCKED_APPOINTMENT_STATUSES
        ):
            raise BusinessRuleError("This consultation is completed — its anamnesis can no longer be changed", code="ANAMNESIS_LOCKED")

        for item in items:
            await self.responses.upsert(
                anamnesis_id=anamnesis_id,
                question_id=item["question_id"],
                response_value=item.get("response_value"),
                response_values=item.get("response_values"),
            )

        if complete:
            # Defense in depth against a "completed" record with nothing in
            # it — found live via the doctor-side form: a per-question
            # autosave that silently failed left this call's own `items`
            # empty (submit() only ever resubmits its caller's answers, it
            # doesn't resend everything previously autosaved), so the record
            # was marked complete with zero response rows. items being empty
            # here is normal on a real submit (responses already landed via
            # earlier autosave calls) — only refuse when NO responses exist
            # for this assessment at all, from this call or any earlier one.
            if not items and not await self.responses.list_for_assessment(anamnesis_id):
                raise BusinessRuleError(
                    "Cannot complete an anamnesis with no recorded responses",
                    code="ANAMNESIS_EMPTY",
                )
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
