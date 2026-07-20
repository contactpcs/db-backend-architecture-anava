from __future__ import annotations

import datetime as dt
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import RequestContext
from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, PermissionError_
from app.core.resolve import resolve_doctor_profile_id as _resolve_doctor_profile_id
from app.core.resolve import resolve_patient_profile_id as _resolve_patient_profile_id
from app.core.scoping import assert_clinic_scope, assert_owns_profile
from app.modules.scheduling.repository import (
    AppointmentAuditLogRepository,
    AppointmentRepository,
    AppointmentRequestRepository,
    ScheduleOverrideRepository,
    WeeklyScheduleRepository,
)

# Same shape as v1's appointment_service constants — the whole point of this
# port is replicating that rulebook against v2's richer schema, not
# inventing a new one.
ACTIVE_STATUSES = {"scheduled", "confirmed", "checked_in", "in_progress"}
CANCEL_MIN_HOURS = 2
RESCHEDULE_REQUEST_MIN_HOURS = 24
REQUEST_EXPIRY_HOURS = 72
DEFAULT_SLOT_MINUTES = 30

# Which statuses a transition is legal FROM (v1's AppointmentDetailModal
# `can` map, moved server-side where an authorization rule actually belongs).
_ALLOWED_FROM = {
    "confirmed": {"scheduled"},
    "checked_in": {"scheduled", "confirmed"},
    "in_progress": {"checked_in"},
    "completed": {"in_progress"},
    "no_show": {"scheduled", "confirmed", "checked_in"},
    "cancelled": ACTIVE_STATUSES,
}
_DOCTOR_ONLY_STATUSES = {"in_progress", "completed"}


async def _get_patient_row(session: AsyncSession, *, profile_id: UUID) -> dict:
    """Full patients row by profile_id — used to auto-resolve a request's
    clinic_id/doctor_id from the patient's own record (v1's request_service
    does the same: 'resolves assigned_doctor_id + clinic from the patient
    row'), and to check patient-self-ownership."""
    row = (
        await session.execute(
            text("SELECT * FROM patients WHERE profile_id = :pid"), {"pid": str(profile_id)}
        )
    ).mappings().first()
    if not row:
        raise NotFoundError("Patient record not found", code="PATIENT_NOT_FOUND")
    return dict(row)


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
                w for w in weekly_rows
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

    slots = []
    current = dt.datetime.combine(on_date, start)
    end_dt = dt.datetime.combine(on_date, end)
    step = dt.timedelta(minutes=slot_minutes)
    while current + step <= end_dt:
        slot_start, slot_end = current.time(), (current + step).time()
        if break_start and break_end and slot_start < break_end and slot_end > break_start:
            current += step
            continue
        is_available = (slot_start, slot_end) not in booked_ranges
        slots.append({"date": on_date, "start_time": slot_start, "end_time": slot_end, "is_available": is_available})
        current += step
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

    async def _compute_for_profile(self, doctor_profile_id: UUID, from_date: dt.date, to_date: dt.date, *, include_unavailable: bool = True) -> list[dict]:
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


