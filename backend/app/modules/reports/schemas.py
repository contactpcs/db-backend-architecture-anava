from datetime import datetime

from pydantic import BaseModel


class VisitScore(BaseModel):
    instance_id: str
    date: datetime | None
    score: float
    severity_level: str | None
    severity_label: str | None


class PatientOverviewRow(BaseModel):
    patient_id: str
    name: str
    first_assessment_date: datetime | None
    assessment_count: int
    overall_change: float | None
    visits: list[VisitScore]


class DashboardKPIs(BaseModel):
    patients: int
    avg_assessments: float
    avg_score_change: float | None
    responders_pct: float | None
    remitters_pct: float


class PatientsOverviewResponse(BaseModel):
    kpis: DashboardKPIs
    patients: list[PatientOverviewRow]
