from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.exceptions import NotFoundError, PermissionError_
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


async def _own_doctor_row(db, ctx: RequestContext) -> dict:
    from sqlalchemy import text

    row = (await db.execute(text("SELECT doctor_id, clinic_id FROM doctors WHERE profile_id = :pid"), {"pid": ctx.user_id})).mappings().first()
    if not row or not row["clinic_id"]:
        raise NotFoundError("No doctor record (or clinic assignment) found for your account", code="DOCTOR_NOT_FOUND")
    return dict(row)


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


@router.get("/doctors/{doctor_id}/availability", response_model=list[s.AvailabilitySlotRead])
async def get_availability(
    doctor_id: UUID, from_date: date, to_date: date | None = None, include_unavailable: bool = True,
    db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    return await AvailabilityService(db).compute_range(doctor_id, from_date, to_date or from_date, include_unavailable=include_unavailable)


# ─── Doctor's own schedule shortcuts (no doctor_id in the path — resolved
# from ctx) — the doctor/schedule frontend page only ever manages its own
# calendar, never someone else's. ──────────────────────────────────────────

@router.get("/schedule/my")
async def get_my_schedule(db=Depends(get_db), ctx: RequestContext = Depends(require_role("doctor"))):
    own = await _own_doctor_row(db, ctx)
    weekly = await WeeklyScheduleService(db).list_for_doctor(own["doctor_id"])
    overrides = await ScheduleOverrideService(db).list_for_doctor(own["doctor_id"])
    return {"weekly": weekly, "overrides": overrides}


@router.put("/schedule/my", response_model=list[s.WeeklyScheduleRead])
async def replace_my_schedule(body: s.MyWeeklyScheduleReplace, db=Depends(get_db), ctx: RequestContext = Depends(require_role("doctor"))):
    from sqlalchemy import text

    row = (await db.execute(text("SELECT profile_id, clinic_id FROM doctors WHERE profile_id = :pid"), {"pid": ctx.user_id})).mappings().first()
    if not row or not row["clinic_id"]:
        raise NotFoundError("No doctor record (or clinic assignment) found for your account", code="DOCTOR_NOT_FOUND")
    items = [item.model_dump() for item in body.items]
    return await WeeklyScheduleService(db).replace_own(doctor_profile_id=UUID(ctx.user_id), clinic_id=row["clinic_id"], items=items)


@router.post("/schedule/my/overrides", response_model=s.ScheduleOverrideRead, status_code=201)
async def create_my_override(body: s.MyScheduleOverrideCreate, db=Depends(get_db), ctx: RequestContext = Depends(require_role("doctor"))):
    own = await _own_doctor_row(db, ctx)
    return await ScheduleOverrideService(db).create_own(
        doctor_profile_id=UUID(ctx.user_id), clinic_id=own["clinic_id"], data=body.model_dump(), created_by=UUID(ctx.user_id)
    )


@router.delete("/schedule/my/overrides/{override_id}", status_code=204)
async def delete_my_override(override_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role("doctor"))):
    await ScheduleOverrideService(db).delete_own(override_id, doctor_profile_id=UUID(ctx.user_id))


