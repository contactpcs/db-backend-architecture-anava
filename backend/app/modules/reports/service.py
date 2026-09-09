from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.reports.repository import ReportsRepository


def _group_by_patient(rows: list[dict]) -> list[dict]:
    """Raw joined rows (repository.doctor_patients_overview) -> one dict per
    patient with a chronological visits[] list. A patient row with no
    completed instance (instance_id NULL, from the LEFT JOIN) keeps an empty
    visits[] rather than a fake entry."""
    by_patient: dict = {}
    order: list = []
    for r in rows:
        pid = r["patient_id"]
        if pid not in by_patient:
            by_patient[pid] = {
                "patient_id": str(pid),
                "name": f"{r['first_name']} {r['last_name']}".strip(),
                "visits": [],
            }
            order.append(pid)
        if r["instance_id"] is not None and r["composite_score"] is not None:
            by_patient[pid]["visits"].append(
                {
                    "instance_id": r["instance_id"],
                    "date": r["completed_at"] or r["started_at"],
                    "score": float(r["composite_score"]),
                    "severity_level": r["composite_severity_level"],
                    "severity_label": r["composite_severity_label"],
                }
            )
    return [by_patient[pid] for pid in order]


def compute_dashboard_overview(patients: list[dict]) -> dict:
    """Pure aggregation over already-grouped per-patient visit lists — no DB,
    unit-testable directly (test_reports_service.py). Business rules per
    Documents/Anava_Doctor_Portal_Analytics_Dashboard_v1.docx Section 5.1/
    DECISION table:
      - baseline = a patient's FIRST completed instance for this disease
        (never resets on a new treatment protocol)
      - latest = their most recent completed instance
      - overall_change = latest - baseline (negative = improvement, since
        every score here is already direction-corrected so higher = worse)
      - responder = >=50% drop from baseline to latest
      - remitter = latest visit's severity_level == "normal"
    Responders/remitters/avg_score_change need a baseline AND a follow-up,
    so patients with fewer than 2 visits are excluded from those three (but
    still counted in `patients` and included in the table with whatever
    visits they do have).
    """
    patient_rows = []
    changes: list[float] = []
    responders = 0
    remitters = 0
    comparable = 0
    assessment_counts: list[int] = []

    for p in patients:
        visits = sorted(p["visits"], key=lambda v: v["date"] or "")
        assessment_counts.append(len(visits))
        overall_change = None
        if len(visits) >= 2:
            baseline, latest = visits[0], visits[-1]
            overall_change = round(latest["score"] - baseline["score"], 2)
            changes.append(overall_change)
            comparable += 1
            if baseline["score"] > 0 and (baseline["score"] - latest["score"]) / baseline["score"] >= 0.5:
                responders += 1
        if visits and visits[-1]["severity_level"] == "normal":
            remitters += 1

        patient_rows.append(
            {
                "patient_id": p["patient_id"],
                "name": p["name"],
                "first_assessment_date": visits[0]["date"] if visits else None,
                "assessment_count": len(visits),
                "overall_change": overall_change,
                "visits": visits,
            }
        )

    total = len(patients)
    kpis = {
        "patients": total,
        "avg_assessments": round(sum(assessment_counts) / total, 2) if total else 0.0,
        "avg_score_change": round(sum(changes) / len(changes), 2) if changes else None,
        "responders_pct": round(responders / comparable * 100, 1) if comparable else None,
        "remitters_pct": round(remitters / total * 100, 1) if total else 0.0,
    }
    return {"kpis": kpis, "patients": patient_rows}


class ReportsService:
    def __init__(self, session: AsyncSession):
        self.repo = ReportsRepository(session)

    async def doctor_patients_overview(self, doctor_profile_id: UUID, disease_id: str) -> dict:
        rows = await self.repo.doctor_patients_overview(doctor_profile_id, disease_id)
        patients = _group_by_patient(rows)
        return compute_dashboard_overview(patients)
