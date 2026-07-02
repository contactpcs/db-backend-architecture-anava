from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
from app.modules.admin import schemas as s
from app.modules.admin.service import ClinicRequestService, ClinicService, RegionService, StaffAssignmentService

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


# ---------------------------------------------------------------- clinics --
@router.post("/clinics", response_model=s.ClinicRead, status_code=201)
async def create_clinic(
    body: s.ClinicCreate,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("super_admin", "regional_admin")),
):
    data = body.model_dump()
    data["region_id"] = str(data["region_id"])
    data["clinic_admin_id"] = str(data["clinic_admin_id"])
    return await ClinicService(db).create(data)


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
    _ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin")),
):
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
    return await ClinicRequestService(db).create(data, submitted_by=UUID(ctx.user_id))


@router.get("/clinic-requests", response_model=list[s.ClinicRequestRead])
async def list_clinic_requests(
    region_id: UUID | None = None,
    status: str | None = None,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("super_admin", "regional_admin")),
):
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
