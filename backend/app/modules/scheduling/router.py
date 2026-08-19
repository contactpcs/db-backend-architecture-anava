from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.exceptions import NotFoundError, PermissionError_
from app.core.permissions import require_role
from app.modules.scheduling import schemas as s
from app.modules.scheduling.service import (
    AppointmentService,
    AvailabilityService,
    ClinicDeviceScheduleService,
    ClinicDeviceService,
    DeviceCapacityService,
    PatientBookingService,
    ScheduleOverrideService,
    WeeklyScheduleService,
)

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")
# Who may set when device sessions run and how many at once. Regional admin is
# included because they manage clinics in their region; the RLS policies on
# clinic_device_schedules (36 §8) scope each of these to the right clinics.
_DEVICE_SCHEDULE_ADMINS = ("super_admin", "regional_admin", "clinic_admin")


async def _own_doctor_row(db, ctx: RequestContext) -> dict:
    from sqlalchemy import text

    row = (
        (await db.execute(text("SELECT doctor_id, clinic_id FROM doctors WHERE profile_id = :pid"), {"pid": ctx.user_id}))
        .mappings()
        .first()
    )
    if not row or not row["clinic_id"]:
        raise NotFoundError("No doctor record (or clinic assignment) found for your account", code="DOCTOR_NOT_FOUND")
    return dict(row)


@router.post("/doctors/{doctor_id}/weekly-schedules", response_model=s.WeeklyScheduleRead, status_code=201)
async def create_weekly_schedule(
    doctor_id: UUID,
    body: s.WeeklyScheduleCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    data = body.model_dump()
    data["clinic_id"] = str(data["clinic_id"])
    return await WeeklyScheduleService(db).create(doctor_id, data, created_by=UUID(ctx.user_id))


@router.get("/doctors/{doctor_id}/weekly-schedules", response_model=list[s.WeeklyScheduleRead])
async def list_weekly_schedules(doctor_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await WeeklyScheduleService(db).list_for_doctor(doctor_id)


@router.post("/doctors/{doctor_id}/schedule-overrides", response_model=s.ScheduleOverrideRead, status_code=201)
async def create_schedule_override(
    doctor_id: UUID,
    body: s.ScheduleOverrideCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    data = body.model_dump()
    data["clinic_id"] = str(data["clinic_id"])
    return await ScheduleOverrideService(db).create(doctor_id, data, created_by=UUID(ctx.user_id))


@router.get("/doctors/{doctor_id}/schedule-overrides", response_model=list[s.ScheduleOverrideRead])
async def list_schedule_overrides(
    doctor_id: UUID,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    return await ScheduleOverrideService(db).list_for_doctor(doctor_id)


@router.get("/doctors/{doctor_id}/availability", response_model=list[s.AvailabilitySlotRead])
async def get_availability(
    doctor_id: UUID,
    from_date: date,
    to_date: date | None = None,
    include_unavailable: bool = True,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
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

    row = (
        (await db.execute(text("SELECT profile_id, clinic_id FROM doctors WHERE profile_id = :pid"), {"pid": ctx.user_id}))
        .mappings()
        .first()
    )
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
    clinic_id: UUID | None = None,
    doctor_id: UUID | None = None,
    patient_id: UUID | None = None,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    skip: int = 0,
    limit: int = 100,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    return await AppointmentService(db).list(
        ctx=ctx,
        clinic_id=clinic_id,
        doctor_id=doctor_id,
        patient_id=patient_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
        skip=skip,
        limit=limit,
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
async def update_appointment(
    appointment_id: UUID,
    body: s.AppointmentUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    return await AppointmentService(db).update_fields(appointment_id, body.model_dump(exclude_unset=True), ctx=ctx)


@router.patch("/appointments/{appointment_id}/reschedule", response_model=s.AppointmentRead)
async def reschedule_appointment(
    appointment_id: UUID,
    body: s.AppointmentReschedule,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    return await AppointmentService(db).reschedule(
        appointment_id, body.model_dump(), changed_by=UUID(ctx.user_id), changed_by_role=ctx.role, ctx=ctx
    )


@router.patch("/appointments/{appointment_id}/status", response_model=s.AppointmentRead)
async def update_appointment_status(
    appointment_id: UUID,
    body: s.AppointmentStatusUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    return await AppointmentService(db).update_status(
        appointment_id,
        status=body.status,
        changed_by=UUID(ctx.user_id),
        changed_by_role=ctx.role,
        ctx=ctx,
        cancellation_reason=body.cancellation_reason,
    )


@router.get("/appointments/{appointment_id}/audit-log")
async def get_appointment_audit_log(
    appointment_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))
):
    appt = await AppointmentService(db).get(appointment_id)
    if ctx.role == "patient" and str(appt["patient_id"]) != ctx.user_id:
        raise PermissionError_("You can only view your own appointment", code="PATIENT_SCOPE_MISMATCH")
    return await AppointmentService(db).audit_log(appointment_id)


# ═══════════════════════════════════════════════════════════════════════════
# Patient self-service
#
# Every endpoint here resolves clinic, doctor and patient from the caller's own
# record. A patient never sends any of those ids, so there is nothing to spoof —
# and no doctor to choose, because allocation already happened automatically at
# registration_complete.
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/me/appointments", response_model=list[s.AppointmentRead])
async def my_appointments(
    include_past: bool = False,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("patient")),
):
    """Every appointment of every type, protocol-generated planned sessions
    included — those are exactly what the patient needs to see in order to claim
    a slot for them."""
    return await PatientBookingService(db).my_appointments(ctx, include_past=include_past)


@router.get("/me/appointments/availability", response_model=list[s.AvailabilitySlotRead])
async def my_availability(
    from_date: date,
    to_date: date | None = None,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("patient")),
):
    """Open slots on the caller's own allocated doctor calendar. Serves both
    initial and follow-up booking."""
    return await PatientBookingService(db).my_availability(ctx, from_date, to_date or from_date)


@router.post("/me/appointments/initial", response_model=s.AppointmentRead, status_code=201)
async def book_initial(
    body: s.MyAppointmentBook,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("patient")),
):
    return await PatientBookingService(db).book_initial(ctx, body.model_dump())


@router.post("/me/appointments/follow-up", response_model=s.AppointmentRead, status_code=201)
async def book_follow_up(
    body: s.MyAppointmentBook,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("patient")),
):
    return await PatientBookingService(db).book_follow_up(ctx, body.model_dump())


@router.get("/me/appointments/{appointment_id}/device-availability", response_model=list[s.DeviceSlotRead])
async def my_device_availability(
    appointment_id: UUID,
    from_date: date,
    to_date: date | None = None,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("patient")),
):
    """Device slots at the clinic of one planned session.

    Separate from /availability because this reads a different pool entirely:
    clinic device capacity, not a doctor calendar. Only slots with room left are
    returned.
    """
    return await PatientBookingService(db).device_availability(appointment_id, ctx, from_date, to_date or from_date)


@router.patch("/me/appointments/{appointment_id}/claim-slot", response_model=s.AppointmentRead)
async def claim_slot(
    appointment_id: UUID,
    body: s.MyClaimSlot,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("patient")),
):
    """Puts a time on a protocol-generated planned session.

    One endpoint for BOTH protocol-born types — a protocol_followup claims a
    slot on the doctor calendar, a device_session claims clinic capacity.
    Everything downstream is identical.
    """
    return await PatientBookingService(db).claim_slot(appointment_id, ctx, body.model_dump())


@router.patch("/me/appointments/{appointment_id}/reschedule", response_model=s.AppointmentRead)
async def reschedule_own(
    appointment_id: UUID,
    body: s.AppointmentReschedule,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("patient")),
):
    return await PatientBookingService(db).reschedule_own(appointment_id, ctx, body.model_dump())


