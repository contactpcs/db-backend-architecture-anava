from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
from app.core.scoping import assert_clinic_scope
from app.modules.patients import schemas as s
from app.modules.patients.service import (
    FollowUpService,
    PatientExitService,
    PatientService,
    PatientTransferService,
)

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")


@router.post("/patients", response_model=s.PatientRead, status_code=201)
async def register_patient(
    body: s.PatientRegister, db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin", "receptionist")),
):
    data = body.model_dump()
    data["primary_clinic_id"] = str(data["primary_clinic_id"])
    await assert_clinic_scope(ctx, db, data["primary_clinic_id"])
    return await PatientService(db).register(data)


@router.get("/patients", response_model=list[s.PatientRead])
async def list_patients(
    registration_status: str | None = None, approval_status: str | None = None, clinic_id: UUID | None = None,
    db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    if clinic_id is None and ctx.role in ("clinic_admin", "receptionist"):
        clinic_id = UUID(ctx.clinic_id)
    return await PatientService(db).list(registration_status=registration_status, approval_status=approval_status, clinic_id=clinic_id)


@router.get("/patients/{patient_id}", response_model=s.PatientRead)
async def get_patient(patient_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await PatientService(db).get(patient_id)


@router.patch("/patients/{patient_id}", response_model=s.PatientRead)
async def update_patient(
    patient_id: UUID, body: s.PatientUpdate, db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin")),
):
    existing = await PatientService(db).get(patient_id)
    await assert_clinic_scope(ctx, db, existing["primary_clinic_id"])
    return await PatientService(db).update(patient_id, body.model_dump())


@router.delete("/patients/{patient_id}", status_code=204)
async def delete_patient(
    patient_id: UUID, db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin")),
):
    existing = await PatientService(db).get(patient_id)
    await assert_clinic_scope(ctx, db, existing["primary_clinic_id"])
    await PatientService(db).delete(patient_id, deleted_by=UUID(ctx.user_id))


@router.patch("/patients/{patient_id}/approval", response_model=s.PatientRead)
async def decide_patient_approval(
    patient_id: UUID, body: s.PatientApprovalDecision, db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin", "receptionist")),
):
    existing = await PatientService(db).get(patient_id)
    await assert_clinic_scope(ctx, db, existing["primary_clinic_id"])
    return await PatientService(db).decide_approval(
        patient_id, decision=body.decision, decided_by=UUID(ctx.user_id), rejection_reason=body.rejection_reason
    )


@router.post("/patients/{patient_id}/disease-selection", response_model=s.DiseaseSelectionRead, status_code=201)
async def select_disease(
    patient_id: UUID, body: s.DiseaseSelectionCreate, db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    return await PatientService(db).select_disease(
        patient_id, disease_id=body.disease_id, disease_unknown=body.disease_unknown, is_primary=body.is_primary,
        assigned_by=UUID(ctx.user_id),
    )


@router.post("/patients/{patient_id}/followup-cycles", status_code=201)
async def start_followup_cycle(patient_id: UUID, body: s.FollowUpCycleCreate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await FollowUpService(db).start(patient_id, doctor_id=body.doctor_id)


@router.post("/patients/{patient_id}/transfers", response_model=s.TransferRead, status_code=201)
async def initiate_transfer(patient_id: UUID, body: s.TransferInitiate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await PatientTransferService(db).initiate(patient_id, body.model_dump(), initiated_by=UUID(ctx.user_id))


@router.get("/transfers/{pct_id}", response_model=s.TransferRead)
async def get_transfer(pct_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await PatientTransferService(db).get(pct_id)


@router.patch("/transfers/{pct_id}/complete", response_model=s.TransferRead)
async def complete_transfer(pct_id: UUID, body: s.TransferComplete, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await PatientTransferService(db).complete(pct_id, consent_id=body.consent_id)


@router.post("/patients/{patient_id}/exit")
async def exit_patient(patient_id: UUID, body: s.ExitInitiate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin", "doctor"))):
    return await PatientExitService(db).exit(patient_id, consent_id=body.consent_id)
