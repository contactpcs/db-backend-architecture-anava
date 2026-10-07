from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.config import get_settings
from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
from app.core.scoping import assert_clinic_scope, assert_patient_self
from app.modules.patients import schemas as s
from app.modules.patients.service import (
    FollowUpService,
    PatientClinicalNoteService,
    PatientExitService,
    PatientService,
    PatientTransferService,
    PatientVisitService,
    PrescribedMedicineService,
)

router = APIRouter()
settings = get_settings()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")


@router.post("/patients", response_model=s.PatientRead, status_code=201)
async def register_patient(
    body: s.PatientRegister,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin", "receptionist")),
):
    data = body.model_dump()
    data["primary_clinic_id"] = str(data["primary_clinic_id"])
    await assert_clinic_scope(ctx, db, data["primary_clinic_id"])
    # Staff-registered patients never go through the self-service OTP
    # signup wizard (patients/router.py's own routes below) — no channel to
    # collect an OTP from, since the staff member is filling this in, not
    # the patient. Same temp-password-emailed provisioning staff accounts
    # get, just for a patient identity instead.
    cognito_sub = None
    if settings.auth_mode == "cognito":
        from app.core.cognito import provision_staff_user

        cognito_sub = provision_staff_user(
            email=data["email"], first_name=data["first_name"], last_name=data["last_name"], phone=data.get("phone")
        )
    return await PatientService(db).register(data, cognito_sub=cognito_sub, registered_by=UUID(ctx.user_id))


@router.get("/patients/count")
async def count_patients(
    registration_status: str | None = None,
    approval_status: str | None = None,
    clinic_id: UUID | None = None,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
) -> dict:
    """Same filters and scoping as GET /patients, but just the number — the
    clinical-assistant dashboard and approvals badge used to download every
    patient to count them (API audit F-045). Registered before
    /patients/{patient_id} so "count" is never parsed as an id."""
    if clinic_id is None and ctx.role in ("clinic_admin", "receptionist"):
        clinic_id = UUID(ctx.clinic_id)
    count = await PatientService(db).count(registration_status=registration_status, approval_status=approval_status, clinic_id=clinic_id)
    return {"count": count}


# approval_view -> _list_where filters. "approved" = the default patient list
# (approved + staff-registered); "pending" = the approvals queue definition
# (approval pending AND wizard complete, as staffService.getPendingCount).
_APPROVAL_VIEWS: dict[str, dict] = {
    "all": {"include_unapproved": True, "hide_unfinished_pending": True},  # = approved + pending + rejected
    "approved": {},
    "pending": {"approval_status": "pending", "registration_status": "registration_complete"},
    "rejected": {"approval_status": "rejected"},
}


