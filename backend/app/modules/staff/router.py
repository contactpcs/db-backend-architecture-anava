from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
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


# --------------------------------------------------------------- doctors --
@router.post("/doctors", response_model=s.DoctorRead, status_code=201)
async def create_doctor(body: s.DoctorCreate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_STAFF_MGMT_ROLES))):
    data = body.model_dump()
    data["clinic_id"] = str(data["clinic_id"])
    return await DoctorService(db).create(data)


@router.get("/doctors", response_model=list[s.DoctorRead])
async def list_doctors(clinic_id: UUID | None = None, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_STAFF_READ_ROLES))):
    return await DoctorService(db).list(clinic_id=clinic_id)


@router.get("/doctors/{doctor_id}", response_model=s.DoctorRead)
async def get_doctor(doctor_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_STAFF_READ_ROLES))):
    return await DoctorService(db).get(doctor_id)


@router.patch("/doctors/{doctor_id}", response_model=s.DoctorRead)
async def update_doctor(doctor_id: UUID, body: s.DoctorUpdate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_STAFF_MGMT_ROLES, "doctor"))):
    return await DoctorService(db).update(doctor_id, body.model_dump())


# ------------------------------------------------------- clinical assistants --
@router.post("/clinical-assistants", response_model=s.ClinicalAssistantRead, status_code=201)
async def create_ca(body: s.ClinicalAssistantCreate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_STAFF_MGMT_ROLES))):
    data = body.model_dump()
    data["clinic_id"] = str(data["clinic_id"])
    return await ClinicalAssistantService(db).create(data)


@router.get("/clinical-assistants", response_model=list[s.ClinicalAssistantRead])
async def list_cas(clinic_id: UUID | None = None, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_STAFF_READ_ROLES))):
    return await ClinicalAssistantService(db).list(clinic_id=clinic_id)


# ------------------------------------------------------------- receptionists --
@router.post("/receptionists", response_model=s.ReceptionistRead, status_code=201)
async def create_receptionist(body: s.ReceptionistCreate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_STAFF_MGMT_ROLES))):
    data = body.model_dump()
    data["clinic_id"] = str(data["clinic_id"])
    return await ReceptionistService(db).create(data)


@router.get("/receptionists", response_model=list[s.ReceptionistRead])
async def list_receptionists(clinic_id: UUID | None = None, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_STAFF_READ_ROLES))):
    return await ReceptionistService(db).list(clinic_id=clinic_id)


# -------------------------------------------------- CA <-> doctor assignments --
@router.post("/ca-doctor-assignments", response_model=s.CaDoctorAssignmentRead, status_code=201)
async def create_ca_doctor_assignment(body: s.CaDoctorAssignmentCreate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_STAFF_MGMT_ROLES))):
    return await CaDoctorAssignmentService(db).create(**body.model_dump())


@router.get("/ca-doctor-assignments", response_model=list[s.CaDoctorAssignmentRead])
async def list_ca_doctor_assignments(ca_id: UUID | None = None, doctor_id: UUID | None = None, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_STAFF_READ_ROLES))):
    return await CaDoctorAssignmentService(db).list(ca_id=ca_id, doctor_id=doctor_id)


# ------------------------------------------------------------- staff requests --
@router.post("/staff-requests", response_model=s.StaffRequestRead, status_code=201)
async def create_staff_request(body: s.StaffRequestCreate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_STAFF_MGMT_ROLES))):
    data = body.model_dump()
    data["clinic_id"] = str(data["clinic_id"])
    if data.get("target_staff_id"):
        data["target_staff_id"] = str(data["target_staff_id"])
    return await StaffRequestService(db).create(data, submitted_by=UUID(ctx.user_id))


@router.get("/staff-requests", response_model=list[s.StaffRequestRead])
async def list_staff_requests(clinic_id: UUID | None = None, status: str | None = None, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_STAFF_MGMT_ROLES))):
    return await StaffRequestService(db).list(clinic_id=clinic_id, status=status)


@router.patch("/staff-requests/{request_id}/decision", response_model=s.StaffRequestRead)
async def decide_staff_request(request_id: UUID, body: s.StaffRequestDecision, db=Depends(get_db), ctx: RequestContext = Depends(require_role("super_admin", "regional_admin"))):
    return await StaffRequestService(db).decide(request_id, decision=body.decision, reviewed_by=UUID(ctx.user_id), review_notes=body.review_notes)
