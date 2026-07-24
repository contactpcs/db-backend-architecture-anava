from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.exceptions import PermissionError_
from app.core.permissions import require_role
from app.core.scoping import assert_clinic_scope, clinic_region_id
from app.modules.staff import schemas as s
from app.modules.staff.service import (
    CaDoctorAssignmentService,
    ClinicalAssistantService,
    DoctorService,
    ReceptionistService,
    StaffRequestService,
)

router = APIRouter()

_STAFF_MGMT_ROLES = ("super_admin", "regional_admin", "clinic_admin")
_STAFF_READ_ROLES = (*_STAFF_MGMT_ROLES, "doctor", "clinical_assistant", "receptionist")
# clinic_admin can only request/refer (POST /staff-requests) and update
# existing staff details — never create or delete outright. regional_admin
# reviews requests and, on approval, creates the profile themselves as a
# separate manual step; regional_admin/super_admin retain full CRUD.
_STAFF_CREATE_ROLES = ("super_admin", "regional_admin")
_STAFF_DELETE_ROLES = ("super_admin", "regional_admin")
# Every one of these roles belongs to exactly one clinic (ctx.clinic_id is
# always set) — a bare GET with no clinic_id should default to "my own
# clinic", never every doctor system-wide. super_admin/regional_admin see
# everything by default since they aren't clinic-scoped.
_SINGLE_CLINIC_ROLES = ("clinic_admin", "doctor", "clinical_assistant", "receptionist")


# --------------------------------------------------------------- doctors --
@router.post("/doctors", response_model=s.DoctorRead, status_code=201)
async def create_doctor(body: s.DoctorCreate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_STAFF_CREATE_ROLES))):
    data = body.model_dump()
    data["clinic_id"] = str(data["clinic_id"])
    await assert_clinic_scope(ctx, db, data["clinic_id"])
    return await DoctorService(db).create(data)


@router.get("/doctors", response_model=list[s.DoctorRead])
async def list_doctors(clinic_id: UUID | None = None, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_STAFF_READ_ROLES))):
    if clinic_id is None and ctx.role in _SINGLE_CLINIC_ROLES:
        clinic_id = UUID(ctx.clinic_id)
    return await DoctorService(db).list(clinic_id=clinic_id)


@router.get("/doctors/{doctor_id}", response_model=s.DoctorRead)
async def get_doctor(doctor_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_STAFF_READ_ROLES))):
    return await DoctorService(db).get(doctor_id)


@router.patch("/doctors/{doctor_id}", response_model=s.DoctorRead)
async def update_doctor(
    doctor_id: UUID,
    body: s.DoctorUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_STAFF_MGMT_ROLES, "doctor")),
):
    existing = await DoctorService(db).get(doctor_id)
    await assert_clinic_scope(ctx, db, existing["clinic_id"])
    return await DoctorService(db).update(doctor_id, body.model_dump(), updated_by=UUID(ctx.user_id))


@router.delete("/doctors/{doctor_id}", status_code=204)
async def delete_doctor(doctor_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_STAFF_DELETE_ROLES))):
    existing = await DoctorService(db).get(doctor_id)
    await assert_clinic_scope(ctx, db, existing["clinic_id"])
    await DoctorService(db).delete(doctor_id, deleted_by=UUID(ctx.user_id))


# ------------------------------------------------------- clinical assistants --
@router.post("/clinical-assistants", response_model=s.ClinicalAssistantRead, status_code=201)
async def create_ca(body: s.ClinicalAssistantCreate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_STAFF_CREATE_ROLES))):
    data = body.model_dump()
    data["clinic_id"] = str(data["clinic_id"])
    await assert_clinic_scope(ctx, db, data["clinic_id"])
    return await ClinicalAssistantService(db).create(data)


@router.get("/clinical-assistants", response_model=list[s.ClinicalAssistantRead])
async def list_cas(clinic_id: UUID | None = None, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_STAFF_READ_ROLES))):
    if clinic_id is None and ctx.role == "clinic_admin":
        clinic_id = UUID(ctx.clinic_id)
    return await ClinicalAssistantService(db).list(clinic_id=clinic_id)


@router.get("/clinical-assistants/{ca_id}", response_model=s.ClinicalAssistantRead)
async def get_ca(ca_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_STAFF_READ_ROLES))):
    return await ClinicalAssistantService(db).get(ca_id)


@router.patch("/clinical-assistants/{ca_id}", response_model=s.ClinicalAssistantRead)
async def update_ca(
    ca_id: UUID,
    body: s.ClinicalAssistantUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_STAFF_MGMT_ROLES, "clinical_assistant")),
):
    existing = await ClinicalAssistantService(db).get(ca_id)
    await assert_clinic_scope(ctx, db, existing["clinic_id"])
    return await ClinicalAssistantService(db).update(ca_id, body.model_dump(), updated_by=UUID(ctx.user_id))


@router.delete("/clinical-assistants/{ca_id}", status_code=204)
async def delete_ca(ca_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_STAFF_DELETE_ROLES))):
    existing = await ClinicalAssistantService(db).get(ca_id)
    await assert_clinic_scope(ctx, db, existing["clinic_id"])
    await ClinicalAssistantService(db).delete(ca_id, deleted_by=UUID(ctx.user_id))


# ------------------------------------------------------------- receptionists --
@router.post("/receptionists", response_model=s.ReceptionistRead, status_code=201)
async def create_receptionist(
    body: s.ReceptionistCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_STAFF_CREATE_ROLES)),
):
    data = body.model_dump()
    data["clinic_id"] = str(data["clinic_id"])
    await assert_clinic_scope(ctx, db, data["clinic_id"])
    return await ReceptionistService(db).create(data)


