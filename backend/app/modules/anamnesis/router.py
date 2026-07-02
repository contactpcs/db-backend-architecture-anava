from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
from app.modules.anamnesis import schemas as s
from app.modules.anamnesis.service import AnamnesisCatalogService, AnamnesisService

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")


@router.get("/anamnesis-catalog", response_model=list[s.AnamnesisQuestionRead])
async def list_anamnesis_catalog(db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await AnamnesisCatalogService(db).list_questions()


@router.post("/patients/{patient_id}/anamnesis", response_model=s.AnamnesisAssessmentRead, status_code=201)
async def start_anamnesis(
    patient_id: UUID, body: s.AnamnesisStart, db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    return await AnamnesisService(db).start(patient_id, submitted_by=UUID(ctx.user_id), taken_by=body.taken_by)


@router.get("/patients/{patient_id}/anamnesis", response_model=s.AnamnesisAssessmentRead)
async def get_current_anamnesis(patient_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await AnamnesisService(db).get_current(patient_id)


@router.get("/anamnesis/{anamnesis_id}/responses", response_model=list[s.AnamnesisResponseRead])
async def get_anamnesis_responses(anamnesis_id: str, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await AnamnesisService(db).get_responses(anamnesis_id)


@router.patch("/anamnesis/{anamnesis_id}", response_model=s.AnamnesisAssessmentRead)
async def submit_anamnesis_responses(
    anamnesis_id: str, body: s.AnamnesisResponsesSubmit, db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    items = [item.model_dump() for item in body.responses]
    return await AnamnesisService(db).submit_responses(anamnesis_id, items=items, complete=body.complete)
