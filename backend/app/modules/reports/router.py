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
    return await ReportsService(db).doctor_patients_overview(ctx.user_id, disease_id)