class AppointmentRequestService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = AppointmentRequestRepository(session)
        self.appointments = AppointmentService(session)

    async def create(self, data: dict, *, submitted_by: UUID, ctx: RequestContext,
                      _patient_profile_id_override=None, _doctor_profile_id_override: bool = False) -> dict:
        patient_profile_id = _patient_profile_id_override or await _resolve_patient_profile_id(self.session, data["patient_id"])
        if ctx.role == "patient":
            assert_owns_profile(ctx, patient_profile_id)

        patient_row = await _get_patient_row(self.session, profile_id=patient_profile_id)
        clinic_id = data.get("clinic_id") or patient_row.get("primary_clinic_id")
        if not clinic_id:
            raise BusinessRuleError("Patient has no primary clinic on file — clinic_id is required", code="CLINIC_REQUIRED")

        doctor_profile_id = None
        if data.get("doctor_id"):
            doctor_profile_id = (
                data["doctor_id"] if _doctor_profile_id_override
                else await _resolve_doctor_profile_id(self.session, data["doctor_id"])
            )
        elif data.get("request_type", "new") == "new":
            # v1: "resolves assigned_doctor_id from the patient row (errors
            # if no assigned doctor)" — a first-visit request has to land on
            # someone's calendar.
            doctor_profile_id = patient_row.get("primary_doctor_id")
            if not doctor_profile_id:
                raise BusinessRuleError("Patient has no assigned doctor — doctor_id is required", code="DOCTOR_REQUIRED")

        request_type = data.get("request_type", "new")
        existing = await self.repo.find_pending(
            patient_id=patient_profile_id, request_type=request_type,
            parent_appointment_id=data.get("parent_appointment_id"),
        )
        if existing:
            raise ConflictError("You already have a pending request of this type", code="APPOINTMENT_REQUEST_ALREADY_PENDING")

        payload = {
            "clinic_id": str(clinic_id), "patient_id": str(patient_profile_id),
            "doctor_id": str(doctor_profile_id) if doctor_profile_id else None,
            "cycle_id": str(data["cycle_id"]) if data.get("cycle_id") else None,
            "request_type": request_type, "parent_appointment_id": str(data["parent_appointment_id"]) if data.get("parent_appointment_id") else None,
            "preferred_date_1": data["preferred_date_1"],
            "preferred_date_2": data.get("preferred_date_2"), "preferred_date_3": data.get("preferred_date_3"),
            "preferred_time_window": data.get("preferred_time_window", "any"),
            "patient_complaint": data.get("patient_complaint"), "reason": data.get("reason"),
            "urgency": data.get("urgency", "normal"), "submitted_by": str(submitted_by),
            "expires_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=REQUEST_EXPIRY_HOURS),
        }
        req = await self.repo.create(payload)
        await emit_event(
            self.session, aggregate_type="appointment_request", aggregate_id=req["request_id"],
            event_type="appointment_request_submitted", payload={"request_id": str(req["request_id"])},
        )
        return req

    async def create_reschedule_request(self, appointment_id: UUID, data: dict, *, submitted_by: UUID, ctx: RequestContext) -> dict:
        """POST /appointments/{id}/request-reschedule — the patient-facing
        counterpart to staff's direct AppointmentService.reschedule. v1:
        patients can never reschedule directly, only ask; appointment must
        still be scheduled/confirmed and at least RESCHEDULE_REQUEST_MIN_HOURS
        out."""
        appt = await self.appointments.get(appointment_id)
        assert_owns_profile(ctx, appt["patient_id"])
        if appt["status"] not in ("scheduled", "confirmed"):
            raise BusinessRuleError("Only a scheduled or confirmed appointment can be rescheduled", code="APPOINTMENT_NOT_ACTIVE")
        if _hours_until(appt["appointment_date"], appt["start_time"]) < RESCHEDULE_REQUEST_MIN_HOURS:
            raise BusinessRuleError(
                f"Reschedule requests must be made at least {RESCHEDULE_REQUEST_MIN_HOURS} hours in advance", code="RESCHEDULE_WINDOW_PASSED"
            )
        return await self.create(
            {
                "clinic_id": appt["clinic_id"], "doctor_id": appt["doctor_id"],
                "request_type": "reschedule", "parent_appointment_id": appointment_id,
                "preferred_date_1": data["preferred_date_1"], "preferred_date_2": data.get("preferred_date_2"),
                "preferred_date_3": data.get("preferred_date_3"), "preferred_time_window": data.get("preferred_time_window", "any"),
                "reason": data.get("reason"),
            },
            submitted_by=submitted_by, ctx=ctx,
            # appt["patient_id"]/["doctor_id"] are already profiles.id (the
            # appointments row stores them directly) — passing them through
            # create()'s normal resolution path (which expects
            # patients.patient_id / doctors.doctor_id row ids) would 404.
            _patient_profile_id_override=appt["patient_id"], _doctor_profile_id_override=True,
        )

    async def get(self, request_id: UUID) -> dict:
        req = await self.repo.get(request_id)
        if not req:
            raise NotFoundError("Appointment request not found", code="APPOINTMENT_REQUEST_NOT_FOUND")
        return req

    async def list(self, *, ctx: RequestContext, clinic_id=None, doctor_id=None, status=None) -> list[dict]:
        if ctx.role == "patient":
            return await self.repo.list(patient_id=UUID(ctx.user_id), status=status)
        if ctx.role == "doctor":
            return await self.repo.list(doctor_id=UUID(ctx.user_id), status=status)
        region_id = None
        if ctx.role in ("clinic_admin", "receptionist", "clinical_assistant") and not clinic_id:
            clinic_id = UUID(ctx.clinic_id) if ctx.clinic_id else None
        elif ctx.role == "regional_admin" and not clinic_id:
            region_id = UUID(ctx.region_id) if ctx.region_id else None
        return await self.repo.list(clinic_id=clinic_id, region_id=region_id, doctor_id=doctor_id, status=status)

    async def cancel_own(self, request_id: UUID, *, ctx: RequestContext) -> dict:
        """Patient withdrawing their own pending request — v1's cancel_request."""
        req = await self.get(request_id)
        assert_owns_profile(ctx, req["patient_id"])
        if req["status"] != "pending":
            raise BusinessRuleError(f"Request already {req['status']}", code="APPOINTMENT_REQUEST_ALREADY_DECIDED")
        updated = await self.repo.set_decision(request_id, status="cancelled_by_patient", reviewed_by=UUID(ctx.user_id), review_notes=None)
        await emit_event(
            self.session, aggregate_type="appointment_request", aggregate_id=request_id,
            event_type="appointment_request_cancelled", payload={"request_id": str(request_id)},
        )
        return updated  # type: ignore[return-value]

    async def decide(self, request_id: UUID, data: dict, *, reviewed_by: UUID, ctx: RequestContext) -> dict:
        # A patient hitting this same endpoint can only ever withdraw their
        # own pending request — everything else (approve/reject, deciding
        # on someone else's request) is a staff-only action gated below.
        if ctx.role == "patient":
            if data["decision"] != "cancelled_by_patient":
                raise PermissionError_("Patients can only withdraw their own request", code="PATIENT_ACTION_NOT_ALLOWED")
            return await self.cancel_own(request_id, ctx=ctx)

        req = await self.get(request_id)
        if req["status"] != "pending":
            raise BusinessRuleError(f"Appointment request already {req['status']}", code="APPOINTMENT_REQUEST_ALREADY_DECIDED")
        await assert_clinic_scope(ctx, self.session, req["clinic_id"])

        if data["decision"] == "approved":
            if not all([data.get("appointment_date"), data.get("start_time")]):
                raise BusinessRuleError("appointment_date/start_time required to approve", code="APPOINTMENT_SLOT_REQUIRED")
            doctor_id_override = data.get("doctor_id")
            create_data = {
                "clinic_id": req["clinic_id"], "doctor_id": doctor_id_override or req["doctor_id"],
                "appointment_date": data["appointment_date"], "start_time": data["start_time"], "end_time": data.get("end_time"),
                "appointment_type": data.get("appointment_type", "doctor_consultation"),
                "reason": req.get("reason"), "patient_complaint": req.get("patient_complaint"),
            }
            if req["request_type"] == "reschedule" and req["parent_appointment_id"]:
                new_appointment = await self.appointments.reschedule(
                    req["parent_appointment_id"], create_data, changed_by=reviewed_by, changed_by_role=ctx.role, ctx=ctx,
                    appointment_request_id=request_id,
                )
            else:
                new_appointment = await self.appointments.create(
                    {**create_data, "patient_id": None}, booked_by=reviewed_by, booked_by_role=ctx.role,
                    _patient_profile_id_override=req["patient_id"], _doctor_profile_id_override=doctor_id_override is None,
                    appointment_request_id=request_id, ctx=ctx,
                )
            approved_appointment_id = new_appointment["appointment_id"]
        else:
            approved_appointment_id = None

        updated = await self.repo.set_decision(
            request_id, status=data["decision"], reviewed_by=reviewed_by, review_notes=data.get("review_notes"),
            approved_appointment_id=approved_appointment_id,
        )
        await emit_event(
            self.session, aggregate_type="appointment_request", aggregate_id=request_id,
            event_type="appointment_request_decided", payload={"request_id": str(request_id), "decision": data["decision"]},
        )
        return updated  # type: ignore[return-value]


