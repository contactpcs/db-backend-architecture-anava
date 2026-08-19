from __future__ import annotations

import builtins
import datetime as dt
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.db import RequestContext
from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, PermissionError_
from app.core.resolve import resolve_doctor_profile_id as _resolve_doctor_profile_id
from app.core.resolve import resolve_patient_profile_id as _resolve_patient_profile_id
from app.core.scoping import assert_clinic_scope, assert_owns_profile
from app.modules.scheduling.repository import (
    AppointmentAuditLogRepository,
    AppointmentRepository,
    ClinicDeviceRepository,
    ClinicDeviceScheduleRepository,
    ScheduleOverrideRepository,
    WeeklyScheduleRepository,
)

# ── the four kinds of clinic visit ──────────────────────────────────────────
# All four live on core.appointments. They differ in who creates the row, what
# state it starts in, and — crucially — which pool of time it books against.
#
#   type                created by                starts    books against
#   initial             patient                   selected  doctor's schedule
#   follow_up           patient                   selected  doctor's schedule
#   protocol_followup   doctor, at protocol setup  planned   doctor's schedule
#   device_session      doctor, at protocol setup  planned   CLINIC capacity
#
# The last row is the whole reason DeviceCapacityService exists: a device
# session is run by a clinical assistant and consumes no doctor's time, so it
# carries doctor_id = NULL and must never touch a doctor's calendar.
TYPE_INITIAL = "initial"
TYPE_FOLLOW_UP = "follow_up"
TYPE_DEVICE_SESSION = "device_session"
TYPE_PROTOCOL_FOLLOWUP = "protocol_followup"

PATIENT_BOOKABLE_TYPES = {TYPE_INITIAL, TYPE_FOLLOW_UP}
PROTOCOL_BORN_TYPES = {TYPE_DEVICE_SESSION, TYPE_PROTOCOL_FOLLOWUP}
# Everything except a device session consumes a doctor's time.
DOCTOR_SCHEDULED_TYPES = {TYPE_INITIAL, TYPE_FOLLOW_UP, TYPE_PROTOCOL_FOLLOWUP}

# ── the lifecycle ───────────────────────────────────────────────────────────
# planned    doctor set a DATE at protocol setup. No slot, no hold.
# selected   patient picked a slot. NOT PAID. Held, and the hold expires.
# paid       payment captured. Slot locked, hold cleared. PAYMENT CONFIRMS A
#            VISIT — nobody else does.
# then       checked_in -> in_progress -> completed, or no_show.
STATUS_PLANNED = "planned"
STATUS_SELECTED = "selected"
STATUS_PAID = "paid"

# Statuses that occupy a slot. Matches the predicate on
# idx_appointments_device_capacity and the capacity count in the repository —
# change one and all three must change together.
SLOT_OCCUPYING_STATUSES = {STATUS_SELECTED, STATUS_PAID, "checked_in", "in_progress"}
# What may still be cancelled or rescheduled.
ACTIVE_STATUSES = {STATUS_PLANNED, STATUS_SELECTED, STATUS_PAID, "checked_in", "in_progress"}

CANCEL_MIN_HOURS = 2
RESCHEDULE_MIN_HOURS = 24
DEFAULT_SLOT_MINUTES = 30

# Which statuses a transition is legal FROM.
#
# 'confirmed' and 'scheduled' are deliberately gone: payment is what confirms a
# visit, so 'paid' says it once instead of two states saying it twice.
#
# planned -> cancelled is legal because the protocol module cancels its own
# unclaimed rows that way when a doctor cancels the whole protocol
# (treatment_protocols/repository.py::cancel_planned).
_ALLOWED_FROM = {
    STATUS_SELECTED: {STATUS_PLANNED},
    STATUS_PAID: {STATUS_SELECTED},
    "checked_in": {STATUS_PAID},
    "in_progress": {"checked_in"},
    "completed": {"in_progress"},
    "no_show": {STATUS_PAID, "checked_in"},
    "cancelled": ACTIVE_STATUSES,
    "rescheduled": {STATUS_SELECTED, STATUS_PAID},
}

# Reaching these means the visit is physically happening, so only whoever is
# actually running it may set them. WHO that is depends on the type — see
# _authorize_transition. A device session has no treating doctor at all.
_ATTENDANCE_STATUSES = {"in_progress", "completed"}


async def _get_patient_row(session: AsyncSession, *, profile_id: UUID) -> dict:
    """Full patients row by profile_id — used to auto-resolve a request's
    clinic_id/doctor_id from the patient's own record (v1's request_service
    does the same: 'resolves assigned_doctor_id + clinic from the patient
    row'), and to check patient-self-ownership."""
    row = (await session.execute(text("SELECT * FROM patients WHERE profile_id = :pid"), {"pid": str(profile_id)})).mappings().first()
    if not row:
        raise NotFoundError("Patient record not found", code="PATIENT_NOT_FOUND")
    return dict(row)


def _initial_status_and_hold(settings) -> tuple[str, dt.datetime | None]:
    """The payment seam, in one place.

    payment_required False -> land on 'paid', no hold, sweeper idle.
    payment_required True  -> land on 'selected' with a hold; the gateway
                              webhook calls mark_paid() to finish.

    Both satisfy chk_appointments_hold, which requires exactly the
    'selected' rows to carry an expiry and no others. Used by every booking
    path (patient self-booking and staff booking) — a row without one of
    these two statuses set explicitly falls back to the DB column default
    ('scheduled'), a value the status FSM below has no transition for at all.
    """
    if settings.appointment_payment_required:
        return STATUS_SELECTED, dt.datetime.now(dt.UTC) + dt.timedelta(minutes=settings.appointment_hold_minutes)
    return STATUS_PAID, None


def _hours_until(appointment_date: dt.date, start_time: dt.time) -> float:
    target = dt.datetime.combine(appointment_date, start_time)
    return (target - dt.datetime.now()).total_seconds() / 3600.0


