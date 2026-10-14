from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
from app.modules.final_reports import schemas as s
from app.modules.final_reports.service import FinalReportService

router = APIRouter()

# Only a doctor generates. Doctors and admins read previous reports; CAs and
# receptionists do not (plan decision 9).
_STAFF_READERS = ("doctor", "clinic_admin", "regional_admin", "super_admin")


@router.post("/patients/{patient_id}/final-report/preview", response_model=s.FinalReportPreview)
async def preview_final_report(
    patient_id: UUID,
    body: s.FinalReportDraft,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("doctor")),
):
    """The sections as they would be signed with this selection, the
    selection they hold, and every candidate each section's pencil can offer.
    Nothing is stored."""
    selection = body.selection.model_dump() if body.selection else None
    return await FinalReportService(db).preview(patient_id, body.scope, ctx, selection)


@router.post("/patients/{patient_id}/final-reports", response_model=s.FinalReportRead, status_code=201)
async def sign_final_report(
    patient_id: UUID,
    body: s.FinalReportSign,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("doctor")),
):
    """Sign = freeze. The sections are re-assembled server-side, stored
    immutably and released to the patient in the same transaction."""
    return await FinalReportService(db).sign(patient_id, body, ctx)


@router.get("/patients/{patient_id}/final-reports", response_model=list[s.FinalReportMeta])
async def list_patient_final_reports(patient_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_STAFF_READERS))):
    return await FinalReportService(db).list_for_patient(patient_id, ctx)


@router.get("/me/final-reports", response_model=list[s.FinalReportMeta])
async def list_my_final_reports(
    scope: s.Scope | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    sort: str = Query("newest", pattern="^(newest|oldest)$"),
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("patient")),
):
    """The patient's own signed reports — My Results > Final Reports."""
    return await FinalReportService(db).list_mine(ctx, scope=scope, date_from=date_from, date_to=date_to, newest_first=sort == "newest")


@router.get("/final-reports/{report_id}", response_model=s.FinalReportRead)
async def get_final_report(report_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_STAFF_READERS, "patient"))):
    return await FinalReportService(db).get(report_id, ctx)