class AppointmentService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = AppointmentRepository(session)
        self.audit = AppointmentAuditLogRepository(session)

    async def create(self, data: dict, *, booked_by: UUID, booked_by_role: str, ctx: RequestContext | None = None,
                      _patient_profile_id_override=None, _doctor_profile_id_override=False,
                      appointment_request_id: UUID | None = None) -> dict:
        patient_profile_id = _patient_profile_id_override or await _resolve_patient_profile_id(self.session, data["patient_id"])
        doctor_profile_id = await _resolve_doctor_profile_id(self.session, data["doctor_id"]) if data.get("doctor_id") and not _doctor_profile_id_override else data.get("doctor_id")
        if not doctor_profile_id:
            raise NotFoundError("Doctor not found", code="DOCTOR_NOT_FOUND")
        doctor_profile_id = UUID(str(doctor_profile_id))

        if ctx is not None:
            await assert_clinic_scope(ctx, self.session, data["clinic_id"])

        is_available, duration = await AvailabilityService(self.session).check_slot(doctor_profile_id, data["appointment_date"], data["start_time"])
        if not is_available:
            raise ConflictError("That slot is not available on the doctor's schedule", code="APPOINTMENT_SLOT_UNAVAILABLE")
        end_time = data.get("end_time") or (dt.datetime.combine(data["appointment_date"], data["start_time"]) + dt.timedelta(minutes=duration)).time()

        payload = {
            "clinic_id": str(data["clinic_id"]), "patient_id": str(patient_profile_id),
            "doctor_id": str(doctor_profile_id), "ca_id": str(data["ca_id"]) if data.get("ca_id") else None,
            "cycle_id": str(data["cycle_id"]) if data.get("cycle_id") else None,
            "appointment_request_id": str(appointment_request_id) if appointment_request_id else None,
            "appointment_date": data["appointment_date"], "start_time": data["start_time"], "end_time": end_time,
            "slot_duration_minutes": duration,
            "appointment_type": data.get("appointment_type", "initial_assessment"), "session_phase": data.get("session_phase"),
            "reason": data.get("reason"), "patient_complaint": data.get("patient_complaint"),
            "booked_by": str(booked_by), "booked_by_role": booked_by_role,
        }
        try:
            appointment = await self.repo.create(payload)
        except IntegrityError as exc:
            raise ConflictError(
                "This doctor already has an appointment overlapping this time slot", code="APPOINTMENT_OVERLAP"
            ) from exc
        await emit_event(
            self.session, aggregate_type="appointment", aggregate_id=appointment["appointment_id"],
            event_type="appointment_booked", payload={"appointment_id": str(appointment["appointment_id"]), "doctor_id": str(doctor_profile_id)},
        )
        return await self.get(appointment["appointment_id"])

    async def get(self, appointment_id: UUID) -> dict:
        appt = await self.repo.get(appointment_id)
        if not appt:
            raise NotFoundError("Appointment not found", code="APPOINTMENT_NOT_FOUND")
        return appt

    async def list(self, *, ctx: RequestContext, clinic_id=None, doctor_id=None, patient_id=None,
                    status=None, date_from=None, date_to=None, skip: int = 0, limit: int = 100) -> list[dict]:
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
            clinic_id=clinic_id, region_id=region_id, doctor_id=doctor_id, patient_id=patient_id, status=status,
            date_from=date_from, date_to=date_to, skip=skip, limit=limit,
        )

    async def list_upcoming(self, *, ctx: RequestContext, days: int = 14) -> list[dict]:
        today = dt.date.today()
        rows = await self.list(ctx=ctx, date_from=today, date_to=today + dt.timedelta(days=days), limit=200)
        return [r for r in rows if r["status"] in ACTIVE_STATUSES]

    async def list_today(self, *, ctx: RequestContext) -> list[dict]:
        today = dt.date.today()
        return await self.list(ctx=ctx, date_from=today, date_to=today, limit=200)

    async def _write_audit(self, appointment_id: UUID, *, changed_by: UUID, changed_by_role: str,
                            previous_status, new_status, previous_date=None, new_date=None,
                            previous_time=None, new_time=None, change_reason=None) -> None:
        await self.audit.create({
            "appointment_id": str(appointment_id), "changed_by": str(changed_by), "changed_by_role": changed_by_role,
            "previous_status": previous_status, "new_status": new_status, "previous_date": previous_date,
            "new_date": new_date, "previous_time": previous_time, "new_time": new_time, "change_reason": change_reason,
        })

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
            if _hours_until(appt["appointment_date"], appt["start_time"]) < CANCEL_MIN_HOURS:
                raise BusinessRuleError(f"Cancellations require at least {CANCEL_MIN_HOURS} hours' notice", code="CANCEL_WINDOW_PASSED")
            return

        if status in _DOCTOR_ONLY_STATUSES:
            if ctx.role == "doctor" and str(appt["doctor_id"]) != ctx.user_id:
                raise PermissionError_("You can only update your own appointments", code="NOT_YOUR_APPOINTMENT")
            if ctx.role not in ("doctor", "super_admin"):
                raise PermissionError_("Only the treating doctor can perform this action", code="DOCTOR_ONLY_ACTION")
            return

        if ctx.role == "doctor" and str(appt["doctor_id"]) != ctx.user_id:
            raise PermissionError_("You can only update your own appointments", code="NOT_YOUR_APPOINTMENT")
        # clinic_admin/receptionist/clinical_assistant/regional_admin/super_admin: clinic-scoped

    async def update_status(self, appointment_id: UUID, *, status: str, changed_by: UUID, changed_by_role: str,
                             ctx: RequestContext, cancellation_reason=None) -> dict:
        appt = await self.get(appointment_id)
        if ctx.role != "patient":
            await assert_clinic_scope(ctx, self.session, appt["clinic_id"])
        if status == "cancelled" and not cancellation_reason:
            raise BusinessRuleError("A cancellation reason is required", code="CANCELLATION_REASON_REQUIRED")
        self._authorize_transition(appt, status=status, ctx=ctx)

        updated = await self.repo.update_status(appointment_id, status=status, cancelled_by=changed_by if status == "cancelled" else None, cancellation_reason=cancellation_reason)
        await self._write_audit(appointment_id, changed_by=changed_by, changed_by_role=changed_by_role, previous_status=appt["status"], new_status=status, change_reason=cancellation_reason)
        await emit_event(
            self.session, aggregate_type="appointment", aggregate_id=appointment_id,
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

    async def reschedule(self, appointment_id: UUID, data: dict, *, changed_by: UUID, changed_by_role: str,
                          ctx: RequestContext, appointment_request_id: UUID | None = None) -> dict:
        old = await self.get(appointment_id)
        if ctx.role == "patient":
            raise PermissionError_("Patients cannot reschedule directly — submit a reschedule request instead", code="PATIENT_ACTION_NOT_ALLOWED")
        if old["status"] not in ACTIVE_STATUSES:
            raise BusinessRuleError("Only an active appointment can be rescheduled", code="APPOINTMENT_NOT_ACTIVE")
        await assert_clinic_scope(ctx, self.session, old["clinic_id"])

        new_appointment = await self.create(
            {
                "clinic_id": old["clinic_id"], "patient_id": old["patient_id"], "doctor_id": old["doctor_id"],
                "ca_id": old["ca_id"], "cycle_id": old["cycle_id"], "appointment_date": data["appointment_date"],
                "start_time": data["start_time"], "end_time": data.get("end_time"), "appointment_type": old["appointment_type"],
                "reason": old.get("reason"), "patient_complaint": old.get("patient_complaint"),
            },
            booked_by=changed_by, booked_by_role=changed_by_role,
            _patient_profile_id_override=old["patient_id"], _doctor_profile_id_override=True,
            appointment_request_id=appointment_request_id,
        )
        await self.repo.reschedule(appointment_id, new_appointment_id=new_appointment["appointment_id"])
        await self.repo.update_fields(new_appointment["appointment_id"], {"rescheduled_from": str(appointment_id)})
        await self._write_audit(
            appointment_id, changed_by=changed_by, changed_by_role=changed_by_role, previous_status=old["status"],
            new_status="rescheduled", previous_date=old["appointment_date"], new_date=data["appointment_date"],
            previous_time=old["start_time"], new_time=data["start_time"], change_reason=data.get("change_reason"),
        )
        await emit_event(
            self.session, aggregate_type="appointment", aggregate_id=appointment_id,
            event_type="appointment_rescheduled", payload={"old_appointment_id": str(appointment_id), "new_appointment_id": str(new_appointment["appointment_id"])},
        )
        return await self.get(new_appointment["appointment_id"])

    async def audit_log(self, appointment_id: UUID) -> list[dict]:
        return await self.audit.list_for_appointment(appointment_id)