@router.patch("/me/appointments/{appointment_id}/cancel", response_model=s.AppointmentRead)
async def cancel_own(
    appointment_id: UUID,
    body: s.MyCancel,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("patient")),
):
    return await PatientBookingService(db).cancel_own(appointment_id, ctx, reason=body.reason)


# ═══════════════════════════════════════════════════════════════════════════
# Clinic device schedule — clinic-admin side
#
# When each of the clinic's devices can run sessions, and how many at once. One
# pool per device (clinic_device_id) — see 41_device_capacity_per_device.sql —
# not one blanket number for the whole clinic. Doctor calendars are managed
# separately above and are untouched by any of this.
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/clinics/{clinic_id}/device-schedules", response_model=list[s.ClinicDeviceScheduleOverview])
async def list_device_schedules(
    clinic_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    """Every device this clinic owns, each with its current week (empty if
    unset). Readable by patients too — they need the device hours to understand
    which slots exist before claiming one. Exposes opening hours and a capacity
    number, no patient data."""
    return await ClinicDeviceScheduleService(db).overview(clinic_id, ctx)


@router.get("/clinics/{clinic_id}/devices/{clinic_device_id}/schedule", response_model=list[s.DeviceScheduleRead])
async def get_device_schedule(
    clinic_id: UUID,
    clinic_device_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    return await ClinicDeviceScheduleService(db).device_week(clinic_id, clinic_device_id, ctx)


@router.put("/clinics/{clinic_id}/devices/{clinic_device_id}/schedule", response_model=list[s.DeviceScheduleRead])
async def replace_device_schedule(
    clinic_id: UUID,
    clinic_device_id: UUID,
    body: s.DeviceScheduleReplace,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_DEVICE_SCHEDULE_ADMINS)),
):
    """Atomic replace of one device's whole week — same shape as a doctor
    redrawing their own timetable."""
    items = [item.model_dump() for item in body.items]
    return await ClinicDeviceScheduleService(db).replace_device_week(clinic_id, clinic_device_id, items, ctx)