@router.post("/appointment-requests", response_model=s.AppointmentRequestRead, status_code=201)
async def create_appointment_request(body: s.AppointmentRequestCreate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await AppointmentRequestService(db).create(body.model_dump(), submitted_by=UUID(ctx.user_id), ctx=ctx)


@router.get("/appointment-requests", response_model=list[s.AppointmentRequestRead])
async def list_appointment_requests(clinic_id: UUID | None = None, doctor_id: UUID | None = None, status: str | None = None,
                                     db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await AppointmentRequestService(db).list(ctx=ctx, clinic_id=clinic_id, doctor_id=doctor_id, status=status)


@router.get("/appointment-requests/{request_id}", response_model=s.AppointmentRequestRead)
async def get_appointment_request(request_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    req = await AppointmentRequestService(db).get(request_id)
    if ctx.role == "patient" and str(req["patient_id"]) != ctx.user_id:
        raise PermissionError_("You can only view your own request", code="PATIENT_SCOPE_MISMATCH")
    return req


@router.patch("/appointment-requests/{request_id}/decision", response_model=s.AppointmentRequestRead)
async def decide_appointment_request(request_id: UUID, body: s.AppointmentRequestDecision, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await AppointmentRequestService(db).decide(request_id, body.model_dump(), reviewed_by=UUID(ctx.user_id), ctx=ctx)


@router.post("/appointments", response_model=s.AppointmentRead, status_code=201)
async def create_appointment(body: s.AppointmentCreate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await AppointmentService(db).create(body.model_dump(), booked_by=UUID(ctx.user_id), booked_by_role=ctx.role, ctx=ctx)


@router.get("/appointments/upcoming", response_model=list[s.AppointmentRead])
async def list_upcoming_appointments(db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await AppointmentService(db).list_upcoming(ctx=ctx)


@router.get("/appointments/today", response_model=list[s.AppointmentRead])
async def list_today_appointments(db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await AppointmentService(db).list_today(ctx=ctx)


@router.get("/appointments", response_model=list[s.AppointmentRead])
async def list_appointments(
    clinic_id: UUID | None = None, doctor_id: UUID | None = None, patient_id: UUID | None = None,
    status: str | None = None, date_from: date | None = None, date_to: date | None = None,
    skip: int = 0, limit: int = 100,
    db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    return await AppointmentService(db).list(
        ctx=ctx, clinic_id=clinic_id, doctor_id=doctor_id, patient_id=patient_id, status=status,
        date_from=date_from, date_to=date_to, skip=skip, limit=limit,
    )


@router.get("/appointments/{appointment_id}", response_model=s.AppointmentRead)
async def get_appointment(appointment_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    appt = await AppointmentService(db).get(appointment_id)
    if ctx.role == "patient" and str(appt["patient_id"]) != ctx.user_id:
        raise PermissionError_("You can only view your own appointment", code="PATIENT_SCOPE_MISMATCH")
    if ctx.role == "doctor" and str(appt["doctor_id"]) != ctx.user_id:
        raise PermissionError_("You can only view your own appointment", code="NOT_YOUR_APPOINTMENT")
    return appt


@router.patch("/appointments/{appointment_id}", response_model=s.AppointmentRead)
async def update_appointment(appointment_id: UUID, body: s.AppointmentUpdate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await AppointmentService(db).update_fields(appointment_id, body.model_dump(exclude_unset=True), ctx=ctx)


@router.patch("/appointments/{appointment_id}/reschedule", response_model=s.AppointmentRead)
async def reschedule_appointment(appointment_id: UUID, body: s.AppointmentReschedule, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await AppointmentService(db).reschedule(appointment_id, body.model_dump(), changed_by=UUID(ctx.user_id), changed_by_role=ctx.role, ctx=ctx)


@router.post("/appointments/{appointment_id}/request-reschedule", response_model=s.AppointmentRequestRead, status_code=201)
async def request_reschedule(appointment_id: UUID, body: s.AppointmentRescheduleRequestCreate, db=Depends(get_db), ctx: RequestContext = Depends(require_role("patient"))):
    return await AppointmentRequestService(db).create_reschedule_request(appointment_id, body.model_dump(), submitted_by=UUID(ctx.user_id), ctx=ctx)


@router.patch("/appointments/{appointment_id}/status", response_model=s.AppointmentRead)
async def update_appointment_status(appointment_id: UUID, body: s.AppointmentStatusUpdate, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await AppointmentService(db).update_status(
        appointment_id, status=body.status, changed_by=UUID(ctx.user_id), changed_by_role=ctx.role, ctx=ctx, cancellation_reason=body.cancellation_reason,
    )


@router.get("/appointments/{appointment_id}/audit-log")
async def get_appointment_audit_log(appointment_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    appt = await AppointmentService(db).get(appointment_id)
    if ctx.role == "patient" and str(appt["patient_id"]) != ctx.user_id:
        raise PermissionError_("You can only view your own appointment", code="PATIENT_SCOPE_MISMATCH")
    return await AppointmentService(db).audit_log(appointment_id)
