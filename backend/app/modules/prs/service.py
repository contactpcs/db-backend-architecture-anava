from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import NotFoundError
from app.core.resolve import resolve_patient_profile_id as _resolve_profile_id
from app.modules.prs.repository import (
    AssessmentInstanceRepository,
    PatientScaleAssignmentRepository,
    PrsCatalogRepository,
    PrsResponseRepository,
    PrsScaleResultRepository,
)


def _is_skipped(q: dict, questions: list[dict], given_by_qid: dict[str, str]) -> bool:
    """Server-side mirror of the frontend's computeHiddenQuestionIndices
    (prsSkipLogic.ts) — same hidden_unless rule (repository._apply_skip_logic),
    but evaluated against this instance's actual recorded given_response
    instead of live UI state, so scoring can't be tricked by what the client
    happened to render. Trigger question not answered -> not skipped (matches
    the frontend's "show until we know" default)."""
    rule = q.get("hidden_unless")
    if not rule:
        return False
    ref_question = next((x for x in questions if x["question_id"] == rule["question_id"]), None)
    if not ref_question:
        return False
    ref_given = given_by_qid.get(rule["question_id"])
    if ref_given is None:
        return False
    ref_label = next((o["label"] for o in ref_question["options"] if o["value"] == ref_given), None)
    if ref_label is None:
        return False
    ref_label = ref_label.strip().lower()
    if rule.get("hidden_when_label") and ref_label == rule["hidden_when_label"].strip().lower():
        return True
    if rule.get("visible_only_when_label") and ref_label != rule["visible_only_when_label"].strip().lower():
        return True
    return False




class PrsCatalogService:
    def __init__(self, session: AsyncSession):
        self.repo = PrsCatalogRepository(session)

    async def diseases(self) -> list[dict]:
        return await self.repo.diseases()

    async def questions_for_scale(self, scale_id: str, language: str = "en") -> list[dict]:
        # repo.questions_for_scale already existed (used internally by
        # _finalize_scale's scoring) but was never exposed to a router — no
        # endpoint anywhere returns a scale's question list. Found while
        # building the patient self-registration wizard: without this, a
        # patient has no way to actually see/answer the general_registration
        # PRS scales, so registration_status can never reach
        # general_prs_complete for ANY patient, self- or staff-registered.
        return await self.repo.questions_for_scale(scale_id, language=language)


