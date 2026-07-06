from __future__ import annotations

import uuid
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional


class PrsCatalogRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def diseases(self) -> list[dict]:
        rows = (await self.session.execute(text("SELECT * FROM prs_diseases WHERE status = TRUE ORDER BY disease_name"))).mappings().all()
        return [dict(r) for r in rows]

    async def scales_for_disease(self, disease_id: str, applicable_for: list[str]) -> list[dict]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT sc.* FROM prs_scales sc "
                    "JOIN prs_disease_scale_map m ON m.scale_id = sc.scale_id "
                    "WHERE m.disease_id = :disease_id AND sc.applicable_for = ANY(:applicable_for)"
                ),
                {"disease_id": disease_id, "applicable_for": applicable_for},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def questions_for_scale(self, scale_id: str) -> list[dict]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT q.* FROM prs_questions q "
                    "JOIN prs_scale_question_map m ON m.question_id = q.question_id "
                    "WHERE m.scale_id = :scale_id ORDER BY m.display_order"
                ),
                {"scale_id": scale_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def option_points(self, question_id: str, option_value: str) -> float | None:
        row = (
            await self.session.execute(
                text("SELECT points FROM prs_options WHERE question_id = :qid AND option_value = :val"),
                {"qid": question_id, "val": option_value},
            )
        ).mappings().first()
        return float(row["points"]) if row else None

    async def max_points_for_question(self, question_id: str) -> float:
        row = (
            await self.session.execute(
                text("SELECT COALESCE(MAX(points), 0) AS m FROM prs_options WHERE question_id = :qid"),
                {"qid": question_id},
            )
        ).mappings().one()
        return float(row["m"])


class PatientScaleAssignmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, patient_id: UUID, scale_id: str, assessment_stage: str, assigned_by: UUID, assignment_reason: str) -> dict:
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO patient_scale_assignments (patient_id, scale_id, assessment_stage, assigned_by, assignment_reason) "
                "VALUES (:patient_id, :scale_id, :assessment_stage, :assigned_by, :assignment_reason) RETURNING *"
            ),
            {
                "patient_id": str(patient_id), "scale_id": scale_id, "assessment_stage": assessment_stage,
                "assigned_by": str(assigned_by), "assignment_reason": assignment_reason,
            },
        )

    async def list(self, *, patient_id: UUID, assessment_stage: str | None = None) -> list[dict]:
        clauses, params = ["patient_id = :pid", "is_active = TRUE"], {"pid": str(patient_id)}
        if assessment_stage:
            clauses.append("assessment_stage = :stage")
            params["stage"] = assessment_stage
        rows = (
            await self.session.execute(text(f"SELECT * FROM patient_scale_assignments WHERE {' AND '.join(clauses)}"), params)
        ).mappings().all()
        return [dict(r) for r in rows]


class AssessmentInstanceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, disease_id: str, patient_id: UUID, session_id, cycle_id, initiated_by: str,
                      administered_by, assessment_stage: str) -> dict:
        # '-' not '/' — used as a URL path parameter, same fix as anamnesis_id.
        instance_id = f"{str(patient_id)[:8]}-{uuid.uuid4().hex[:8]}"
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO prs_assessment_instances (instance_id, disease_id, patient_id, session_id, cycle_id, "
                "initiated_by, administered_by, assessment_stage) VALUES "
                "(:id, :disease_id, :patient_id, :session_id, :cycle_id, :initiated_by, :administered_by, :stage) "
                "RETURNING *"
            ),
            {
                "id": instance_id, "disease_id": disease_id, "patient_id": str(patient_id),
                "session_id": str(session_id) if session_id else None, "cycle_id": str(cycle_id) if cycle_id else None,
                "initiated_by": initiated_by, "administered_by": str(administered_by) if administered_by else None,
                "stage": assessment_stage,
            },
        )

    async def get(self, instance_id: str) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM prs_assessment_instances WHERE instance_id = :id"), {"id": instance_id})

    async def list_for_patient(self, patient_profile_id: UUID, *, assessment_stage: str | None = None) -> list[dict]:
        clauses, params = ["patient_id = :pid"], {"pid": str(patient_profile_id)}
        if assessment_stage:
            clauses.append("assessment_stage = :stage")
            params["stage"] = assessment_stage
        rows = (
            await self.session.execute(
                text(f"SELECT * FROM prs_assessment_instances WHERE {' AND '.join(clauses)} ORDER BY started_at DESC"),
                params,
            )
        ).mappings().all()
        return [dict(r) for r in rows]


class PrsResponseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(self, *, instance_id: str, question_id: str, given_response: str, response_value: float | None) -> dict:
        response_id = f"{instance_id}/{question_id.replace('/', '-')}"
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO prs_responses (response_id, instance_id, question_id, given_response, response_value) "
                "VALUES (:id, :instance_id, :question_id, :given, :value) "
                "ON CONFLICT (response_id) DO UPDATE SET given_response = EXCLUDED.given_response, "
                "response_value = EXCLUDED.response_value RETURNING *"
            ),
            {"id": response_id, "instance_id": instance_id, "question_id": question_id, "given": given_response, "value": response_value},
        )

    async def sum_for_scale(self, instance_id: str, scale_id: str) -> tuple[float, int]:
        row = (
            await self.session.execute(
                text(
                    "SELECT COALESCE(SUM(r.response_value), 0) AS total, COUNT(*) AS n "
                    "FROM prs_responses r JOIN prs_scale_question_map m ON m.question_id = r.question_id "
                    "WHERE r.instance_id = :instance_id AND m.scale_id = :scale_id"
                ),
                {"instance_id": instance_id, "scale_id": scale_id},
            )
        ).mappings().one()
        return float(row["total"]), row["n"]


class PrsScaleResultRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(self, *, instance_id: str, scale_id: str, calculated_value: float, max_possible: float) -> dict:
        scale_result_id = f"{instance_id}/{scale_id.replace('/', '-')}"
        # Insert/update fires the existing recalculate_final_result() trigger
        # (SQL/07_prs_tables.sql) which aggregates into prs_final_results.
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO prs_scale_results (scale_result_id, instance_id, scale_id, calculated_value, max_possible) "
                "VALUES (:id, :instance_id, :scale_id, :calc, :max) "
                "ON CONFLICT (scale_result_id) DO UPDATE SET calculated_value = EXCLUDED.calculated_value, "
                "max_possible = EXCLUDED.max_possible RETURNING *"
            ),
            {"id": scale_result_id, "instance_id": instance_id, "scale_id": scale_id, "calc": calculated_value, "max": max_possible},
        )

    async def list_for_instance(self, instance_id: str) -> list[dict]:
        rows = (await self.session.execute(text("SELECT * FROM prs_scale_results WHERE instance_id = :id"), {"id": instance_id})).mappings().all()
        return [dict(r) for r in rows]

    async def final_result(self, instance_id: str) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM prs_final_results WHERE instance_id = :id"), {"id": instance_id})