def _slot_minutes(start_time: dt.time, end_time: dt.time) -> int:
    return int((dt.datetime.combine(dt.date.today(), end_time) - dt.datetime.combine(dt.date.today(), start_time)).total_seconds() // 60)


def _build_day_slots(on_date: dt.date, weekly_rows: list[dict], override: dict | None, booked_ranges: set[tuple]) -> list[dict]:
    """Pure — no I/O. Ports v1's schedule_service.build_day_slots: override
    (blocked or special hours) takes precedence over the weekly template;
    otherwise picks the first active weekly rule for this day-of-week whose
    effective_from/until window covers the date; steps by slot_duration,
    skipping any step that overlaps a break; marks is_available by whether
    that exact (start,end) pair is already booked."""
    if override and not override["is_available"]:
        return []
    if override and override["is_available"]:
        start, end = override["start_time"], override["end_time"]
        slot_minutes = override["slot_duration_minutes"] or DEFAULT_SLOT_MINUTES
        break_start, break_end = None, None
    else:
        # Postgres EXTRACT(DOW): 0=Sunday..6=Saturday. Python's date.isoweekday()
        # is 1=Monday..7=Sunday — %7 maps Sunday(7) to 0 and leaves Mon-Sat
        # (1-6) unchanged, landing exactly on the Postgres convention.
        dow = on_date.isoweekday() % 7
        rule = next(
            (
                w
                for w in weekly_rows
                if w["day_of_week"] == dow
                and (w.get("effective_from") is None or w["effective_from"] <= on_date)
                and (w.get("effective_until") is None or w["effective_until"] >= on_date)
            ),
            None,
        )
        if not rule:
            return []
        start, end, slot_minutes = rule["start_time"], rule["end_time"], rule["slot_duration_minutes"]
        break_start, break_end = rule.get("break_start"), rule.get("break_end")

    return [
        {"date": on_date, "start_time": s, "end_time": e, "is_available": (s, e) not in booked_ranges}
        for s, e in _step_slots(on_date, start, end, slot_minutes, break_start, break_end)
    ]


def _step_slots(
    on_date: dt.date, start: dt.time, end: dt.time, slot_minutes: int, break_start: dt.time | None, break_end: dt.time | None
) -> list[tuple[dt.time, dt.time]]:
    """Walks a working window in fixed steps, skipping any step that overlaps
    the break. Pure, and shared by both schedule kinds — a doctor's calendar and
    a clinic's device schedule produce slots identically; they differ only in
    what makes a slot *available*, which is the caller's business."""
    out: list[tuple[dt.time, dt.time]] = []
    current = dt.datetime.combine(on_date, start)
    end_dt = dt.datetime.combine(on_date, end)
    step = dt.timedelta(minutes=slot_minutes)
    while current + step <= end_dt:
        slot_start, slot_end = current.time(), (current + step).time()
        if not (break_start and break_end and slot_start < break_end and slot_end > break_start):
            out.append((slot_start, slot_end))
        current += step
    return out


def _build_device_day_slots(on_date: dt.date, weekly_rows: list[dict], override: dict | None, booked_counts: dict) -> list[dict]:
    """Device-session slots for one day at one clinic.

    Same stepping as a doctor's day, one difference that matters: a doctor's
    slot is available when nobody has it, a clinic's device slot is available
    while FEWER THAN `capacity` people have it. Exclusive versus counted — the
    distinction the whole capacity design rests on.
    """
    if override and not override["is_available"]:
        return []

    dow = on_date.isoweekday() % 7
    rule = next(
        (
            w
            for w in weekly_rows
            if w["day_of_week"] == dow
            and (w.get("effective_from") is None or w["effective_from"] <= on_date)
            and (w.get("effective_until") is None or w["effective_until"] >= on_date)
        ),
        None,
    )
    if not rule and not (override and override["is_available"]):
        return []

    if override and override["is_available"]:
        # `rule and rule[...]` returns the RULE DICT when rule is an empty dict,
        # not the field - which is where the "expected time, got dict" errors
        # came from. Reading through an explicit None check keeps each name at
        # the type _step_slots expects.
        start = override["start_time"] or (rule["start_time"] if rule else None)
        end = override["end_time"] or (rule["end_time"] if rule else None)
        # A per-day capacity override is how a clinic says "a device is out for
        # service today" or "one assistant is on leave" without editing the week.
        capacity = override["capacity"] or (rule["capacity"] if rule else None)
        slot_minutes = (rule["slot_duration_minutes"] if rule else None) or DEFAULT_SLOT_MINUTES
        break_start = break_end = None
    else:
        # Reaching here means the guard above did not return, and it returns
        # whenever rule is None and the override is not an available one. So
        # rule is non-None — but that follows from a compound condition mypy
        # cannot narrow through, hence the explicit assert rather than a cast.
        assert rule is not None
        start, end = rule["start_time"], rule["end_time"]
        capacity = rule["capacity"]
        slot_minutes = rule["slot_duration_minutes"]
        break_start, break_end = rule.get("break_start"), rule.get("break_end")

    if start is None or end is None or not capacity:
        return []

    slots = []
    for slot_start, slot_end in _step_slots(on_date, start, end, slot_minutes, break_start, break_end):
        booked = booked_counts.get((on_date, slot_start), 0)
        slots.append(
            {
                "date": on_date,
                "start_time": slot_start,
                "end_time": slot_end,
                "capacity": capacity,
                "booked": booked,
                "remaining": max(0, capacity - booked),
                "is_available": booked < capacity,
            }
        )
    return slots


class WeeklyScheduleService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = WeeklyScheduleRepository(session)

    async def create(self, doctor_id: UUID, data: dict, *, created_by: UUID) -> dict:
        doctor_profile_id = await _resolve_doctor_profile_id(self.session, doctor_id)
        payload = {**data, "doctor_id": str(doctor_profile_id), "clinic_id": str(data["clinic_id"]), "created_by": str(created_by)}
        try:
            return await self.repo.create(payload)
        except IntegrityError as exc:
            raise ConflictError("A weekly schedule rule already exists for this doctor/clinic/day", code="SCHEDULE_ALREADY_EXISTS") from exc

    async def list_for_doctor(self, doctor_id: UUID) -> list[dict]:
        doctor_profile_id = await _resolve_doctor_profile_id(self.session, doctor_id)
        return await self.repo.list_for_doctor(doctor_profile_id)

    async def replace_own(self, *, doctor_profile_id: UUID, clinic_id: UUID, items: list[dict]) -> list[dict]:
        return await self.repo.replace_for_doctor(doctor_profile_id, clinic_id, items, created_by=doctor_profile_id)


class ScheduleOverrideService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ScheduleOverrideRepository(session)

    async def create(self, doctor_id: UUID, data: dict, *, created_by: UUID) -> dict:
        doctor_profile_id = await _resolve_doctor_profile_id(self.session, doctor_id)
        return await self._create_for(doctor_profile_id, data["clinic_id"], data, created_by=created_by)

    async def create_own(self, *, doctor_profile_id: UUID, clinic_id: UUID, data: dict, created_by: UUID) -> dict:
        return await self._create_for(doctor_profile_id, clinic_id, data, created_by=created_by)

    async def _create_for(self, doctor_profile_id: UUID, clinic_id: UUID, data: dict, *, created_by: UUID) -> dict:
        payload = {**data, "doctor_id": str(doctor_profile_id), "clinic_id": str(clinic_id), "created_by": str(created_by)}
        try:
            return await self.repo.create(payload)
        except IntegrityError as exc:
            raise ConflictError("An override already exists for this doctor/date", code="OVERRIDE_ALREADY_EXISTS") from exc

    async def list_for_doctor(self, doctor_id: UUID) -> list[dict]:
        doctor_profile_id = await _resolve_doctor_profile_id(self.session, doctor_id)
        return await self.repo.list_for_doctor(doctor_profile_id)

    async def delete_own(self, override_id: UUID, *, doctor_profile_id: UUID) -> None:
        override = await self.repo.get(override_id)
        if not override or str(override["doctor_id"]) != str(doctor_profile_id):
            raise NotFoundError("Override not found", code="OVERRIDE_NOT_FOUND")
        await self.repo.delete(override_id)


class AvailabilityService:
    """Merges weekly recurring schedule + date overrides - existing
    appointments to produce open slots, day by day, for a date range.
    Deliberately uncached (an earlier Redis-cached version of this existed —
    dropped it here: a range query multiplies the possible cache keys and a
    missed invalidation on a newly-booked slot is a correctness bug, not
    just a staleness one, for a feature this session-critical. Add caching
    back only if profiling actually shows this join is slow — it's a handful
    of small indexed queries, not the expensive join the original comment
    anticipated)."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.weekly = WeeklyScheduleRepository(session)
        self.overrides = ScheduleOverrideRepository(session)
        self.appointments = AppointmentRepository(session)

    async def compute_range(self, doctor_id: UUID, from_date: dt.date, to_date: dt.date, *, include_unavailable: bool = True) -> list[dict]:
        doctor_profile_id = await _resolve_doctor_profile_id(self.session, doctor_id)
        return await self._compute_for_profile(doctor_profile_id, from_date, to_date, include_unavailable=include_unavailable)

    async def _compute_for_profile(
        self, doctor_profile_id: UUID, from_date: dt.date, to_date: dt.date, *, include_unavailable: bool = True
    ) -> list[dict]:
        if to_date < from_date:
            raise BusinessRuleError("to_date must not be before from_date", code="INVALID_DATE_RANGE")
        if (to_date - from_date).days > 60:
            raise BusinessRuleError("Date range too large (max 60 days)", code="AVAILABILITY_RANGE_TOO_LARGE")

        weekly_rows = await self.weekly.list_for_doctor(doctor_profile_id)
        overrides_by_date = await self.overrides.for_range(doctor_profile_id, from_date, to_date)
        booked = await self.appointments.list_for_doctor_in_range(doctor_profile_id, from_date, to_date)
        booked_by_date: dict[dt.date, set] = {}
        for b in booked:
            booked_by_date.setdefault(b["appointment_date"], set()).add((b["start_time"], b["end_time"]))

        all_slots: list[dict] = []
        current = from_date
        while current <= to_date:
            override = overrides_by_date.get(current)
            all_slots.extend(_build_day_slots(current, weekly_rows, override, booked_by_date.get(current, set())))
            current += dt.timedelta(days=1)

        if not include_unavailable:
            all_slots = [s for s in all_slots if s["is_available"]]
        return all_slots

    async def check_slot(self, doctor_profile_id: UUID, on_date: dt.date, start_time: dt.time) -> tuple[bool, int]:
        """(is_available, duration_minutes) for one exact start_time — used
        by AppointmentService to validate a booking request and derive
        end_time when the caller doesn't supply one (v1's schedule_service.
        check_slot)."""
        day_slots = await self._compute_for_profile(doctor_profile_id, on_date, on_date, include_unavailable=True)
        for slot in day_slots:
            if slot["start_time"] == start_time:
                return slot["is_available"], _slot_minutes(slot["start_time"], slot["end_time"])
        return False, DEFAULT_SLOT_MINUTES


class AppointmentService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = AppointmentRepository(session)
        self.audit = AppointmentAuditLogRepository(session)
        self.settings = get_settings()

    async def create(
        self,
        data: dict,
        *,
        booked_by: UUID,
        booked_by_role: str,
        ctx: RequestContext | None = None,
        _patient_profile_id_override=None,
        _doctor_profile_id_override=False,
    ) -> dict:
        patient_profile_id = _patient_profile_id_override or await _resolve_patient_profile_id(self.session, data["patient_id"])

        # A self-registered patient starts approval_status='pending' and is
        # not bookable until a receptionist has reviewed their ID (decide_
        # approval in patients/service.py). Staff-registered patients are
        # 'not_required' forever and skip this gate entirely.
        patient = await _get_patient_row(self.session, profile_id=patient_profile_id)
        if patient["approval_status"] not in ("not_required", "approved"):
            raise BusinessRuleError(
                "This patient's registration is still pending approval",
                code="PATIENT_APPROVAL_PENDING",
            )

        doctor_profile_id = (
            await _resolve_doctor_profile_id(self.session, data["doctor_id"])
            if data.get("doctor_id") and not _doctor_profile_id_override
            else data.get("doctor_id")
        )
        if not doctor_profile_id:
            raise NotFoundError("Doctor not found", code="DOCTOR_NOT_FOUND")
        doctor_profile_id = UUID(str(doctor_profile_id))

        if ctx is not None:
            await assert_clinic_scope(ctx, self.session, data["clinic_id"])

        is_available, duration = await AvailabilityService(self.session).check_slot(
            doctor_profile_id, data["appointment_date"], data["start_time"]
        )
        if not is_available:
            raise ConflictError("That slot is not available on the doctor's schedule", code="APPOINTMENT_SLOT_UNAVAILABLE")
        end_time = (
            data.get("end_time")
            or (dt.datetime.combine(data["appointment_date"], data["start_time"]) + dt.timedelta(minutes=duration)).time()
        )

        status, hold = _initial_status_and_hold(self.settings)
        payload = {
            "clinic_id": str(data["clinic_id"]),
            "patient_id": str(patient_profile_id),
            "doctor_id": str(doctor_profile_id),
            "ca_id": str(data["ca_id"]) if data.get("ca_id") else None,
            "cycle_id": str(data["cycle_id"]) if data.get("cycle_id") else None,
            "appointment_date": data["appointment_date"],
            "start_time": data["start_time"],
            "end_time": end_time,
            "slot_duration_minutes": duration,
            "appointment_type": data.get("appointment_type", TYPE_INITIAL),
            "status": status,
            "hold_expires_at": hold,
            "reason": data.get("reason"),
            "patient_complaint": data.get("patient_complaint"),
            "booked_by": str(booked_by),
            "booked_by_role": booked_by_role,
        }
        try:
            appointment = await self.repo.create(payload)
        except IntegrityError as exc:
            raise ConflictError("This doctor already has an appointment overlapping this time slot", code="APPOINTMENT_OVERLAP") from exc
        await emit_event(
            self.session,
            aggregate_type="appointment",
            aggregate_id=appointment["appointment_id"],
            event_type="appointment_booked",
            payload={"appointment_id": str(appointment["appointment_id"]), "doctor_id": str(doctor_profile_id)},
        )
        return await self.get(appointment["appointment_id"])

    async def get(self, appointment_id: UUID) -> dict:
        appt = await self.repo.get(appointment_id)
        if not appt:
            raise NotFoundError("Appointment not found", code="APPOINTMENT_NOT_FOUND")
        return appt

    async def list(
        self,
        *,
        ctx: RequestContext,
        clinic_id=None,
        doctor_id=None,
        patient_id=None,
        status=None,
        appointment_type=None,
        date_from=None,
        date_to=None,
        skip: int = 0,
        limit: int = 100,
    ) -> builtins.list[dict]:
        # v1: patient sees only their own, doctor only their own, staff
        # scoped to clinic (+ optional doctor_id/patient_id filters layered
        # on top) — never trusting a caller-supplied id to widen their view.
        # A caller-supplied patient_id is always patients.patient_id (the
        # public id every other module's routes use) — appointments.patient_id
        # is profiles.id, so it needs the same resolution AppointmentCreate
        # goes through, not a raw UUID cast.
        if ctx.role == "patient":
            patient_id, doctor_id, clinic_id = UUID(ctx.user_id), None, None
        elif ctx.role == "doctor":
            doctor_id = UUID(ctx.user_id)
            patient_id = await _resolve_patient_profile_id(self.session, patient_id) if patient_id else None
            clinic_id = None
        else:
            if ctx.role in ("clinic_admin", "receptionist", "clinical_assistant"):
                clinic_id = UUID(ctx.clinic_id) if ctx.clinic_id else None
            if patient_id:
                patient_id = await _resolve_patient_profile_id(self.session, patient_id)
            if doctor_id:
                doctor_id = await _resolve_doctor_profile_id(self.session, doctor_id)
        region_id = None
        if ctx.role == "regional_admin" and not clinic_id:
            region_id = UUID(ctx.region_id) if ctx.region_id else None
        return await self.repo.list(
            appointment_type=appointment_type,
            clinic_id=clinic_id,
            region_id=region_id,
            doctor_id=doctor_id,
            patient_id=patient_id,
            status=status,
            date_from=date_from,
            date_to=date_to,
            skip=skip,
            limit=limit,
        )

    async def list_upcoming(self, *, ctx: RequestContext, days: int = 14) -> builtins.list[dict]:
        today = dt.date.today()
        rows = await self.list(ctx=ctx, date_from=today, date_to=today + dt.timedelta(days=days), limit=200)
        return [r for r in rows if r["status"] in ACTIVE_STATUSES]

    async def list_today(self, *, ctx: RequestContext) -> builtins.list[dict]:
        today = dt.date.today()
        return await self.list(ctx=ctx, date_from=today, date_to=today, limit=200)

    async def _write_audit(
        self,
        appointment_id: UUID,
        *,
        changed_by: UUID,
        changed_by_role: str,
        previous_status,
        new_status,
        previous_date=None,
        new_date=None,
        previous_time=None,
        new_time=None,
        change_reason=None,
    ) -> None:
        await self.audit.create(
            {
                "appointment_id": str(appointment_id),
                "changed_by": str(changed_by),
                "changed_by_role": changed_by_role,
                "previous_status": previous_status,
                "new_status": new_status,
                "previous_date": previous_date,
                "new_date": new_date,
                "previous_time": previous_time,
                "new_time": new_time,
                "change_reason": change_reason,
            }
        )

    def _authorize_transition(self, appt: dict, *, status: str, ctx: RequestContext) -> None:
        allowed_from = _ALLOWED_FROM.get(status)
        if allowed_from is None:
            raise BusinessRuleError(f"Unknown status transition to {status!r}", code="INVALID_STATUS_TRANSITION")
        if appt["status"] not in allowed_from:
            raise BusinessRuleError(f"Cannot move an appointment from '{appt['status']}' to '{status}'", code="INVALID_STATUS_TRANSITION")

        if ctx.role == "patient":
            if status != "cancelled":
                raise PermissionError_("Patients can only cancel an appointment, not change its status", code="PATIENT_ACTION_NOT_ALLOWED")
            assert_owns_profile(ctx, appt["patient_id"])
            # A protocol-born visit is a prescription. A patient skipping one is
            # already handled by it simply staying 'planned' — there is nothing
            # to cancel, and cancelling would destroy the doctor's date.
            if appt["appointment_type"] in PROTOCOL_BORN_TYPES and appt["status"] == STATUS_PLANNED:
                raise PermissionError_(
                    "A prescribed session cannot be cancelled by the patient — book it when you are ready",
                    code="PROTOCOL_SESSION_NOT_PATIENT_CANCELLABLE",
                )
            # A 'planned' row has no time, so the notice window cannot apply and
            # there is no slot being given up late.
            if appt["start_time"] is not None and _hours_until(appt["appointment_date"], appt["start_time"]) < CANCEL_MIN_HOURS:
                raise BusinessRuleError(f"Cancellations require at least {CANCEL_MIN_HOURS} hours' notice", code="CANCEL_WINDOW_PASSED")
            return

        if status in _ATTENDANCE_STATUSES:
            # WHO may start and finish a visit depends on what kind it is.
            #
            # A device_session is administered by a clinical assistant on a
            # device and has NO treating doctor (doctor_id is NULL by design).
            # Requiring the doctor here — as this rule used to, unconditionally —
            # made it impossible for the assistant to start the session they are
            # standing in front of, and impossible for anyone else either.
            if appt["appointment_type"] == TYPE_DEVICE_SESSION:
                if ctx.role not in ("clinical_assistant", "super_admin"):
                    raise PermissionError_(
                        "Only the clinical assistant running the session can perform this action",
                        code="CLINICAL_ASSISTANT_ONLY_ACTION",
                    )
                # An assistant already running another session at this time is
                # caught by excl_ca_overlap when ca_id is written, not here.
                return
            if ctx.role == "doctor" and str(appt["doctor_id"]) != ctx.user_id:
                raise PermissionError_("You can only update your own appointments", code="NOT_YOUR_APPOINTMENT")
            if ctx.role not in ("doctor", "super_admin"):
                raise PermissionError_("Only the treating doctor can perform this action", code="DOCTOR_ONLY_ACTION")
            return

        # appt["doctor_id"] is NULL on a device session, so this comparison is
        # skipped for exactly the rows that have no doctor to compare against.
        if ctx.role == "doctor" and appt["doctor_id"] is not None and str(appt["doctor_id"]) != ctx.user_id:
            raise PermissionError_("You can only update your own appointments", code="NOT_YOUR_APPOINTMENT")
        # clinic_admin/receptionist/clinical_assistant/regional_admin/super_admin: clinic-scoped

    async def update_status(
        self, appointment_id: UUID, *, status: str, changed_by: UUID, changed_by_role: str, ctx: RequestContext, cancellation_reason=None
    ) -> dict:
        appt = await self.get(appointment_id)
        if ctx.role != "patient":
            await assert_clinic_scope(ctx, self.session, appt["clinic_id"])
        if status == "cancelled" and not cancellation_reason:
            raise BusinessRuleError("A cancellation reason is required", code="CANCELLATION_REASON_REQUIRED")
        self._authorize_transition(appt, status=status, ctx=ctx)

        # Late binding: an assistant is never reserved in advance. Whoever is
        # free takes the patient, and their identity is captured here — the
        # moment they start the session — not at booking time. Only for a
        # device session; a consultation's doctor was booked with the slot.
        ca_id = None
        if status == "in_progress" and appt["appointment_type"] == TYPE_DEVICE_SESSION and ctx.role == "clinical_assistant":
            ca_id = changed_by

        try:
            await self.repo.update_status(
                appointment_id,
                status=status,
                cancelled_by=changed_by if status == "cancelled" else None,
                cancellation_reason=cancellation_reason,
                ca_id=ca_id,
            )
        except IntegrityError as exc:
            # excl_ca_overlap fires here rather than at booking, because ca_id
            # is NULL until this moment. The assistant tapping "start" is the
            # one who needs to read this message.
            raise ConflictError(
                "You are already running another session at this time",
                code="CLINICAL_ASSISTANT_OVERLAP",
            ) from exc
        await self._write_audit(
            appointment_id,
            changed_by=changed_by,
            changed_by_role=changed_by_role,
            previous_status=appt["status"],
            new_status=status,
            change_reason=cancellation_reason,
        )
        await emit_event(
            self.session,
            aggregate_type="appointment",
            aggregate_id=appointment_id,
            event_type="appointment_cancelled" if status == "cancelled" else "appointment_status_changed",
            payload={"appointment_id": str(appointment_id), "status": status, "changed_by_role": changed_by_role},
        )
        return await self.get(appointment_id)

    async def update_fields(self, appointment_id: UUID, data: dict, *, ctx: RequestContext) -> dict:
        appt = await self.get(appointment_id)
        await assert_clinic_scope(ctx, self.session, appt["clinic_id"])
        if ctx.role == "doctor" and str(appt["doctor_id"]) != ctx.user_id:
            raise PermissionError_("You can only update your own appointments", code="NOT_YOUR_APPOINTMENT")
        fields = {k: v for k, v in data.items() if v is not None}
        return await self.repo.update_fields(appointment_id, fields) or appt

    async def reschedule(
        self,
        appointment_id: UUID,
        data: dict,
        *,
        changed_by: UUID,
        changed_by_role: str,
        ctx: RequestContext,
    ) -> dict:
        old = await self.get(appointment_id)
        if ctx.role == "patient":
            raise PermissionError_(
                "Patients cannot reschedule directly — submit a reschedule request instead",
                code="PATIENT_ACTION_NOT_ALLOWED",
            )
        if old["status"] not in ACTIVE_STATUSES:
            raise BusinessRuleError("Only an active appointment can be rescheduled", code="APPOINTMENT_NOT_ACTIVE")
        await assert_clinic_scope(ctx, self.session, old["clinic_id"])

        new_appointment = await self.create(
            {
                "clinic_id": old["clinic_id"],
                "patient_id": old["patient_id"],
                "doctor_id": old["doctor_id"],
                "ca_id": old["ca_id"],
                "cycle_id": old["cycle_id"],
                "appointment_date": data["appointment_date"],
                "start_time": data["start_time"],
                "end_time": data.get("end_time"),
                "appointment_type": old["appointment_type"],
                "reason": old.get("reason"),
                "patient_complaint": old.get("patient_complaint"),
            },
            booked_by=changed_by,
            booked_by_role=changed_by_role,
            _patient_profile_id_override=old["patient_id"],
            _doctor_profile_id_override=True,
        )
        await self.repo.reschedule(appointment_id, new_appointment_id=new_appointment["appointment_id"])
        await self.repo.update_fields(new_appointment["appointment_id"], {"rescheduled_from": str(appointment_id)})
        await self._write_audit(
            appointment_id,
            changed_by=changed_by,
            changed_by_role=changed_by_role,
            previous_status=old["status"],
            new_status="rescheduled",
            previous_date=old["appointment_date"],
            new_date=data["appointment_date"],
            previous_time=old["start_time"],
            new_time=data["start_time"],
            change_reason=data.get("change_reason"),
        )
        await emit_event(
            self.session,
            aggregate_type="appointment",
            aggregate_id=appointment_id,
            event_type="appointment_rescheduled",
            payload={
                "old_appointment_id": str(appointment_id),
                "new_appointment_id": str(new_appointment["appointment_id"]),
            },
        )
        return await self.get(new_appointment["appointment_id"])

    async def audit_log(self, appointment_id: UUID) -> builtins.list[dict]:
        return await self.audit.list_for_appointment(appointment_id)


class DeviceCapacityService:
    """The pool a device_session books against.

    A doctor's calendar is EXCLUSIVE — one visit at a time, enforced by a GIST
    exclusion constraint. A clinic's devices are CAPACITY — N at a time, and
    PostgreSQL has no constraint form meaning "at most N rows matching a
    predicate" (36 header). So this is the one rule in the appointment design
    enforced in application code, and the lock below is why it is still correct
    under concurrency.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ClinicDeviceScheduleRepository(session)
        self.appointments = AppointmentRepository(session)

    async def open_slots(
        self, clinic_device_id: UUID, from_date: dt.date, to_date: dt.date, *, only_available: bool = False
    ) -> builtins.list[dict]:
        if to_date < from_date:
            raise BusinessRuleError("to_date must not be before from_date", code="INVALID_DATE_RANGE")
        if (to_date - from_date).days > 60:
            raise BusinessRuleError("Date range too large (max 60 days)", code="AVAILABILITY_RANGE_TOO_LARGE")

        weekly = await self.repo.list_for_device(clinic_device_id)
        overrides = await self.repo.overrides_for_range(clinic_device_id, from_date, to_date)
        # One grouped query for the whole range rather than a count per slot.
        booked = await self.repo.booked_counts_for_range(clinic_device_id, from_date, to_date)

        out: builtins.list[dict] = []
        current = from_date
        while current <= to_date:
            out.extend(_build_device_day_slots(current, weekly, overrides.get(current), booked))
            current += dt.timedelta(days=1)
        if only_available:
            out = [s for s in out if s["is_available"]]
        return out

    async def reserve(self, clinic_device_id: UUID, on_date: dt.date, start_time: dt.time) -> int:
        """Verifies one more session fits in this slot on THIS device, and holds
        that answer until the caller's transaction commits.

        THE LOCK IS THE WHOLE CORRECTNESS ARGUMENT. Without FOR UPDATE, two
        patients claiming the same slot simultaneously both read 2-of-3, both
        pass this check, and the device is overbooked with no error raised
        anywhere. With it, the second blocks here until the first commits, then
        reads 3-of-3 and is correctly rejected.

        Must be called inside the same transaction as the write it guards.
        Returns the slot duration in minutes so the caller can derive end_time.
        """
        dow = on_date.isoweekday() % 7
        capacity = await self.repo.lock_capacity_for_day(clinic_device_id, dow)
        if capacity is None:
            raise ConflictError("This device runs no sessions on that day", code="DEVICE_SCHEDULE_CLOSED")

        override = (await self.repo.overrides_for_range(clinic_device_id, on_date, on_date)).get(on_date)
        if override:
            if not override["is_available"]:
                raise ConflictError("The device is closed for sessions on that date", code="DEVICE_SCHEDULE_CLOSED")
            if override["capacity"]:
                capacity = override["capacity"]

        booked = await self.repo.count_device_sessions_in_slot(clinic_device_id, on_date, start_time)
        if booked >= capacity:
            raise ConflictError(
                f"That device slot is full ({booked} of {capacity} taken)",
                code="DEVICE_SLOT_FULL",
            )

        weekly = await self.repo.list_for_device(clinic_device_id)
        rule = next((w for w in weekly if w["day_of_week"] == dow), None)
        return (rule and rule["slot_duration_minutes"]) or DEFAULT_SLOT_MINUTES