@router.get("/receptionists", response_model=list[s.ReceptionistRead])
async def list_receptionists(
    clinic_id: UUID | None = None,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_STAFF_READ_ROLES)),
):
    if clinic_id is None and ctx.role == "clinic_admin":
        clinic_id = UUID(ctx.clinic_id)
    return await ReceptionistService(db).list(clinic_id=clinic_id)


@router.get("/receptionists/{receptionist_id}", response_model=s.ReceptionistRead)
async def get_receptionist(receptionist_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_STAFF_READ_ROLES))):
    return await ReceptionistService(db).get(receptionist_id)


@router.patch("/receptionists/{receptionist_id}", response_model=s.ReceptionistRead)
async def update_receptionist(
    receptionist_id: UUID,
    body: s.ReceptionistUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_STAFF_MGMT_ROLES, "receptionist")),
):
    existing = await ReceptionistService(db).get(receptionist_id)
    await assert_clinic_scope(ctx, db, existing["clinic_id"])
    return await ReceptionistService(db).update(receptionist_id, body.model_dump(), updated_by=UUID(ctx.user_id))


@router.delete("/receptionists/{receptionist_id}", status_code=204)
async def delete_receptionist(receptionist_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_STAFF_DELETE_ROLES))):
    existing = await ReceptionistService(db).get(receptionist_id)
    await assert_clinic_scope(ctx, db, existing["clinic_id"])
    await ReceptionistService(db).delete(receptionist_id, deleted_by=UUID(ctx.user_id))


# -------------------------------------------------- CA <-> doctor assignments --
@router.post("/ca-doctor-assignments", response_model=s.CaDoctorAssignmentRead, status_code=201)
async def create_ca_doctor_assignment(
    body: s.CaDoctorAssignmentCreate,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_STAFF_MGMT_ROLES)),
):
    return await CaDoctorAssignmentService(db).create(**body.model_dump())


@router.get("/ca-doctor-assignments", response_model=list[s.CaDoctorAssignmentRead])
async def list_ca_doctor_assignments(
    ca_id: UUID | None = None,
    doctor_id: UUID | None = None,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_STAFF_READ_ROLES)),
):
    return await CaDoctorAssignmentService(db).list(ca_id=ca_id, doctor_id=doctor_id)


# ------------------------------------------------------------- staff requests --
# clinic_admin is confined to their own clinic (ctx.clinic_id), regional_admin
# to clinics within their own region (ctx.region_id) — super_admin crosses
# every boundary. See Master Doc Section 8 Flow G/H/I: Clinic Admin initiates,
# Regional Admin approves.
@router.post("/staff-requests", response_model=s.StaffRequestRead, status_code=201)
async def create_staff_request(
    body: s.StaffRequestCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_STAFF_MGMT_ROLES)),
):
    data = body.model_dump()
    data["clinic_id"] = str(data["clinic_id"])
    if data.get("target_staff_id"):
        data["target_staff_id"] = str(data["target_staff_id"])

    if ctx.role == "clinic_admin" and data["clinic_id"] != ctx.clinic_id:
        raise PermissionError_("You can only submit staff requests for your own clinic", code="CLINIC_SCOPE_MISMATCH")
    if ctx.role == "regional_admin":
        region_id = await clinic_region_id(db, data["clinic_id"])
        if region_id != ctx.region_id:
            raise PermissionError_("You can only submit staff requests for clinics in your own region", code="REGION_SCOPE_MISMATCH")

    return await StaffRequestService(db).create(data, submitted_by=UUID(ctx.user_id))


@router.get("/staff-requests", response_model=list[s.StaffRequestRead])
async def list_staff_requests(
    clinic_id: UUID | None = None,
    status: str | None = None,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_STAFF_MGMT_ROLES)),
):
    if ctx.role == "clinic_admin":
        if clinic_id and str(clinic_id) != ctx.clinic_id:
            raise PermissionError_("You can only view staff requests for your own clinic", code="CLINIC_SCOPE_MISMATCH")
        return await StaffRequestService(db).list(clinic_id=UUID(ctx.clinic_id), status=status)

    if ctx.role == "regional_admin":
        if clinic_id:
            region_id = await clinic_region_id(db, clinic_id)
            if region_id != ctx.region_id:
                raise PermissionError_("You can only view staff requests for clinics in your own region", code="REGION_SCOPE_MISMATCH")
            return await StaffRequestService(db).list(clinic_id=clinic_id, status=status)
        if not ctx.region_id:
            raise PermissionError_("Your account has no region assigned", code="NO_REGION_ASSIGNED")
        return await StaffRequestService(db).list_by_region(ctx.region_id, status=status)

    return await StaffRequestService(db).list(clinic_id=clinic_id, status=status)


@router.patch("/staff-requests/{request_id}/decision", response_model=s.StaffRequestRead)
async def decide_staff_request(
    request_id: UUID,
    body: s.StaffRequestDecision,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin")),
):
    if ctx.role == "regional_admin":
        req = await StaffRequestService(db).get(request_id)
        region_id = await clinic_region_id(db, req["clinic_id"])
        if region_id != ctx.region_id:
            raise PermissionError_("You can only decide on staff requests for clinics in your own region", code="REGION_SCOPE_MISMATCH")
    return await StaffRequestService(db).decide(
        request_id,
        decision=body.decision,
        reviewed_by=UUID(ctx.user_id),
        review_notes=body.review_notes,
    )
