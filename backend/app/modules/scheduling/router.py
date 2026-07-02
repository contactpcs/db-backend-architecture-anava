from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
from app.modules.scheduling import schemas as s
from app.modules.scheduling.service import (
    AppointmentRequestService,
    AppointmentService,
    AvailabilityService,
    ScheduleOverrideService,
    WeeklyScheduleService,
)

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")


@router.post("/doctors/{doctor_id}/weekly-schedules", response_model=s.WeeklyScheduleRead, status_code=201)
async def create_weekly_schedule(doctor_id: UUID, body: s.WeeklyScheduleCreate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    data = body.model_dump()
    data["clinic_id"] = str(data["clinic_id"])
    return await WeeklyScheduleService(db).create(doctor_id, data, created_by=UUID(ctx.user_id))


@router.get("/doctors/{doctor_id}/weekly-schedules", response_model=list[s.WeeklyScheduleRead])
async def list_weekly_schedules(doctor_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await WeeklyScheduleService(db).list_for_doctor(doctor_id)


@router.post("/doctors/{doctor_id}/schedule-overrides", response_model=s.ScheduleOverrideRead, status_code=201)
async def create_schedule_override(doctor_id: UUID, body: s.ScheduleOverrideCreate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    data = body.model_dump()
    data["clinic_id"] = str(data["clinic_id"])
    return await ScheduleOverrideService(db).create(doctor_id, data, created_by=UUID(ctx.user_id))


@router.get("/doctors/{doctor_id}/schedule-overrides", response_model=list[s.ScheduleOverrideRead])
async def list_schedule_overrides(doctor_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await ScheduleOverrideService(db).list_for_doctor(doctor_id)


@router.get("/doctors/{doctor_id}/availability")
async def get_availability(doctor_id: UUID, on_date: date, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await AvailabilityService(db).compute(doctor_id, on_date)


@router.post("/appointment-requests", response_model=s.AppointmentRequestRead, status_code=201)
async def create_appointment_request(body: s.AppointmentRequestCreate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await AppointmentRequestService(db).create(body.model_dump(), submitted_by=UUID(ctx.user_id))


@router.get("/appointment-requests", response_model=list[s.AppointmentRequestRead])
async def list_appointment_requests(clinic_id: UUID | None = None, status: str | None = None, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await AppointmentRequestService(db).list(clinic_id=clinic_id, status=status)


@router.get("/appointment-requests/{request_id}", response_model=s.AppointmentRequestRead)
async def get_appointment_request(request_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await AppointmentRequestService(db).get(request_id)


@router.patch("/appointment-requests/{request_id}/decision", response_model=s.AppointmentRequestRead)
async def decide_appointment_request(request_id: UUID, body: s.AppointmentRequestDecision, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await AppointmentRequestService(db).decide(request_id, body.model_dump(), reviewed_by=UUID(ctx.user_id))


@router.post("/appointments", response_model=s.AppointmentRead, status_code=201)
async def create_appointment(body: s.AppointmentCreate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await AppointmentService(db).create(body.model_dump(), booked_by=UUID(ctx.user_id), booked_by_role=ctx.role)


@router.get("/appointments", response_model=list[s.AppointmentRead])
async def list_appointments(clinic_id: UUID | None = None, doctor_id: UUID | None = None, patient_id: UUID | None = None, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await AppointmentService(db).list(clinic_id=clinic_id, doctor_id=doctor_id, patient_id=patient_id)


@router.get("/appointments/{appointment_id}", response_model=s.AppointmentRead)
async def get_appointment(appointment_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await AppointmentService(db).get(appointment_id)


@router.patch("/appointments/{appointment_id}/reschedule", response_model=s.AppointmentRead)
async def reschedule_appointment(appointment_id: UUID, body: s.AppointmentReschedule, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await AppointmentService(db).reschedule(appointment_id, body.model_dump(), changed_by=UUID(ctx.user_id), changed_by_role=ctx.role)


@router.patch("/appointments/{appointment_id}/status", response_model=s.AppointmentRead)
async def update_appointment_status(appointment_id: UUID, body: s.AppointmentStatusUpdate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await AppointmentService(db).update_status(appointment_id, status=body.status, changed_by=UUID(ctx.user_id), changed_by_role=ctx.role, cancellation_reason=body.cancellation_reason)


@router.get("/appointments/{appointment_id}/audit-log")
async def get_appointment_audit_log(appointment_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await AppointmentService(db).audit_log(appointment_id)