class PatientBookingService:
    """Everything a patient does to their own appointments.

    All four visit types converge here. The split that matters is not
    patient-created versus doctor-created, it is WHICH POOL the slot comes from:
    a device_session books clinic capacity, everything else books a doctor.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = AppointmentRepository(session)
        self.appointments = AppointmentService(session)
        self.availability = AvailabilityService(session)
        self.capacity = DeviceCapacityService(session)
        self.settings = get_settings()

    # ── helpers ─────────────────────────────────────────────────────────────

    def _initial_status_and_hold(self) -> tuple[str, dt.datetime | None]:
        return _initial_status_and_hold(self.settings)

    async def _own_patient_row(self, ctx: RequestContext) -> dict:
        return await _get_patient_row(self.session, profile_id=UUID(ctx.user_id))

    async def _require_allocated_doctor(self, patient: dict) -> UUID:
        if patient["registration_status"] != "registration_complete":
            raise BusinessRuleError(
                "Finish registration before booking an appointment",
                code="REGISTRATION_INCOMPLETE",
            )
        if not patient["primary_doctor_id"]:
            raise BusinessRuleError(
                "No doctor has been allocated to you yet",
                code="NO_DOCTOR_ALLOCATED",
            )
        return UUID(str(patient["primary_doctor_id"]))

    # ── availability ────────────────────────────────────────────────────────

    async def my_availability(self, ctx: RequestContext, from_date: dt.date, to_date: dt.date) -> builtins.list[dict]:
        """Open slots on the caller's own allocated doctor's calendar.

        The patient never sends a doctor id: allocation already happened at
        registration_complete, so there is nothing to choose and nothing to
        spoof.
        """
        patient = await self._own_patient_row(ctx)
        doctor_profile_id = await self._require_allocated_doctor(patient)
        return await self.availability._compute_for_profile(doctor_profile_id, from_date, to_date, include_unavailable=False)

    async def device_availability(
        self, appointment_id: UUID, ctx: RequestContext, from_date: dt.date, to_date: dt.date
    ) -> builtins.list[dict]:
        """Device slots at the clinic of a specific planned session."""
        appt = await self.appointments.get(appointment_id)
        assert_owns_profile(ctx, appt["patient_id"])
        if appt["appointment_type"] != TYPE_DEVICE_SESSION:
            raise BusinessRuleError(
                "Only a device session books against clinic device capacity",
                code="NOT_A_DEVICE_SESSION",
            )
        return await self.capacity.open_slots(appt["clinic_device_id"], from_date, to_date, only_available=True)

    # ── booking ─────────────────────────────────────────────────────────────

    async def book_initial(self, ctx: RequestContext, data: dict) -> dict:
        patient = await self._own_patient_row(ctx)
        doctor_profile_id = await self._require_allocated_doctor(patient)

        existing = await self.repo.find_active_initial(UUID(ctx.user_id))
        if existing:
            raise ConflictError(
                "You already have an initial appointment booked",
                code="INITIAL_APPOINTMENT_EXISTS",
            )
        return await self._book_on_doctor(ctx, patient, doctor_profile_id, TYPE_INITIAL, data)

    async def book_follow_up(self, ctx: RequestContext, data: dict) -> dict:
        patient = await self._own_patient_row(ctx)
        doctor_profile_id = await self._require_allocated_doctor(patient)

        # A follow-up needs something to follow up on. Protocol follow-ups are
        # generated by the doctor and are a different type entirely, so this
        # gate only ever applies to a patient booking one themselves.
        if not await self.repo.has_completed_initial(UUID(ctx.user_id)):
            raise BusinessRuleError(
                "Complete your initial appointment before booking a follow-up",
                code="NO_COMPLETED_INITIAL",
            )
        return await self._book_on_doctor(ctx, patient, doctor_profile_id, TYPE_FOLLOW_UP, data)

    async def _book_on_doctor(self, ctx: RequestContext, patient: dict, doctor_profile_id: UUID, appointment_type: str, data: dict) -> dict:
        clinic_id = patient["primary_clinic_id"]
        if not clinic_id:
            raise BusinessRuleError("You are not registered at a clinic", code="CLINIC_REQUIRED")

        is_available, duration = await self.availability.check_slot(doctor_profile_id, data["appointment_date"], data["start_time"])
        if not is_available:
            raise ConflictError("That slot is not available", code="APPOINTMENT_SLOT_UNAVAILABLE")

        end_time = (dt.datetime.combine(data["appointment_date"], data["start_time"]) + dt.timedelta(minutes=duration)).time()
        status, hold = self._initial_status_and_hold()

        payload = {
            "clinic_id": str(clinic_id),
            "patient_id": ctx.user_id,
            "doctor_id": str(doctor_profile_id),
            "appointment_date": data["appointment_date"],
            "start_time": data["start_time"],
            "end_time": end_time,
            "slot_duration_minutes": duration,
            "appointment_type": appointment_type,
            "status": status,
            "hold_expires_at": hold,
            "reason": data.get("reason"),
            "patient_complaint": data.get("patient_complaint"),
            "booked_by": ctx.user_id,
            "booked_by_role": "patient",
        }
        try:
            created = await self.repo.create(payload)
        except IntegrityError as exc:
            # Either excl_doctor_overlap (someone took the slot between the
            # check above and this insert) or uq_one_active_initial_per_patient
            # (two taps racing). Both mean the same thing to the patient.
            raise ConflictError("That slot was just taken — please pick another", code="APPOINTMENT_SLOT_TAKEN") from exc

        await emit_event(
            self.session,
            aggregate_type="appointment",
            aggregate_id=created["appointment_id"],
            event_type="appointment_booked",
            payload={
                "appointment_id": str(created["appointment_id"]),
                "appointment_type": appointment_type,
                "status": status,
            },
        )
        return await self.appointments.get(created["appointment_id"])

    async def claim_slot(self, appointment_id: UUID, ctx: RequestContext, data: dict) -> dict:
        """planned -> selected (or straight to paid while payment is bypassed).

        This is the one entry point for BOTH protocol-born types, and the only
        place the two pools meet. A protocol_followup claims a slot on the
        doctor's calendar; a device_session claims one of the clinic's device
        slots. Everything downstream is identical.
        """
        appt = await self.appointments.get(appointment_id)
        assert_owns_profile(ctx, appt["patient_id"])

        if appt["status"] != STATUS_PLANNED:
            raise BusinessRuleError(
                f"Only a planned session can be given a time (this one is '{appt['status']}')",
                code="NOT_PLANNED",
            )
        if appt["appointment_type"] not in PROTOCOL_BORN_TYPES:
            raise BusinessRuleError(
                "This appointment already has a time",
                code="NOT_CLAIMABLE",
            )

        start_time = data["start_time"]
        on_date = data.get("appointment_date") or appt["appointment_date"]

        if appt["appointment_type"] == TYPE_DEVICE_SESSION:
            # Counted check under a row lock. Held until this transaction
            # commits, which is what stops two patients taking the last slot.
            duration = await self.capacity.reserve(appt["clinic_device_id"], on_date, start_time)
        else:
            doctor_profile_id = UUID(str(appt["doctor_id"]))
            is_available, duration = await self.availability.check_slot(doctor_profile_id, on_date, start_time)
            if not is_available:
                raise ConflictError("That slot is not available", code="APPOINTMENT_SLOT_UNAVAILABLE")

        end_time = (dt.datetime.combine(on_date, start_time) + dt.timedelta(minutes=duration)).time()
        status, hold = self._initial_status_and_hold()

        if on_date != appt["appointment_date"]:
            # Claiming onto a different date is how an overdue planned session
            # is picked up — the ordinary transition, not a separate reschedule
            # path (31: an overdue 'planned' row simply stays planned).
            await self.repo.update_fields(appointment_id, {"appointment_date": on_date})

        try:
            await self.repo.claim_slot(appointment_id, start_time=start_time, end_time=end_time, hold_expires_at=hold, status=status)
        except IntegrityError as exc:
            raise ConflictError("That slot was just taken — please pick another", code="APPOINTMENT_SLOT_TAKEN") from exc

        await self.appointments._write_audit(
            appointment_id,
            changed_by=UUID(ctx.user_id),
            changed_by_role="patient",
            previous_status=STATUS_PLANNED,
            new_status=status,
            new_date=on_date,
            new_time=start_time,
        )
        await emit_event(
            self.session,
            aggregate_type="appointment",
            aggregate_id=appointment_id,
            event_type="appointment_slot_claimed",
            payload={"appointment_id": str(appointment_id), "status": status},
        )
        return await self.appointments.get(appointment_id)

    async def mark_paid(self, appointment_id: UUID, *, changed_by: UUID, changed_by_role: str = "system") -> dict:
        """selected -> paid. THE PAYMENT SEAM.

        Reachable today through the staff status endpoint so the transition is
        exercised and tested before any gateway exists. When Razorpay lands its
        webhook calls this identical method — no rework, and nothing else in the
        booking path has to change.

        Idempotent: the repository UPDATE matches only rows still 'selected', so
        a webhook delivered twice (which gateways do, by design) is a no-op the
        second time rather than a double transition.
        """
        appt = await self.appointments.get(appointment_id)
        updated = await self.repo.mark_paid(appointment_id, paid_by=changed_by, paid_by_role=changed_by_role)
        if updated is None:
            if appt["status"] == STATUS_PAID:
                return appt
            raise BusinessRuleError(
                f"Cannot mark an appointment '{appt['status']}' as paid",
                code="NOT_AWAITING_PAYMENT",
            )
        await self.appointments._write_audit(
            appointment_id,
            changed_by=changed_by,
            changed_by_role=changed_by_role,
            previous_status=STATUS_SELECTED,
            new_status=STATUS_PAID,
        )
        await emit_event(
            self.session,
            aggregate_type="appointment",
            aggregate_id=appointment_id,
            event_type="appointment_paid",
            payload={"appointment_id": str(appointment_id)},
        )
        return await self.appointments.get(appointment_id)

    # ── changing a booking ──────────────────────────────────────────────────

    async def reschedule_own(self, appointment_id: UUID, ctx: RequestContext, data: dict) -> dict:
        appt = await self.appointments.get(appointment_id)
        assert_owns_profile(ctx, appt["patient_id"])

        if appt["appointment_type"] in PROTOCOL_BORN_TYPES:
            # A protocol row is never "rescheduled" into a new row — releasing
            # the slot and claiming another keeps one folder per prescribed
            # session, which uq_appointments_protocol_session requires anyway.
            raise BusinessRuleError(
                "Release this session and claim a new slot instead",
                code="USE_CLAIM_SLOT_INSTEAD",
            )
        if appt["status"] not in (STATUS_SELECTED, STATUS_PAID):
            raise BusinessRuleError("Only a booked appointment can be moved", code="APPOINTMENT_NOT_ACTIVE")
        if appt["start_time"] and _hours_until(appt["appointment_date"], appt["start_time"]) < RESCHEDULE_MIN_HOURS:
            raise BusinessRuleError(
                f"Rescheduling requires at least {RESCHEDULE_MIN_HOURS} hours' notice",
                code="RESCHEDULE_WINDOW_PASSED",
            )
        return await self.appointments.reschedule(appointment_id, data, changed_by=UUID(ctx.user_id), changed_by_role="patient", ctx=ctx)

    async def cancel_own(self, appointment_id: UUID, ctx: RequestContext, *, reason: str) -> dict:
        return await self.appointments.update_status(
            appointment_id,
            status="cancelled",
            changed_by=UUID(ctx.user_id),
            changed_by_role="patient",
            ctx=ctx,
            cancellation_reason=reason,
        )

    async def my_appointments(self, ctx: RequestContext, *, include_past: bool = False) -> builtins.list[dict]:
        """Every appointment of every type, protocol-generated 'planned' rows
        included — those are exactly what the patient needs to see in order to
        claim a slot for them."""
        return await self.repo.list_for_patient(UUID(ctx.user_id), include_past=include_past)


class ClinicDeviceScheduleService:
    """Clinic-admin side: when each of the clinic's devices runs sessions, and
    how many at once. One pool per device (clinic_devices.clinic_device_id),
    not one blanket number for the whole clinic — see 41's file header."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ClinicDeviceScheduleRepository(session)
        self.devices = ClinicDeviceRepository(session)

    async def _own_device(self, clinic_id: UUID, clinic_device_id: UUID) -> dict:
        """Resolves the device and confirms it belongs to this clinic — the
        same cross-clinic guard the DB's composite FK enforces, checked early
        so a mismatched pair 404s instead of surfacing as a constraint error."""
        row = await self.devices.get(clinic_device_id)
        if not row or str(row["clinic_id"]) != str(clinic_id):
            raise NotFoundError("Device not found at this clinic", code="CLINIC_DEVICE_NOT_FOUND")
        return row

    async def overview(self, clinic_id: UUID, ctx: RequestContext) -> builtins.list[dict]:
        """Every device this clinic owns, each with its current week (empty if
        unset yet) — the admin landing screen."""
        await assert_clinic_scope(ctx, self.session, clinic_id)
        devices = await self.devices.list_for_clinic(clinic_id, active_only=True)
        weeks = await self.repo.list_for_devices([d["clinic_device_id"] for d in devices])
        return [{**d, "week": weeks.get(d["clinic_device_id"], [])} for d in devices]

    async def device_week(self, clinic_id: UUID, clinic_device_id: UUID, ctx: RequestContext) -> builtins.list[dict]:
        await assert_clinic_scope(ctx, self.session, clinic_id)
        await self._own_device(clinic_id, clinic_device_id)
        return await self.repo.list_for_device(clinic_device_id)

    async def replace_device_week(
        self, clinic_id: UUID, clinic_device_id: UUID, items: builtins.list[dict], ctx: RequestContext
    ) -> builtins.list[dict]:
        """Atomic replace of one device's whole week.

        Same shape as a doctor redrawing their own timetable: there is no
        natural per-day PATCH when the admin is editing the entire week in one
        form, and a partial write would leave a half-open schedule.
        """
        await assert_clinic_scope(ctx, self.session, clinic_id)
        await self._own_device(clinic_id, clinic_device_id)
        days = [i["day_of_week"] for i in items]
        if len(days) != len(set(days)):
            raise BusinessRuleError("Each weekday may appear only once", code="DUPLICATE_WEEKDAY")
        rows = await self.repo.replace_for_device(clinic_device_id, clinic_id, items, created_by=UUID(ctx.user_id))
        await emit_event(
            self.session,
            aggregate_type="clinic_device_schedule",
            aggregate_id=clinic_device_id,
            event_type="clinic_device_schedule_updated",
            payload={"clinic_id": str(clinic_id), "clinic_device_id": str(clinic_device_id), "day_count": len(rows)},
        )
        return rows

    async def device_overrides(
        self, clinic_id: UUID, clinic_device_id: UUID, ctx: RequestContext, *, from_date=None
    ) -> builtins.list[dict]:
        await assert_clinic_scope(ctx, self.session, clinic_id)
        await self._own_device(clinic_id, clinic_device_id)
        return await self.repo.list_overrides(clinic_device_id, from_date=from_date)

    async def create_device_override(self, clinic_id: UUID, clinic_device_id: UUID, data: dict, ctx: RequestContext) -> dict:
        await assert_clinic_scope(ctx, self.session, clinic_id)
        await self._own_device(clinic_id, clinic_device_id)
        return await self.repo.create_override(
            {**data, "clinic_device_id": str(clinic_device_id), "clinic_id": str(clinic_id), "created_by": ctx.user_id}
        )

    async def delete_device_override(self, clinic_id: UUID, clinic_device_id: UUID, override_id: UUID, ctx: RequestContext) -> None:
        await assert_clinic_scope(ctx, self.session, clinic_id)
        row = await self.repo.get_override(override_id)
        if not row or str(row["clinic_device_id"]) != str(clinic_device_id) or str(row["clinic_id"]) != str(clinic_id):
            raise NotFoundError("Override not found", code="OVERRIDE_NOT_FOUND")
        await self.repo.delete_override(override_id)

    async def capacity_board(self, clinic_id: UUID, ctx: RequestContext, from_date: dt.date, to_date: dt.date) -> builtins.list[dict]:
        """Every device's slots in the range, capacity/booked/remaining — the
        admin's view of how full the clinic is. One pool per device now, so
        this loops each device rather than issuing one clinic-wide query (41
        open item 1) — fine at today's per-clinic device counts."""
        await assert_clinic_scope(ctx, self.session, clinic_id)
        devices = await self.devices.list_for_clinic(clinic_id, active_only=True)
        capacity = DeviceCapacityService(self.session)
        out: builtins.list[dict] = []
        for d in devices:
            slots = await capacity.open_slots(d["clinic_device_id"], from_date, to_date)
            for slot in slots:
                out.append({**slot, "clinic_device_id": d["clinic_device_id"], "device_name": d.get("device_name")})
        return out


