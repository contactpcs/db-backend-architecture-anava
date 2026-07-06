from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import NotFoundError
from app.modules.prs.repository import (
    AssessmentInstanceRepository,
    PatientScaleAssignmentRepository,
    PrsCatalogRepository,
    PrsResponseRepository,
    PrsScaleResultRepository,
)


async def _resolve_profile_id(session: AsyncSession, patient_id: UUID) -> UUID:
    """Same fix as anamnesis/service.py — prs_assessment_instances.patient_id
    (and patient_scale_assignments.patient_id) reference profiles(id), not
    patients.patient_id. API accepts patients.patient_id consistently;
    resolved here."""
    from app.modules.patients.repository import PatientRepository

    patient = await PatientRepository(session).get(patient_id)
    if not patient:
        raise NotFoundError("Patient not found", code="PATIENT_NOT_FOUND")
    return patient["profile_id"]


class PrsCatalogService:
    def __init__(self, session: AsyncSession):
        self.repo = PrsCatalogRepository(session)

    async def diseases(self) -> list[dict]:
        return await self.repo.diseases()

    async def questions_for_scale(self, scale_id: str) -> list[dict]:
        # repo.questions_for_scale already existed (used internally by
        # _finalize_scale's scoring) but was never exposed to a router — no
        # endpoint anywhere returns a scale's question list. Found while
        # building the patient self-registration wizard: without this, a
        # patient has no way to actually see/answer the general_registration
        # PRS scales, so registration_status can never reach
        # general_prs_complete for ANY patient, self- or staff-registered.
        return await self.repo.questions_for_scale(scale_id)


class PatientScaleAssignmentService:
    def __init__(self, session: AsyncSession):
        self.repo = PatientScaleAssignmentRepository(session)

    async def create(self, *, patient_id: UUID, scale_id: str, assessment_stage: str, assigned_by: UUID, assignment_reason: str) -> dict:
        profile_id = await _resolve_profile_id(self.repo.session, patient_id)
        return await self.repo.create(
            patient_id=profile_id, scale_id=scale_id, assessment_stage=assessment_stage,
            assigned_by=assigned_by, assignment_reason=assignment_reason,
        )

    async def list(self, patient_id: UUID, *, assessment_stage: str | None = None) -> list[dict]:
        profile_id = await _resolve_profile_id(self.repo.session, patient_id)
        return await self.repo.list(patient_id=profile_id, assessment_stage=assessment_stage)

    async def auto_assign_for_disease(self, patient_id: UUID, disease_id: str, assessment_stage: str, assigned_by: UUID) -> list[dict]:
        """Master Doc Section 9.3 — at registration, scales are auto-assigned
        based on disease_selection. Assigns every scale mapped to this disease
        whose applicable_for matches this stage (or 'all')."""
        profile_id = await _resolve_profile_id(self.repo.session, patient_id)
        catalog = PrsCatalogRepository(self.repo.session)
        scales = await catalog.scales_for_disease(disease_id, [assessment_stage, "all"])
        assigned = []
        for scale in scales:
            assigned.append(
                await self.repo.create(
                    patient_id=profile_id, scale_id=scale["scale_id"], assessment_stage=assessment_stage,
                    assigned_by=assigned_by, assignment_reason="auto_disease_match",
                )
            )
        return assigned


