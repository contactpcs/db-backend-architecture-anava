from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
from app.modules.clinical import schemas as s
from app.modules.clinical.service import (
    DoctorSessionNoteService,
    ProtocolRequestService,
    SessionService,
    TreatmentCycleService,
    TreatmentPlanService,
    TreatmentSessionService,
)

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")


# --------------------------------------------------------------- cycles --
@router.post("/treatment-cycles", response_model=s.TreatmentCycleRead, status_code=201)
async def create_cycle(body: s.TreatmentCycleCreate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await TreatmentCycleService(db).create(body.model_dump())


@router.get("/treatment-cycles", response_model=list[s.TreatmentCycleRead])
async def list_cycles(patient_id: UUID | None = None, clinic_id: UUID | None = None, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await TreatmentCycleService(db).list(patient_id=patient_id, clinic_id=clinic_id)


@router.get("/treatment-cycles/{cycle_id}", response_model=s.TreatmentCycleRead)
async def get_cycle(cycle_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await TreatmentCycleService(db).get(cycle_id)


@router.patch("/treatment-cycles/{cycle_id}/status", response_model=s.TreatmentCycleRead)
async def update_cycle_status(cycle_id: UUID, body: s.TreatmentCycleStatusUpdate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await TreatmentCycleService(db).set_status(cycle_id, body.status)


# ---------------------------------------------------- protocol requests --
@router.post("/assessment-protocol-requests", response_model=s.ProtocolRequestRead, status_code=201)
async def create_protocol_request(body: s.ProtocolRequestCreate, db=Depends(get_db), ctx: RequestContext = Depends(require_role("clinical_assistant", "super_admin"))):
    return await ProtocolRequestService(db).create(body.model_dump(), clinical_assistant_id=UUID(ctx.user_id))


@router.get("/assessment-protocol-requests", response_model=list[s.ProtocolRequestRead])
async def list_protocol_requests(patient_id: UUID | None = None, status: str | None = None, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await ProtocolRequestService(db).list(patient_id=patient_id, status=status)


@router.get("/assessment-protocol-requests/{request_id}", response_model=s.ProtocolRequestRead)
async def get_protocol_request(request_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await ProtocolRequestService(db).get(request_id)


@router.patch("/assessment-protocol-requests/{request_id}/decision", response_model=s.ProtocolRequestRead)
async def decide_protocol_request(request_id: UUID, body: s.ProtocolRequestDecision, db=Depends(get_db), _ctx: RequestContext = Depends(require_role("doctor", "super_admin"))):
    return await ProtocolRequestService(db).decide(request_id, decision=body.decision, doctor_notes=body.doctor_notes)


# --------------------------------------------------------------- sessions --
@router.post("/sessions", response_model=s.SessionRead, status_code=201)
async def create_session(body: s.SessionCreate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await SessionService(db).create(body.model_dump())


@router.get("/sessions", response_model=list[s.SessionRead])
async def list_sessions(patient_id: UUID | None = None, cycle_id: UUID | None = None, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await SessionService(db).list(patient_id=patient_id, cycle_id=cycle_id)


@router.get("/sessions/{session_id}", response_model=s.SessionRead)
async def get_session(session_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await SessionService(db).get(session_id)


@router.patch("/sessions/{session_id}/status", response_model=s.SessionRead)
async def update_session_status(session_id: UUID, body: s.SessionStatusUpdate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await SessionService(db).update_status(session_id, status=body.status, outcome=body.outcome)


# ---------------------------------------------------------- treatment plans --
@router.post("/treatment-plans", response_model=s.TreatmentPlanRead, status_code=201)
async def create_treatment_plan(body: s.TreatmentPlanCreate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role("doctor", "super_admin"))):
    return await TreatmentPlanService(db).create(body.model_dump())


@router.get("/treatment-plans", response_model=list[s.TreatmentPlanRead])
async def list_treatment_plans(patient_id: UUID | None = None, cycle_id: UUID | None = None, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await TreatmentPlanService(db).list(patient_id=patient_id, cycle_id=cycle_id)


@router.get("/treatment-plans/{plan_id}", response_model=s.TreatmentPlanRead)
async def get_treatment_plan(plan_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await TreatmentPlanService(db).get(plan_id)


@router.patch("/treatment-plans/{plan_id}", response_model=s.TreatmentPlanRead)
async def update_treatment_plan(plan_id: UUID, body: s.TreatmentPlanUpdate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role("doctor", "super_admin"))):
    return await TreatmentPlanService(db).update(plan_id, body.model_dump())


# ------------------------------------------------------- treatment sessions --
@router.post("/treatment-sessions", response_model=s.TreatmentSessionRead, status_code=201)
async def create_treatment_session(body: s.TreatmentSessionCreate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role("clinical_assistant", "super_admin"))):
    return await TreatmentSessionService(db).create(body.model_dump())


@router.get("/treatment-sessions", response_model=list[s.TreatmentSessionRead])
async def list_treatment_sessions(plan_id: UUID | None = None, patient_id: UUID | None = None, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await TreatmentSessionService(db).list(plan_id=plan_id, patient_id=patient_id)


@router.patch("/treatment-sessions/{ts_id}/status", response_model=s.TreatmentSessionRead)
async def update_treatment_session_status(ts_id: UUID, body: s.TreatmentSessionStatusUpdate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role("clinical_assistant", "super_admin"))):
    return await TreatmentSessionService(db).update_status(ts_id, status=body.status, session_notes=body.session_notes, patient_feedback=body.patient_feedback)


# ---------------------------------------------------------- doctor notes --
@router.post("/doctor-session-notes", status_code=201)
async def create_doctor_session_note(body: s.DoctorSessionNoteCreate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role("doctor", "super_admin"))):
    return await DoctorSessionNoteService(db).create(body.model_dump())


@router.get("/doctor-session-notes/{note_id}")
async def get_doctor_session_note(note_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role("doctor", "super_admin"))):
    return await DoctorSessionNoteService(db).get(note_id)
