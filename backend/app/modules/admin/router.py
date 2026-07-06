from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.exceptions import PermissionError_
from app.core.permissions import require_role
from app.core.scoping import assert_clinic_scope
from app.modules.admin import schemas as s
from app.modules.admin.service import (
    AdminAccountsService,
    ClinicRequestService,
    ClinicService,
    RegionService,
    StaffAssignmentService,
)

router = APIRouter()


# ---------------------------------------------------------------- regions --
@router.post("/regions", response_model=s.RegionRead, status_code=201)
async def create_region(
    body: s.RegionCreate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role("super_admin"))
):
    return await RegionService(db).create(**body.model_dump())


@router.get("/regions", response_model=list[s.RegionRead])
async def list_regions(db=Depends(get_db), _ctx: RequestContext = Depends(require_role(
    "super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist"
))):
    return await RegionService(db).list()


@router.get("/regions/{region_id}", response_model=s.RegionRead)
async def get_region(region_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(
    "super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist"
))):
    return await RegionService(db).get(region_id)


@router.patch("/regions/{region_id}", response_model=s.RegionRead)
async def update_region(
    region_id: UUID,
    body: s.RegionUpdate,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("super_admin")),
):
    return await RegionService(db).update(region_id, body.model_dump())


@router.delete("/regions/{region_id}", status_code=204)
async def delete_region(
    region_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role("super_admin"))
):
    await RegionService(db).delete(region_id)


@router.post("/regions/{region_id}/assign-admin", response_model=s.RegionRead, status_code=201)
async def assign_regional_admin(
    region_id: UUID,
    body: s.RegionalAdminAssign,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("super_admin")),
):
    return await RegionService(db).assign_admin(region_id, body.model_dump())


# ----------------------------------------------------------------- admins --
@router.get("/admins", response_model=list[s.AdminAccountRead])
async def list_admins(
    admin_type: str | None = None,
    region_id: UUID | None = None,
    clinic_id: UUID | None = None,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin")),
):
    # A regional_admin is always clamped to their own region — never take
    # their word for region_id, or they could pass another region's id (or
    # omit it) and see every clinic_admin in the system.
    if ctx.role == "regional_admin":
        region_id = UUID(ctx.region_id)
    return await AdminAccountsService(db).list(admin_type=admin_type, region_id=region_id, clinic_id=clinic_id)


@router.patch("/admins/{admin_id}", response_model=s.AdminAccountRead)
async def update_admin(
    admin_id: UUID,
    body: s.AdminAccountUpdate,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("super_admin")),
):
    return await AdminAccountsService(db).update(admin_id, body.model_dump())


# ---------------------------------------------------------------- clinics --
@router.post("/clinics", response_model=s.ClinicRead, status_code=201)
async def create_clinic(
    body: s.ClinicCreate,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("super_admin", "regional_admin")),
):
    data = body.model_dump()
    data["region_id"] = str(data["region_id"])
    return await ClinicService(db).create(data)


@router.post("/clinics/{clinic_id}/assign-admin", response_model=s.ClinicRead, status_code=201)
async def assign_clinic_admin(
    clinic_id: UUID,
    body: s.ClinicAdminAssign,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("super_admin", "regional_admin")),
):
    return await ClinicService(db).assign_admin(clinic_id, body.model_dump())


@router.get("/clinics", response_model=list[s.ClinicRead])
async def list_clinics(
    region_id: UUID | None = None,
    status: str | None = None,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(
        "super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist"
    )),
):
    return await ClinicService(db).list(region_id=region_id, status=status)


@router.get("/clinics/{clinic_id}", response_model=s.ClinicRead)
async def get_clinic(clinic_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(
    "super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist"
))):
    return await ClinicService(db).get(clinic_id)


@router.patch("/clinics/{clinic_id}", response_model=s.ClinicRead)
async def update_clinic(
    clinic_id: UUID,
    body: s.ClinicUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin")),
):
    await assert_clinic_scope(ctx, db, clinic_id)
    fields = body.model_dump()
    if fields.get("clinic_admin_id"):
        fields["clinic_admin_id"] = str(fields["clinic_admin_id"])
    return await ClinicService(db).update(clinic_id, fields)


@router.patch("/clinics/{clinic_id}/status", response_model=s.ClinicRead)
async def change_clinic_status(
    clinic_id: UUID,
    body: s.ClinicStatusUpdate,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("super_admin", "regional_admin")),
):
    return await ClinicService(db).change_status(clinic_id, body.status)


@router.delete("/clinics/{clinic_id}", status_code=204)
async def delete_clinic(
    clinic_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role("super_admin", "regional_admin"))
):
    await ClinicService(db).delete(clinic_id)


# --------------------------------------------------------- clinic requests --
@router.post("/clinic-requests", response_model=s.ClinicRequestRead, status_code=201)
async def create_clinic_request(
    body: s.ClinicRequestCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin")),
):
    data = body.model_dump()
    data["region_id"] = str(data["region_id"])
    if data.get("clinic_id"):
        data["clinic_id"] = str(data["clinic_id"])
    if ctx.role == "regional_admin" and data["region_id"] != ctx.region_id:
        raise PermissionError_("You can only submit clinic requests for your own region", code="REGION_SCOPE_MISMATCH")
    return await ClinicRequestService(db).create(data, submitted_by=UUID(ctx.user_id))


@router.get("/clinic-requests", response_model=list[s.ClinicRequestRead])
async def list_clinic_requests(
    region_id: UUID | None = None,
    status: str | None = None,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "regional_admin")),
):
    if region_id is None and ctx.role == "regional_admin":
        region_id = UUID(ctx.region_id)
    return await ClinicRequestService(db).list(region_id=region_id, status=status)


@router.get("/clinic-requests/{request_id}", response_model=s.ClinicRequestRead)
async def get_clinic_request(
    request_id: UUID,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("super_admin", "regional_admin")),
):
    return await ClinicRequestService(db).get(request_id)


@router.patch("/clinic-requests/{request_id}/decision", response_model=s.ClinicRequestRead)
async def decide_clinic_request(
    request_id: UUID,
    body: s.ClinicRequestDecision,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin")),
):
    return await ClinicRequestService(db).decide(
        request_id, decision=body.decision, reviewed_by=UUID(ctx.user_id), review_notes=body.review_notes
    )


# ----------------------------------------------------- staff assignments --
@router.get("/clinics/{clinic_id}/staff-assignments", response_model=list[s.StaffAssignmentRead])
async def list_staff_assignments(
    clinic_id: UUID,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin")),
):
    return await StaffAssignmentService(db).list_for_clinic(clinic_id)


@router.post("/clinics/{clinic_id}/staff-assignments", response_model=s.StaffAssignmentRead, status_code=201)
async def add_staff_assignment(
    clinic_id: UUID,
    body: s.StaffAssignmentCreate,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin")),
):
    return await StaffAssignmentService(db).add(clinic_id, profile_id=body.profile_id, staff_role=body.staff_role)


@router.patch("/staff-assignments/{assignment_id}", response_model=s.StaffAssignmentRead)
async def remove_staff_assignment(
    assignment_id: UUID,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin")),
):
    return await StaffAssignmentService(db).remove(assignment_id)