class PrsAssessmentService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.instances = AssessmentInstanceRepository(session)
        self.responses = PrsResponseRepository(session)
        self.scale_results = PrsScaleResultRepository(session)
        self.catalog = PrsCatalogRepository(session)

    async def list_for_patient(self, patient_id: UUID, *, assessment_stage: str | None = None) -> list[dict]:
        """Used by the admin/staff patient-detail view to show a patient's
        PRS history (e.g. their general_registration assessment) without
        needing to already know an instance_id."""
        profile_id = await _resolve_profile_id(self.session, patient_id)
        return await self.instances.list_for_patient(profile_id, assessment_stage=assessment_stage)

    async def start(self, *, patient_id: UUID, disease_id: str, assessment_stage: str, session_id, cycle_id,
                     administered_by=None, initiated_by: str = "patient") -> dict:
        profile_id = await _resolve_profile_id(self.session, patient_id)
        instance = await self.instances.create(
            disease_id=disease_id, patient_id=profile_id, session_id=session_id, cycle_id=cycle_id,
            initiated_by=initiated_by, administered_by=administered_by, assessment_stage=assessment_stage,
        )
        await emit_event(
            self.session, aggregate_type="prs_assessment_instance", aggregate_id=instance["instance_id"],
            event_type="prs_started", payload={"instance_id": instance["instance_id"], "patient_id": str(patient_id)},
        )
        return instance

    async def get(self, instance_id: str) -> dict:
        instance = await self.instances.get(instance_id)
        if not instance:
            raise NotFoundError("PRS assessment instance not found", code="PRS_INSTANCE_NOT_FOUND")
        return instance

    async def submit_responses(self, instance_id: str, *, items: list[dict], finalize_scale_id: str | None) -> dict:
        instance = await self.get(instance_id)

        for item in items:
            points = await self.catalog.option_points(item["question_id"], item["given_response"])
            await self.responses.upsert(
                instance_id=instance_id, question_id=item["question_id"],
                given_response=item["given_response"], response_value=points,
            )

        if finalize_scale_id:
            await self._finalize_scale(instance_id, finalize_scale_id)
            await emit_event(
                self.session, aggregate_type="prs_assessment_instance", aggregate_id=instance_id,
                event_type="prs_scale_scored", payload={"instance_id": instance_id, "scale_id": finalize_scale_id},
            )
            # recalculate_final_result is a DEFERRABLE INITIALLY DEFERRED constraint
            # trigger (SQL/07_prs_tables.sql) — it only fires at COMMIT, which hasn't
            # happened yet inside this same request/transaction. Without forcing it
            # now, the re-fetch below always sees the pre-trigger 'in_progress' status,
            # so registration_status could never auto-advance past anamnesis_complete
            # even when this was genuinely the last scale (confirmed: instances were
            # actually 'completed' in the DB after commit, but the patient stayed
            # stuck mid-wizard because this check ran too early to see it).
            await self.session.execute(text("SET CONSTRAINTS trg_recalculate_final_result IMMEDIATE"))
            instance = await self.get(instance_id)  # re-fetch — the trigger has now run
            if instance["status"] == "completed" and instance["assessment_stage"] == "general_registration":
                await emit_event(
                    self.session, aggregate_type="prs_assessment_instance", aggregate_id=instance_id,
                    event_type="prs_completed", payload={"instance_id": instance_id, "patient_id": str(instance["patient_id"])},
                )
                from app.modules.patients.repository import PatientRepository
                from app.modules.patients.service import PatientService

                patient = await PatientRepository(self.session).get_by_profile_id(instance["patient_id"])
                if patient:
                    await PatientService(self.session).advance_registration_status(patient["patient_id"])
        return instance

    async def _finalize_scale(self, instance_id: str, scale_id: str) -> dict:
        calculated_value, _answered = await self.responses.sum_for_scale(instance_id, scale_id)
        questions = await self.catalog.questions_for_scale(scale_id)
        max_possible = 0.0
        for q in questions:
            max_possible += await self.catalog.max_points_for_question(q["question_id"])
        return await self.scale_results.upsert(
            instance_id=instance_id, scale_id=scale_id, calculated_value=calculated_value, max_possible=max_possible
        )

    async def results(self, instance_id: str) -> dict:
        await self.get(instance_id)
        scale_results = await self.scale_results.list_for_instance(instance_id)
        final = await self.scale_results.final_result(instance_id)
        return {"scale_results": scale_results, "final_result": final}
