from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
from app.core.scoping import assert_owns_profile, assert_patient_self
from app.modules.prs import schemas as s
from app.modules.prs.service import PatientScaleAssignmentService, PrsAssessmentService, PrsCatalogService

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")


@router.get("/prs-catalog/diseases")
async def list_diseases(db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await PrsCatalogService(db).diseases()


@router.get("/prs-catalog/scale-questions", response_model=list[s.PrsQuestionRead])
async def list_scale_questions(
    scale_id: str, language: str = "en", db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))
):
    # scale_id is a composite TEXT key containing "/" (e.g. "GAD-7/2026") —
    # never usable as a path segment (breaks REST routing, see this
    # codebase's own convention), so it's a query param here, not {scale_id}.
    return await PrsCatalogService(db).questions_for_scale(scale_id, language=language)


@router.post("/patient-scale-assignments", response_model=s.PatientScaleAssignmentRead, status_code=201)
async def assign_scale(body: s.PatientScaleAssignmentCreate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await PatientScaleAssignmentService(db).create(assigned_by=UUID(ctx.user_id), **body.model_dump())


@router.get("/patients/{patient_id}/scale-assignments", response_model=list[s.PatientScaleAssignmentRead])
async def list_scale_assignments(patient_id: UUID, assessment_stage: str | None = None, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    await assert_patient_self(ctx, db, patient_id)
    return await PatientScaleAssignmentService(db).list(patient_id, assessment_stage=assessment_stage)


@router.get("/patients/{patient_id}/prs-instances", response_model=list[s.AssessmentInstanceRead])
async def list_patient_prs_instances(
    patient_id: UUID, assessment_stage: str | None = None, db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    await assert_patient_self(ctx, db, patient_id)
    return await PrsAssessmentService(db).list_for_patient(patient_id, assessment_stage=assessment_stage)


@router.post("/prs-assessment-instances", response_model=s.AssessmentStartRead, status_code=201)
async def start_assessment(body: s.AssessmentInstanceCreate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    await assert_patient_self(ctx, db, body.patient_id)
    initiated_by = "doctor_on_behalf" if ctx.role != "patient" else "patient"
    return await PrsAssessmentService(db).start(
        patient_id=body.patient_id, disease_id=body.disease_id, assessment_stage=body.assessment_stage,
        session_id=body.session_id, cycle_id=body.cycle_id,
        administered_by=UUID(ctx.user_id) if ctx.role != "patient" else None, initiated_by=initiated_by,
        language_code=body.language_code,
    )


@router.patch("/prs-assessment-instances/{instance_id}/language", response_model=s.AssessmentStartRead)
async def set_assessment_language(
    instance_id: str, body: s.InstanceLanguageUpdate, db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    # Language dropdown after "Start Assessment" — updates the instance's
    # stored language_code and returns questions/options re-translated in
    # the same shape start() returns, so the UI just re-renders.
    instance = await PrsAssessmentService(db).get(instance_id)
    assert_owns_profile(ctx, instance["patient_id"])
    return await PrsAssessmentService(db).set_language(instance_id, body.language_code)


@router.get("/prs-assessment-instances/{instance_id}", response_model=s.AssessmentInstanceRead)
async def get_assessment(instance_id: str, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    instance = await PrsAssessmentService(db).get(instance_id)
    assert_owns_profile(ctx, instance["patient_id"])
    return instance


@router.get("/prs-assessment-instances/{instance_id}/responses", response_model=list[s.ResponseRead])
async def list_responses(
    instance_id: str, language: str | None = None, db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    # language=<code> — doctor/staff view: translates question_text/response_label
    # into the requested language regardless of what the patient answered in.
    # Omitted: returns raw rows in whatever language each response was recorded in.
    instance = await PrsAssessmentService(db).get(instance_id)
    assert_owns_profile(ctx, instance["patient_id"])
    return await PrsAssessmentService(db).responses_for_instance(instance_id, language=language)


@router.post("/prs-assessment-instances/{instance_id}/responses", response_model=s.AssessmentInstanceRead)
async def submit_responses(instance_id: str, body: s.ResponsesSubmit, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    instance = await PrsAssessmentService(db).get(instance_id)
    assert_owns_profile(ctx, instance["patient_id"])
    items = [item.model_dump() for item in body.responses]
    return await PrsAssessmentService(db).submit_responses(instance_id, items=items, finalize_scale_id=body.finalize_scale_id)


@router.get("/prs-assessment-instances/{instance_id}/results")
async def get_results(instance_id: str, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    instance = await PrsAssessmentService(db).get(instance_id)
    assert_owns_profile(ctx, instance["patient_id"])
    return await PrsAssessmentService(db).results(instance_id)