@router.get("/patients/page", response_model=s.PatientPageRead)
async def page_patients(
    search: str | None = None,
    clinic_id: UUID | None = None,
    approval_view: Literal["all", "approved", "pending", "rejected"] | None = None,
    with_counts: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
) -> dict:
    """GET /patients one page at a time, with server search (name, phone,
    email, MRN, doctor) — the admin patients list used to download every
    clinic patient and filter in the browser (API audit F-053). Same scoping
    as GET /patients; registered before /patients/{patient_id}.
    approval_view picks a super-admin tab; with_counts adds the four tab
    counts over the caller's whole scope (API audit F-062 / BUG-APPR)."""
    if clinic_id is None and ctx.role in ("clinic_admin", "receptionist"):
        clinic_id = UUID(ctx.clinic_id)
    svc = PatientService(db)
    view = _APPROVAL_VIEWS[approval_view or "approved"]
    items, total = await svc.list_page(limit=page_size, offset=(page - 1) * page_size, search=search or None, clinic_id=clinic_id, **view)
    result = {"items": items, "total": total, "page": page, "page_size": page_size, "total_pages": -(-total // page_size)}
    if with_counts:
        result["counts"] = {k: await svc.count(**v) for k, v in _APPROVAL_VIEWS.items()}
    return result


@router.get("/patients", response_model=list[s.PatientRead])
async def list_patients(
    registration_status: str | None = None,
    approval_status: str | None = None,
    clinic_id: UUID | None = None,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    # "patient" is allowed here so patients.service.ts's getDashboard()/
    # getMyAnamnesis() (which call this same "RLS-scoped to own record"
    # endpoint) don't 403. RLS scopes a patient to their own row too (the
    # app login does not bypass it), but the app layer forces it here as
    # well — defence in depth, ignoring any clinic_id they might pass.
    profile_id = UUID(ctx.user_id) if ctx.role == "patient" else None
    if ctx.role == "patient":
        clinic_id = None
    elif clinic_id is None and ctx.role in ("clinic_admin", "receptionist"):
        clinic_id = UUID(ctx.clinic_id)
    return await PatientService(db).list(
        registration_status=registration_status, approval_status=approval_status, clinic_id=clinic_id, profile_id=profile_id
    )


@router.get("/patients/{patient_id}", response_model=s.PatientRead)
async def get_patient(patient_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    await assert_patient_self(ctx, db, patient_id)
    return await PatientService(db).get(patient_id)


@router.get("/patients/{patient_id}/clinic", response_model=s.PatientClinicRead)
async def get_patient_clinic(patient_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    """Contact details of the patient's primary clinic, for the patient
    portal. Same access rule as GET /patients/{patient_id}: a patient can
    only ask about themselves."""
    await assert_patient_self(ctx, db, patient_id)
    return await PatientService(db).get_clinic(patient_id)


@router.get("/patients/{patient_id}/registration-record")
async def get_registration_record(
    patient_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))
):
    """The registration intake in one call (API audit F-025): registration-
    stage anamnesis + its responses, and the latest completed non-voided
    general_registration PRS instance with its results. Was 4 calls in two
    sequential chains (anamnesis -> responses, prs-instances -> results).
    Same services, same access rule (assert_patient_self), and the anamnesis
    parts are serialized through the same schemas as their own endpoints;
    missing pieces are null, exactly as the frontend treated a 404."""
    from app.core.exceptions import NotFoundError
    from app.modules.anamnesis import schemas as an
    from app.modules.anamnesis.service import AnamnesisService
    from app.modules.prs.service import PrsAssessmentService

    await assert_patient_self(ctx, db, patient_id)
    anamnesis, responses = None, []
    try:
        assessment = await AnamnesisService(db).get_current(patient_id, "registration")
        anamnesis = an.AnamnesisAssessmentRead.model_validate(assessment)
        rows = await AnamnesisService(db).get_responses(assessment["anamnesis_id"])
        responses = [an.AnamnesisResponseRead.model_validate(r) for r in rows]
    except NotFoundError:
        pass

    general_prs = None
    prs = PrsAssessmentService(db)
    live = [i for i in await prs.list_for_patient(patient_id, assessment_stage="general_registration") if not i.get("is_voided")]
    latest = next((i for i in live if i["status"] == "completed"), live[0] if live else None)
    if latest:
        general_prs = await prs.results(latest["instance_id"], latest)
    return {"anamnesis": anamnesis, "anamnesis_responses": responses, "general_prs": general_prs}


@router.get("/patients/{patient_id}/visits/{appointment_id}/summary", response_model=s.VisitSummaryRead)
async def get_visit_summary(
    patient_id: UUID,
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    await assert_patient_self(ctx, db, patient_id)
    return await PatientVisitService(db).get_visit_summary(patient_id, appointment_id)


@router.patch("/patients/{patient_id}", response_model=s.PatientRead)
async def update_patient(
    patient_id: UUID,
    body: s.PatientUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin")),
):
    existing = await PatientService(db).get(patient_id)
    await assert_clinic_scope(ctx, db, existing["primary_clinic_id"])
    # exclude_unset, not a plain model_dump(): a field the client genuinely
    # never mentioned must stay untouched, but one they explicitly sent as
    # null (e.g. clearing weight_kg) has to reach the service AS null so it
    # can actually be cleared — a plain model_dump() makes those two cases
    # indistinguishable (both come out None), which is why clearing a field
    # silently did nothing before (see PatientService.update()).
    return await PatientService(db).update(patient_id, body.model_dump(exclude_unset=True))


@router.patch("/patients/{patient_id}/self", response_model=s.PatientRead)
async def update_patient_self(
    patient_id: UUID,
    body: s.PatientSelfUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("patient")),
):
    # Patient self-service edit. mrn, approval_status, is_active,
    # primary_doctor_id etc. are deliberately absent from PatientSelfUpdate
    # — those stay staff-only via PATCH /patients/{id} above.
    await assert_patient_self(ctx, db, patient_id)
    # exclude_unset, not a plain model_dump(): a field the client genuinely
    # never mentioned must stay untouched, but one they explicitly sent as
    # null (e.g. clearing weight_kg) has to reach the service AS null so it
    # can actually be cleared — a plain model_dump() makes those two cases
    # indistinguishable (both come out None), which is why clearing a field
    # silently did nothing before (see PatientService.update()).
    return await PatientService(db).update(patient_id, body.model_dump(exclude_unset=True))


@router.delete("/patients/{patient_id}", status_code=204)
async def delete_patient(
    patient_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin")),
):
    existing = await PatientService(db).get(patient_id)
    await assert_clinic_scope(ctx, db, existing["primary_clinic_id"])
    await PatientService(db).delete(patient_id, deleted_by=UUID(ctx.user_id))


@router.patch("/patients/{patient_id}/approval", response_model=s.PatientRead)
async def decide_patient_approval(
    patient_id: UUID,
    body: s.PatientApprovalDecision,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin", "receptionist", "clinical_assistant")),
):
    existing = await PatientService(db).get(patient_id)
    await assert_clinic_scope(ctx, db, existing["primary_clinic_id"])
    return await PatientService(db).decide_approval(
        patient_id, decision=body.decision, decided_by=UUID(ctx.user_id), rejection_reason=body.rejection_reason
    )


@router.patch("/patients/{patient_id}/allocate-doctor", response_model=s.PatientRead)
async def allocate_doctor(
    patient_id: UUID,
    body: s.DoctorAllocation,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin", "receptionist")),
):
    existing = await PatientService(db).get(patient_id)
    await assert_clinic_scope(ctx, db, existing["primary_clinic_id"])
    return await PatientService(db).allocate_doctor(patient_id, body.doctor_id, allocated_by=UUID(ctx.user_id))


@router.post("/patients/{patient_id}/followup-cycles", status_code=201)
async def start_followup_cycle(
    patient_id: UUID,
    body: s.FollowUpCycleCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    return await FollowUpService(db).start(patient_id, doctor_id=body.doctor_id, ctx=ctx)


@router.post("/patients/{patient_id}/transfers", response_model=s.TransferRead, status_code=201)
async def initiate_transfer(
    patient_id: UUID, body: s.TransferInitiate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))
):
    return await PatientTransferService(db).initiate(patient_id, body.model_dump(), initiated_by=UUID(ctx.user_id))


@router.get("/transfers/{pct_id}", response_model=s.TransferRead)
async def get_transfer(pct_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await PatientTransferService(db).get(pct_id)


@router.patch("/transfers/{pct_id}/complete", response_model=s.TransferRead)
async def complete_transfer(
    pct_id: UUID, body: s.TransferComplete, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF))
):
    return await PatientTransferService(db).complete(pct_id, consent_id=body.consent_id)


@router.post("/patients/{patient_id}/exit")
async def exit_patient(
    patient_id: UUID,
    body: s.ExitInitiate,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin", "doctor")),
):
    return await PatientExitService(db).exit(patient_id, consent_id=body.consent_id)


# ─── Prescribed medicines (SQL/v1/96) ────────────────────────────────────────


@router.get("/patients/{patient_id}/prescribed-medicines", response_model=list[s.PrescribedMedicineRead])
async def list_prescribed_medicines(
    patient_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))
):
    await assert_patient_self(ctx, db, patient_id)
    if ctx.role != "patient":
        patient = await PatientService(db).get(patient_id)
        await assert_clinic_scope(ctx, db, patient["primary_clinic_id"])
    return await PrescribedMedicineService(db).list(patient_id)