class ClinicDeviceService:
    """Clinic-admin inventory: which devices this clinic owns, and how many.

    Gates the protocol device picker. Nothing here decides how many sessions can
    run at once — that is clinic_device_schedules.capacity, which the admin sets
    separately because it also depends on assistants on shift.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ClinicDeviceRepository(session)

    async def list_for_clinic(self, clinic_id: UUID, ctx: RequestContext, *, active_only: bool = True) -> builtins.list[dict]:
        await assert_clinic_scope(ctx, self.session, clinic_id)
        return await self.repo.list_for_clinic(clinic_id, active_only=active_only)

    async def add(self, clinic_id: UUID, data: dict, ctx: RequestContext) -> dict:
        await assert_clinic_scope(ctx, self.session, clinic_id)
        existing = await self.repo.find(clinic_id, data["device_id"])
        if existing:
            # The unique constraint would catch this, but a readable message
            # beats a raw violation — and "already listed" usually means the
            # admin wanted to change the quantity.
            raise ConflictError(
                "This clinic already lists that device — update its quantity instead",
                code="CLINIC_DEVICE_EXISTS",
            )
        try:
            created = await self.repo.create(
                {
                    "clinic_id": str(clinic_id),
                    "device_id": str(data["device_id"]),
                    "quantity": data.get("quantity", 1),
                    "acquired_on": data.get("acquired_on"),
                    "notes": data.get("notes"),
                    "created_by": ctx.user_id,
                }
            )
        except IntegrityError as exc:
            raise NotFoundError("No such device in the catalogue", code="DEVICE_NOT_FOUND") from exc

        await emit_event(
            self.session,
            aggregate_type="clinic_device",
            aggregate_id=created["clinic_device_id"],
            event_type="clinic_device_added",
            payload={"clinic_id": str(clinic_id), "device_id": str(data["device_id"])},
        )
        return created

    async def update(self, clinic_id: UUID, clinic_device_id: UUID, data: dict, ctx: RequestContext) -> dict:
        await assert_clinic_scope(ctx, self.session, clinic_id)
        row = await self.repo.get(clinic_device_id)
        if not row or str(row["clinic_id"]) != str(clinic_id):
            raise NotFoundError("Device not listed at this clinic", code="CLINIC_DEVICE_NOT_FOUND")
        fields = {k: v for k, v in data.items() if v is not None}
        return await self.repo.update(clinic_device_id, fields) or row

    async def remove(self, clinic_id: UUID, clinic_device_id: UUID, ctx: RequestContext) -> None:
        """Deletes a listing, or refuses when history depends on it.

        A device that has actually been prescribed at this clinic must be
        deactivated, not deleted — removing the row would leave a historic
        protocol pointing at hardware the clinic has no record of ever owning.
        Correcting a mistaken entry is what delete is for.
        """
        await assert_clinic_scope(ctx, self.session, clinic_id)
        row = await self.repo.get(clinic_device_id)
        if not row or str(row["clinic_id"]) != str(clinic_id):
            raise NotFoundError("Device not listed at this clinic", code="CLINIC_DEVICE_NOT_FOUND")

        if await self.repo.is_used_by_a_protocol(clinic_id, row["device_id"]):
            raise ConflictError(
                "A protocol at this clinic has prescribed this device — deactivate it instead of deleting",
                code="CLINIC_DEVICE_IN_USE",
            )
        # is_used_by_a_protocol only catches protocol_plan.device_id
        # (the catalogue device). clinic_device_schedules, its overrides, and
        # appointments.clinic_device_id all FK to THIS row (clinic_device_id)
        # with ON DELETE RESTRICT — a device with a schedule configured but
        # never actually prescribed passes the check above and hits the DB
        # constraint instead. Same guidance either way: retire, don't delete.
        try:
            await self.repo.delete(clinic_device_id)
        except IntegrityError as exc:
            raise ConflictError(
                "This device has schedules or appointments on record — deactivate it instead of deleting",
                code="CLINIC_DEVICE_IN_USE",
            ) from exc
