from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ReportsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def doctor_patients_overview(self, doctor_profile_id, disease_id: str) -> list[dict]:
        """One row per (patient, completed instance) for every patient
        currently assigned to this doctor, for one disease. A patient with
        no completed instance yet for this disease still gets one row (all
        instance/score columns NULL via the LEFT JOINs) so they show up in
        the table as "0 assessments" rather than silently vanishing.

        doctor_patient_assignments.doctor_id/patient_id are both profile ids
        (core.profiles.id) — same id space as prs_assessment_instances.
        patient_id and ctx.user_id, so no lookup through the `doctors` or
        `patients` tables is needed to scope this query.
        """
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT p.id AS patient_id, p.first_name, p.last_name, "
                        "pai.instance_id, pai.started_at, pai.completed_at, "
                        "pfr.composite_score, pfr.composite_severity_level, pfr.composite_severity_label "
                        "FROM doctor_patient_assignments dpa "
                        "JOIN profiles p ON p.id = dpa.patient_id "
                        "LEFT JOIN prs_assessment_instances pai "
                        "  ON pai.patient_id = dpa.patient_id AND pai.disease_id = :disease_id AND pai.status = 'completed' "
                        "LEFT JOIN prs_final_results pfr ON pfr.instance_id = pai.instance_id "
                        "WHERE dpa.doctor_id = :doctor_id AND dpa.status = 'active' "
                        "ORDER BY p.id, pai.started_at"
                    ),
                    {"doctor_id": str(doctor_profile_id), "disease_id": disease_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]
