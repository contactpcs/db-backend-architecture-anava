from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
from app.modules.clinical import schemas as s
from app.modules.clinical.service import ProtocolRequestService

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")


# ---------------------------------------------------- protocol requests --
@router.post("/assessment-protocol-requests", response_model=s.ProtocolRequestRead, status_code=201)
async def create_protocol_request(
    body: s.ProtocolRequestCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("clinical_assistant", "super_admin")),
):
    return await ProtocolRequestService(db).create(body.model_dump(), clinical_assistant_id=UUID(ctx.user_id))


@router.get("/assessment-protocol-requests", response_model=list[s.ProtocolRequestRead])
async def list_protocol_requests(
    patient_id: UUID | None = None,
    status: str | None = None,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    return await ProtocolRequestService(db).list(patient_id=patient_id, status=status)


@router.get("/assessment-protocol-requests/{request_id}", response_model=s.ProtocolRequestRead)
async def get_protocol_request(request_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await ProtocolRequestService(db).get(request_id)


@router.patch("/assessment-protocol-requests/{request_id}/decision", response_model=s.ProtocolRequestRead)
async def decide_protocol_request(
    request_id: UUID,
    body: s.ProtocolRequestDecision,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("doctor", "super_admin")),
):
    return await ProtocolRequestService(db).decide(request_id, decision=body.decision, doctor_notes=body.doctor_notes)