@router.get("/clinics/{clinic_id}/devices/{clinic_device_id}/schedule/overrides", response_model=list[s.DeviceOverrideRead])
async def list_device_overrides(
    clinic_id: UUID,
    clinic_device_id: UUID,
    from_date: date | None = None,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    return await ClinicDeviceScheduleService(db).device_overrides(clinic_id, clinic_device_id, ctx, from_date=from_date)


@router.post("/clinics/{clinic_id}/devices/{clinic_device_id}/schedule/overrides", response_model=s.DeviceOverrideRead, status_code=201)
async def create_device_override(
    clinic_id: UUID,
    clinic_device_id: UUID,
    body: s.DeviceOverrideCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_DEVICE_SCHEDULE_ADMINS)),
):
    """A single-day exception: a closure, different hours, or reduced capacity
    because this device is out for service or an assistant is on leave."""
    return await ClinicDeviceScheduleService(db).create_device_override(clinic_id, clinic_device_id, body.model_dump(), ctx)


@router.delete("/clinics/{clinic_id}/devices/{clinic_device_id}/schedule/overrides/{override_id}", status_code=204)
async def delete_device_override(
    clinic_id: UUID,
    clinic_device_id: UUID,
    override_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_DEVICE_SCHEDULE_ADMINS)),
):
    await ClinicDeviceScheduleService(db).delete_device_override(clinic_id, clinic_device_id, override_id, ctx)


@router.get("/clinics/{clinic_id}/devices/{clinic_device_id}/availability", response_model=list[s.DeviceSlotRead])
async def clinic_device_availability(
    clinic_id: UUID,
    clinic_device_id: UUID,
    from_date: date,
    to_date: date | None = None,
    only_available: bool = False,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    """Every slot in the range for ONE device, with capacity, booked and
    remaining. The admin capacity board and the patient slot picker ask the
    same question, so they are the same endpoint — only_available filters it
    down for the patient view."""
    return await DeviceCapacityService(db).open_slots(clinic_device_id, from_date, to_date or from_date, only_available=only_available)


# ═══════════════════════════════════════════════════════════════════════════
# Clinic device inventory — clinic-admin side
#
# Which devices this clinic owns. Feeds the protocol picker at Step 1
# (GET /neuromod/devices?clinic_id=...), and is independently enforced by
# trg_check_device_available_at_clinic so a stale tab cannot prescribe hardware
# that is not in the building.
#
# Distinct from /clinics/{id}/device-schedules, which says WHEN sessions run and
# HOW MANY at once. This says WHAT the clinic has.
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/clinics/{clinic_id}/devices", response_model=list[s.ClinicDeviceRead])
async def list_clinic_devices(
    clinic_id: UUID,
    active_only: bool = True,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    """The clinic's inventory, with device names hydrated from the catalogue."""
    return await ClinicDeviceService(db).list_for_clinic(clinic_id, ctx, active_only=active_only)


@router.post("/clinics/{clinic_id}/devices", response_model=s.ClinicDeviceRead, status_code=201)
async def add_clinic_device(
    clinic_id: UUID,
    body: s.ClinicDeviceCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_DEVICE_SCHEDULE_ADMINS)),
):
    return await ClinicDeviceService(db).add(clinic_id, body.model_dump(), ctx)


@router.patch("/clinics/{clinic_id}/devices/{clinic_device_id}", response_model=s.ClinicDeviceRead)
async def update_clinic_device(
    clinic_id: UUID,
    clinic_device_id: UUID,
    body: s.ClinicDeviceUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_DEVICE_SCHEDULE_ADMINS)),
):
    """Change the quantity, or set is_active=false to retire a device.

    Retiring stops the picker offering it; protocols already prescribed on it
    keep working, because the guard trigger only checks new prescriptions.
    """
    return await ClinicDeviceService(db).update(clinic_id, clinic_device_id, body.model_dump(exclude_unset=True), ctx)


@router.delete("/clinics/{clinic_id}/devices/{clinic_device_id}", status_code=204)
async def remove_clinic_device(
    clinic_id: UUID,
    clinic_device_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_DEVICE_SCHEDULE_ADMINS)),
):
    """Removes a listing entered in error.

    Refuses with CLINIC_DEVICE_IN_USE if a protocol at this clinic has ever
    prescribed the device — deactivate it instead, so history keeps making sense.
    """
    await ClinicDeviceService(db).remove(clinic_id, clinic_device_id, ctx)