@router.post("/patients/{patient_id}/prescribed-medicines", response_model=s.PrescribedMedicineRead, status_code=201)
async def prescribe_medicine(
    patient_id: UUID,
    body: s.PrescribedMedicineCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("doctor", "super_admin")),
):
    patient = await PatientService(db).get(patient_id)
    await assert_clinic_scope(ctx, db, patient["primary_clinic_id"])
    return await PrescribedMedicineService(db).create(patient_id, body.model_dump(), prescribed_by=UUID(ctx.user_id))


@router.patch("/prescribed-medicines/{medicine_id}", response_model=s.PrescribedMedicineRead)
async def update_prescribed_medicine_status(
    medicine_id: UUID,
    body: s.PrescribedMedicineStatusUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("doctor", "super_admin")),
):
    """Stop or resume a medicine. Never deletes — history stays."""
    service = PrescribedMedicineService(db)
    row = await service.get(medicine_id)
    await assert_clinic_scope(ctx, db, row["clinic_id"])
    return await service.set_status(medicine_id, status=body.status, changed_by=UUID(ctx.user_id))


# ─── Clinical notes (SQL/v1/99) ──────────────────────────────────────────────
# Doctor-authored only — never patient-visible, unlike prescribed medicines.


@router.get("/patients/{patient_id}/clinical-notes", response_model=list[s.PatientClinicalNoteRead])
async def list_clinical_notes(patient_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    patient = await PatientService(db).get(patient_id)
    await assert_clinic_scope(ctx, db, patient["primary_clinic_id"])
    return await PatientClinicalNoteService(db).list(patient_id)


@router.post("/patients/{patient_id}/clinical-notes", response_model=s.PatientClinicalNoteRead, status_code=201)
async def add_clinical_note(
    patient_id: UUID,
    body: s.PatientClinicalNoteCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("doctor", "super_admin")),
):
    patient = await PatientService(db).get(patient_id)
    await assert_clinic_scope(ctx, db, patient["primary_clinic_id"])
    return await PatientClinicalNoteService(db).create(patient_id, body.model_dump(), doctor_id=UUID(ctx.user_id))