class PatientScaleAssignmentService:
    def __init__(self, session: AsyncSession):
        self.repo = PatientScaleAssignmentRepository(session)

    async def create(self, *, patient_id: UUID, scale_id: str, disease_id: str, assessment_stage: str, assigned_by: UUID, assignment_reason: str) -> dict:
        profile_id = await _resolve_profile_id(self.repo.session, patient_id)
        return await self.repo.create(
            patient_id=profile_id, scale_id=scale_id, disease_id=disease_id, assessment_stage=assessment_stage,
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
                    patient_id=profile_id, scale_id=scale["scale_id"], disease_id=disease_id, assessment_stage=assessment_stage,
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

    async def responses_for_instance(self, instance_id: str, *, language: str | None = None) -> list[dict]:
        await self.get(instance_id)
        if language:
            # Doctor/staff display view — translate into whatever language
            # the viewer asks for, independent of what the patient answered in.
            return await self.responses.list_for_instance_translated(instance_id, language)
        return await self.responses.list_for_instance(instance_id)

    async def responses_by_scale(self, instance_id: str, *, language: str = "en") -> list[dict]:
        """Doctor/staff detailed report — every question in every scale
        assigned to this instance, not just the ones with a saved answer
        (plain responses_for_instance only has rows for what got answered,
        so a skipped or never-reached question is silently absent from it).
        Reuses _compose_scales (same scales+questions the patient actually
        saw) and _is_skipped (same skip_logic evaluation used for scoring)
        so "skipped" here means exactly what it meant when the score was
        computed — not a separate, potentially inconsistent, notion of it."""
        instance = await self.get(instance_id)
        scales = await self._compose_scales(instance, language_code=language)
        given_by_qid = {r["question_id"]: r for r in await self.responses.list_for_instance(instance_id)}
        raw_given = {qid: r["given_response"] for qid, r in given_by_qid.items()}

        result = []
        for scale in scales:
            questions_out = []
            for q in scale["questions"]:
                given = given_by_qid.get(q["question_id"])
                label = None
                if given is not None:
                    label = next((o["label"] for o in q["options"] if o["value"] == given["given_response"]), given["given_response"])
                questions_out.append({
                    "question_id": q["question_id"],
                    "question_text": q["question_text"],
                    "given_response": given["given_response"] if given else None,
                    "response_label": label,
                    "is_answered": given is not None,
                    "is_skipped": _is_skipped(q, scale["questions"], raw_given),
                })
            result.append({
                "scale_id": scale["scale_id"], "scale_code": scale["scale_code"], "scale_name": scale["scale_name"],
                "questions": questions_out,
            })
        return result

    async def _compose_scales(self, instance: dict, *, language_code: str) -> list[dict]:
        """Shared by start() and set_language() — same scales[] shape (each
        with its questions/options translated into language_code), so a
        language switch re-renders through the identical response shape the
        frontend already handles from start()."""
        profile_id = instance["patient_id"]
        assignment_repo = PatientScaleAssignmentRepository(self.session)
        assignments = await assignment_repo.list(
            patient_id=profile_id, assessment_stage=instance["assessment_stage"], disease_id=instance["disease_id"]
        )
        scale_ids = [a["scale_id"] for a in assignments]
        if not scale_ids:
            catalog_scales = await self.catalog.scales_for_disease(instance["disease_id"], [instance["assessment_stage"], "all"])
            scale_ids = [s["scale_id"] for s in catalog_scales]

        scale_meta = {s["scale_id"]: s for s in await self.catalog.scales_by_ids(scale_ids)}
        completed_scale_ids = {r["scale_id"] for r in await self.scale_results.list_for_instance(instance["instance_id"])}

        scales = []
        for scale_id in scale_ids:
            meta = scale_meta.get(scale_id)
            if not meta:
                continue
            questions = await self.catalog.questions_for_scale(scale_id, language=language_code)
            scales.append({
                "scale_id": scale_id, "scale_code": meta["scale_code"], "scale_name": meta["scale_name"],
                "is_completed": scale_id in completed_scale_ids, "questions": questions,
            })
        return scales

    async def set_language(self, instance_id: str, language_code: str) -> dict:
        """Language dropdown after "Start Assessment" — updates the instance's
        stored language_code (so it's reflected in the DB / clinical record,
        per SQL/47) and returns the same scales[] shape as start() but with
        every question/option re-translated, so the UI just re-renders."""
        instance = await self.get(instance_id)
        instance = await self.instances.update_language(instance_id, language_code)
        scales = await self._compose_scales(instance, language_code=language_code)
        return {"instance_id": instance["instance_id"], "is_resumed": True, "scales": scales}

    async def start(self, *, patient_id: UUID, disease_id: str, assessment_stage: str, session_id, cycle_id,
                     administered_by=None, initiated_by: str = "patient", language_code: str = "en") -> dict:
        """Composed in one round trip: resumes an in-progress instance for
        this patient/disease/stage instead of creating a duplicate, then
        loads every scale actually assigned to this patient (patient_scale_
        assignments — not just the disease's catalog default, since a doctor
        may have overridden the set) with full question+option data and each
        scale's completion state. Previously this only ever created a bare
        instance row and returned scales=[] — nothing downstream could
        render an actual question without a second, never-built endpoint."""
        profile_id = await _resolve_profile_id(self.session, patient_id)

        existing = await self.instances.find_in_progress(patient_id=profile_id, disease_id=disease_id, assessment_stage=assessment_stage)
        is_resumed = existing is not None
        if existing:
            instance = existing
        else:
            instance = await self.instances.create(
                disease_id=disease_id, patient_id=profile_id, session_id=session_id, cycle_id=cycle_id,
                initiated_by=initiated_by, administered_by=administered_by, assessment_stage=assessment_stage,
                language_code=language_code,
            )
            await emit_event(
                self.session, aggregate_type="prs_assessment_instance", aggregate_id=instance["instance_id"],
                event_type="prs_started", payload={"instance_id": instance["instance_id"], "patient_id": str(patient_id)},
            )

        # Resumed instance keeps whatever language it was already set to
        # (the patient picks language via set_language(), not by re-starting).
        scales = await self._compose_scales(instance, language_code=instance["language_code"])
        return {"instance_id": instance["instance_id"], "is_resumed": is_resumed, "scales": scales}

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
                language_code=item.get("language_code") or instance["language_code"],
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

        # Denominator must match what this patient was actually asked — a
        # question skipped via skip_logic (e.g. COMPASS-31's branching) never
        # got a response, so its max points must not count against them.
        # given_response is already this instance's real recorded answer
        # (server-authoritative), not live UI state, so this can't be spoofed
        # by a client sending a different scales[] than it was shown.
        given_by_qid = {r["question_id"]: r["given_response"] for r in await self.responses.list_for_instance(instance_id)}

        max_possible = 0.0
        for q in questions:
            if _is_skipped(q, questions, given_by_qid):
                continue
            max_possible += await self.catalog.max_points_for_question(q["question_id"])
        return await self.scale_results.upsert(
            instance_id=instance_id, scale_id=scale_id, calculated_value=calculated_value, max_possible=max_possible
        )

    async def results(self, instance_id: str) -> dict:
        await self.get(instance_id)
        scale_results = await self.scale_results.list_for_instance(instance_id)
        final = await self.scale_results.final_result(instance_id)
        return {"scale_results": scale_results, "final_result": final}
