from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
from app.modules.reports import schemas as s
from app.modules.reports.service import ReportsService

router = APIRouter()


@router.get("/reports/doctor/patients-overview", response_model=s.PatientsOverviewResponse)
async def doctor_patients_overview(disease_id: str, db=Depends(get_db), ctx: RequestContext = Depends(require_role("doctor"))):
    # ctx.user_id is the doctor's own profile id — doctor_patient_assignments.
    # doctor_id is a profile id too (see repository.py docstring), so no
    # lookup through the `doctors` table is needed to scope this query.
    return await ReportsService(db).doctor_patients_overview(UUID(ctx.user_id), disease_id)


@router.get("/reports/doctor/diseases-overview", response_model=s.DiseasesOverviewResponse)
async def doctor_diseases_overview(db=Depends(get_db), ctx: RequestContext = Depends(require_role("doctor"))):
    # All-diseases cohort landing view (Backend Design v1 Section 5.1) —
    # every condition this doctor has patients tracked for, with
    # improving/stable/worsening counts per disease.
    return await ReportsService(db).doctor_diseases_overview(UUID(ctx.user_id))


@router.get(
    "/reports/doctor/patients/{patient_id}/scale-trajectories",
    response_model=s.ScaleTrajectoriesResponse,
)
async def patient_scale_trajectories(
    patient_id: UUID, disease_id: str, db=Depends(get_db), ctx: RequestContext = Depends(require_role("doctor"))
):
    # Per-scale history for one patient (Backend Design v1 Section 5.4).
    # patient_id outside this doctor's assignments returns an empty list.
    return await ReportsService(db).patient_scale_trajectories(UUID(ctx.user_id), patient_id, disease_id)


@router.get("/reports/doctor/weekly-trend", response_model=s.WeeklyTrendResponse)
async def doctor_weekly_trend(
    disease_id: str, weeks: int = 8, db=Depends(get_db), ctx: RequestContext = Depends(require_role("doctor"))
):
    # Weekly composite trend table (Backend Design v1 Section 5.7) — one row
    # per patient, one column per ISO week, doctor-selectable window.
    weeks = max(1, min(weeks, 52))
    return await ReportsService(db).doctor_weekly_trend(UUID(ctx.user_id), disease_id, weeks)


@router.get("/reports/doctor/protocol-outcomes", response_model=s.ProtocolOutcomesResponse)
async def doctor_protocol_outcomes(disease_id: str, db=Depends(get_db), ctx: RequestContext = Depends(require_role("doctor"))):
    # Treatment Protocol Outcomes + Protocol vs. Scale Outcomes (Backend
    # Design v1 Sections 5.5/5.6) — one call, since both derive from the
    # same protocol/scale-session join.
    return await ReportsService(db).doctor_protocol_outcomes(UUID(ctx.user_id), disease_id)
