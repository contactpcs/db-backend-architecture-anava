from __future__ import annotations

import builtins
import uuid
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional


class AnamnesisQuestionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(self, type: str | None = None) -> builtins.list[dict]:
        query = "SELECT * FROM anamnesis_questions WHERE status = TRUE"
        params: dict = {}
        if type is not None:
            query += " AND type = :type"
            params["type"] = type
        query += " ORDER BY section_number, display_order"
        rows = (await self.session.execute(text(query), params)).mappings().all()
        return [dict(r) for r in rows]

    async def list_with_options(self, type: str | None = None) -> builtins.list[dict]:
        """Same as list() but with each question's radio/select/checkbox
        options nested — the frontend catalog screen needs these to render
        anything beyond free-text questions, and the plain list() response
        never carried them (a real gap found wiring up the frontend)."""
        questions = await self.list(type)
        options_rows = (
            (await self.session.execute(text("SELECT * FROM anamnesis_options ORDER BY question_id, display_order"))).mappings().all()
        )
        options_by_question: dict[str, list[dict]] = {}
        for row in options_rows:
            options_by_question.setdefault(row["question_id"], []).append(dict(row))
        for q in questions:
            q["options"] = options_by_question.get(q["question_id"], [])
        return questions


class AnamnesisAssessmentRepository:
    """One live row per consultation (appointment_id) and one registration row
    per patient, edited in place (91). Rows marked 'superseded' are leftovers
    from the versioned era — every reader here skips them."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        patient_id: UUID,
        submitted_by: UUID,
        taken_by: str,
        assessment_stage: str,
        appointment_id: UUID | None = None,
    ) -> dict:
        # '-' not '/' — this ID is used as a URL path parameter
        # (GET/PATCH /anamnesis/{anamnesis_id}); '/' is a path separator and
        # breaks routing (a real bug hit and fixed during Stage 5 testing).
        anamnesis_id = f"ANA-{str(patient_id)[:8]}-{uuid.uuid4().hex[:12]}"
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO anamnesis_assessments "
                "(anamnesis_id, patient_id, submitted_by, taken_by, assessment_stage, appointment_id) "
                "VALUES (:id, :patient_id, :submitted_by, :taken_by, :assessment_stage, :appointment_id) RETURNING *"
            ),
            {
                "id": anamnesis_id,
                "patient_id": str(patient_id),
                "submitted_by": str(submitted_by),
                "taken_by": taken_by,
                "assessment_stage": assessment_stage,
                "appointment_id": str(appointment_id) if appointment_id else None,
            },
        )

    async def get(self, anamnesis_id: str) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM anamnesis_assessments WHERE anamnesis_id = :id"),
            {"id": anamnesis_id},
        )

    async def get_latest_for_patient(self, patient_id: UUID, assessment_stage: str | None = None) -> dict | None:
        query = "SELECT * FROM anamnesis_assessments WHERE patient_id = :pid AND status <> 'superseded'"
        params: dict = {"pid": str(patient_id)}
        if assessment_stage:
            query += " AND assessment_stage = :stage"
            params["stage"] = assessment_stage
        return await fetch_optional(self.session, text(query + " ORDER BY created_at DESC LIMIT 1"), params)

    async def get_by_appointment(self, appointment_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM anamnesis_assessments WHERE appointment_id = :aid AND status <> 'superseded'"),
            {"aid": str(appointment_id)},
        )

    async def get_registration(self, patient_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "SELECT * FROM anamnesis_assessments "
                "WHERE patient_id = :pid AND assessment_stage = 'registration' AND status <> 'superseded'"
            ),
            {"pid": str(patient_id)},
        )

    async def get_previous_consultation(self, patient_id: UUID, *, exclude_id: str) -> dict | None:
        """The patient's most recent other consultation anamnesis — the
        starting point a new follow-up's answers are copied from."""
        return await fetch_optional(
            self.session,
            text(
                "SELECT * FROM anamnesis_assessments "
                "WHERE patient_id = :pid AND assessment_stage = 'main' AND status <> 'superseded' "
                "AND anamnesis_id <> :ex ORDER BY created_at DESC LIMIT 1"
            ),
            {"pid": str(patient_id), "ex": exclude_id},
        )

    async def appointment_status(self, appointment_id: UUID) -> str | None:
        row = await fetch_optional(
            self.session,
            text("SELECT status FROM appointments WHERE appointment_id = :aid"),
            {"aid": str(appointment_id)},
        )
        return row["status"] if row else None

    async def mark_complete(self, anamnesis_id: str) -> dict | None:
        return await fetch_optional(
            self.session,
            text("UPDATE anamnesis_assessments SET status = 'completed', completed_at = NOW() WHERE anamnesis_id = :id RETURNING *"),
            {"id": anamnesis_id},
        )


class AnamnesisResponseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(self, *, anamnesis_id: str, question_id: str, response_value: str | None, response_values: list[str] | None) -> dict:
        response_id = f"{anamnesis_id}|{question_id}"
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO anamnesis_responses (response_id, anamnesis_id, question_id, response_value, response_values) "
                "VALUES (:id, :anamnesis_id, :question_id, :value, :values) "
                "ON CONFLICT (response_id) DO UPDATE SET response_value = EXCLUDED.response_value, "
                "response_values = EXCLUDED.response_values, updated_at = NOW() RETURNING *"
            ),
            {
                "id": response_id,
                "anamnesis_id": anamnesis_id,
                "question_id": question_id,
                "value": response_value,
                "values": response_values,
            },
        )

    async def copy_from(self, *, source_id: str, target_id: str) -> None:
        """Pre-fill a new consultation's anamnesis with another one's answers.
        response_id is "{anamnesis_id}|{question_id}" (see upsert), so the
        copies get the target's own ids."""
        await self.session.execute(
            text(
                "INSERT INTO anamnesis_responses (response_id, anamnesis_id, question_id, response_value, response_values) "
                "SELECT :tgt || '|' || question_id, :tgt, question_id, response_value, response_values "
                "FROM anamnesis_responses WHERE anamnesis_id = :src "
                "ON CONFLICT (response_id) DO NOTHING"
            ),
            {"src": source_id, "tgt": target_id},
        )

    async def list_for_assessment(self, anamnesis_id: str) -> list[dict]:
        rows = (
            (await self.session.execute(text("SELECT * FROM anamnesis_responses WHERE anamnesis_id = :id"), {"id": anamnesis_id}))
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]
