from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
from app.modules.audit import schemas as s
from app.modules.audit.service import FULL_TRAIL_ROLES, ClinicalActivityService

router = APIRouter()


@router.get("/patients/{patient_id}/clinical-activity", response_model=list[s.ClinicalActivityEntry])
async def list_patient_clinical_activity(
    patient_id: UUID,
    actor_id: UUID | None = Query(None, description="profiles.id - only this staff member's actions"),
    limit: int = Query(200, ge=1, le=500),
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*FULL_TRAIL_ROLES, "clinical_assistant")),
):
    """Who did what for this patient, newest first. A doctor or admin gets
    the full trail, a clinical assistant the limited one (see service)."""
    return await ClinicalActivityService(db).for_patient(patient_id, ctx, actor_id=actor_id, limit=limit)
