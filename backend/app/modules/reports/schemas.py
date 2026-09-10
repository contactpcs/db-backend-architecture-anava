from datetime import datetime

from pydantic import BaseModel


class VisitScore(BaseModel):
    composite_id: str
    date: datetime | None
    score: float
    severity_level: str | None
    severity_label: str | None
    is_provisional: bool


class PatientOverviewRow(BaseModel):
    patient_id: str
    name: str
    first_assessment_date: datetime | None
    assessment_count: int
    overall_change: float | None
    trend: str  # "improving" | "stable" | "worsening" | "insufficient_data"
    visits: list[VisitScore]


class DashboardKPIs(BaseModel):
    patients: int
    improving: int
    stable: int
    worsening: int
    insufficient_data: int
    avg_assessments: float
    avg_score_change: float | None
    responders_pct: float | None
    remitters_pct: float
    provisional_pct: float


class PatientsOverviewResponse(BaseModel):
    kpis: DashboardKPIs
    patients: list[PatientOverviewRow]


class DiseaseOverviewRow(BaseModel):
    disease_id: str
    disease_name: str
    total: int
    improving: int
    stable: int
    worsening: int
    insufficient_data: int


class DiseasesOverviewResponse(BaseModel):
    diseases: list[DiseaseOverviewRow]


class ScaleTrajectoryPoint(BaseModel):
    date: datetime | None
    score: float
    severity_level: str | None
    severity_label: str | None


class ScaleTrajectory(BaseModel):
    scale_code: str
    scale_name: str
    points: list[ScaleTrajectoryPoint]


class ScaleTrajectoriesResponse(BaseModel):
    scales: list[ScaleTrajectory]


class WeeklyTrendPatientRow(BaseModel):
    patient_id: str
    name: str
    scores: list[float | None]


class WeeklyTrendResponse(BaseModel):
    weeks: list[datetime]
    patients: list[WeeklyTrendPatientRow]


class ProtocolOutcomeRow(BaseModel):
    protocol_label: str
    total: int
    improving: int
    stable: int
    worsening: int
    insufficient_data: int


class ProtocolScaleHeatmapCell(BaseModel):
    protocol_label: str
    scale_code: str
    scale_name: str
    avg_change: float
    n: int


class ProtocolOutcomesResponse(BaseModel):
    protocols: list[ProtocolOutcomeRow]
    heatmap: list[ProtocolScaleHeatmapCell]
