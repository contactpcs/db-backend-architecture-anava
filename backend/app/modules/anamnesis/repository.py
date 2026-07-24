from __future__ import annotations

import builtins
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional


class AnamnesisQuestionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(self) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM anamnesis_questions WHERE status = TRUE ORDER BY section_number, display_order")
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def list_with_options(self) -> builtins.list[dict]:
        """Same as list() but with each question's radio/select/checkbox
        options nested — the frontend catalog screen needs these to render
        anything beyond free-text questions, and the plain list() response
        never carried them (a real gap found wiring up the frontend)."""
        questions = await self.list()
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
    def __init__(self, session: AsyncSession):
        self.session = session

    async def latest_version(self, patient_id: UUID) -> int:
        row = (
            (
                await self.session.execute(
                    text("SELECT COALESCE(MAX(version), 0) AS v FROM anamnesis_assessments WHERE patient_id = :pid"),
                    {"pid": str(patient_id)},
                )
            )
            .mappings()
            .one()
        )
        return row["v"]

    async def create(self, *, patient_id: UUID, submitted_by: UUID, taken_by: str, cycle_id, version: int) -> dict:
        # '-' not '/' — this ID is used as a URL path parameter
        # (GET/PATCH /anamnesis/{anamnesis_id}); '/' is a path separator and
        # breaks routing (a real bug hit and fixed during Stage 5 testing).
        anamnesis_id = f"ANA-{str(patient_id)[:8]}-{version:03d}"
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO anamnesis_assessments (anamnesis_id, patient_id, submitted_by, taken_by, cycle_id, version) "
                "VALUES (:id, :patient_id, :submitted_by, :taken_by, :cycle_id, :version) RETURNING *"
            ),
            {
                "id": anamnesis_id,
                "patient_id": str(patient_id),
                "submitted_by": str(submitted_by),
                "taken_by": taken_by,
                "cycle_id": str(cycle_id) if cycle_id else None,
                "version": version,
            },
        )

    async def get(self, anamnesis_id: str) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM anamnesis_assessments WHERE anamnesis_id = :id"),
            {"id": anamnesis_id},
        )

    async def get_latest_for_patient(self, patient_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM anamnesis_assessments WHERE patient_id = :pid ORDER BY version DESC LIMIT 1"),
            {"pid": str(patient_id)},
        )

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

    async def list_for_assessment(self, anamnesis_id: str) -> list[dict]:
        rows = (
            (await self.session.execute(text("SELECT * FROM anamnesis_responses WHERE anamnesis_id = :id"), {"id": anamnesis_id}))
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]
