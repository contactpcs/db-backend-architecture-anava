from __future__ import annotations

import datetime as dt
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.modules.scheduling.repository import (
    AppointmentAuditLogRepository,
    AppointmentRepository,
    AppointmentRequestRepository,
    ScheduleOverrideRepository,
    WeeklyScheduleRepository,
)


async def _resolve_doctor_profile_id(session: AsyncSession, doctor_id: UUID) -> UUID:
    from app.modules.staff.repository import DoctorRepository

    doctor = await DoctorRepository(session).get(doctor_id)
    if not doctor:
        raise NotFoundError("Doctor not found", code="DOCTOR_NOT_FOUND")
    return doctor["profile_id"]


async def _resolve_patient_profile_id(session: AsyncSession, patient_id: UUID) -> UUID:
    from app.modules.patients.repository import PatientRepository

    patient = await PatientRepository(session).get(patient_id)
    if not patient:
        raise NotFoundError("Patient not found", code="PATIENT_NOT_FOUND")
    return patient["profile_id"]


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


class ScheduleOverrideService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ScheduleOverrideRepository(session)

    async def create(self, doctor_id: UUID, data: dict, *, created_by: UUID) -> dict:
        doctor_profile_id = await _resolve_doctor_profile_id(self.session, doctor_id)
        payload = {**data, "doctor_id": str(doctor_profile_id), "clinic_id": str(data["clinic_id"]), "created_by": str(created_by)}
        try:
            return await self.repo.create(payload)
        except IntegrityError as exc:
            raise ConflictError("An override already exists for this doctor/date", code="OVERRIDE_ALREADY_EXISTS") from exc

    async def list_for_doctor(self, doctor_id: UUID) -> list[dict]:
        doctor_profile_id = await _resolve_doctor_profile_id(self.session, doctor_id)
        return await self.repo.list_for_doctor(doctor_profile_id)


class AvailabilityService:
    """Merges weekly recurring schedule + date overrides - existing
    appointments to produce open slots. This is the Redis-cache candidate
    flagged in Architecture Section 19 — not cached yet (Stage 8), correctness
    first."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.weekly = WeeklyScheduleRepository(session)
        self.overrides = ScheduleOverrideRepository(session)
        self.appointments = AppointmentRepository(session)

    async def compute(self, doctor_id: UUID, on_date: dt.date) -> list[dict]:
        doctor_profile_id = await _resolve_doctor_profile_id(self.session, doctor_id)

        override = await self.overrides.for_date(doctor_profile_id, on_date)
        if override and not override["is_available"]:
            return []  # doctor explicitly blocked this date (leave/holiday)

        if override and override["is_available"]:
            start, end, slot_minutes = override["start_time"], override["end_time"], override["slot_duration_minutes"] or 30
        else:
            weekly = await self.weekly.list_for_doctor(doctor_profile_id)
            rule = next((w for w in weekly if w["day_of_week"] == on_date.weekday() % 7 or w["day_of_week"] == (on_date.isoweekday() % 7)), None)
            if not rule:
                return []
            start, end, slot_minutes = rule["start_time"], rule["end_time"], rule["slot_duration_minutes"]

        booked = await self.appointments.list_for_doctor_on_date(doctor_profile_id, on_date)
        booked_ranges = {(b["start_time"], b["end_time"]) for b in booked}

        slots = []
        current = dt.datetime.combine(on_date, start)
        end_dt = dt.datetime.combine(on_date, end)
        step = dt.timedelta(minutes=slot_minutes)
        while current + step <= end_dt:
            slot_start, slot_end = current.time(), (current + step).time()
            if (slot_start, slot_end) not in booked_ranges:
                slots.append({"start_time": slot_start.isoformat(), "end_time": slot_end.isoformat()})
            current += step
        return slots


class AppointmentRequestService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = AppointmentRequestRepository(session)
        self.appointments = AppointmentService(session)

    async def create(self, data: dict, *, submitted_by: UUID) -> dict:
        patient_profile_id = await _resolve_patient_profile_id(self.session, data["patient_id"])
        doctor_profile_id = await _resolve_doctor_profile_id(self.session, data["doctor_id"]) if data.get("doctor_id") else None
        payload = {
            "clinic_id": str(data["clinic_id"]), "patient_id": str(patient_profile_id),
            "doctor_id": str(doctor_profile_id) if doctor_profile_id else None,
            "cycle_id": str(data["cycle_id"]) if data.get("cycle_id") else None,
            "request_type": data.get("request_type", "new"), "preferred_date_1": data["preferred_date_1"],
            "preferred_date_2": data.get("preferred_date_2"), "preferred_date_3": data.get("preferred_date_3"),
            "preferred_time_window": data.get("preferred_time_window", "any"),
            "patient_complaint": data.get("patient_complaint"), "urgency": data.get("urgency", "normal"),
            "submitted_by": str(submitted_by),
        }
        req = await self.repo.create(payload)
        await emit_event(
            self.session, aggregate_type="appointment_request", aggregate_id=req["request_id"],
            event_type="appointment_request_submitted", payload={"request_id": str(req["request_id"])},
        )
        return req

    async def get(self, request_id: UUID) -> dict:
        req = await self.repo.get(request_id)
        if not req:
            raise NotFoundError("Appointment request not found", code="APPOINTMENT_REQUEST_NOT_FOUND")
        return req

    async def list(self, **filters) -> list[dict]:
        return await self.repo.list(**filters)

    async def decide(self, request_id: UUID, data: dict, *, reviewed_by: UUID) -> dict:
        req = await self.get(request_id)
        if req["status"] != "pending":
            raise BusinessRuleError(f"Appointment request already {req['status']}", code="APPOINTMENT_REQUEST_ALREADY_DECIDED")

        approved_appointment_id = None
        if data["decision"] == "approved":
            if not all([data.get("appointment_date"), data.get("start_time"), data.get("end_time")]):
                raise BusinessRuleError("appointment_date/start_time/end_time required to approve", code="APPOINTMENT_SLOT_REQUIRED")
            appointment = await self.appointments.create(
                {
                    "clinic_id": req["clinic_id"], "patient_id": None, "doctor_id": data.get("doctor_id") or req["doctor_id"],
                    "appointment_date": data["appointment_date"], "start_time": data["start_time"], "end_time": data["end_time"],
                    "appointment_type": data.get("appointment_type", "initial_assessment"),
                },
                booked_by=reviewed_by, booked_by_role="staff", _patient_profile_id_override=req["patient_id"],
                _doctor_profile_id_override=data.get("doctor_id") is None,
                appointment_request_id=request_id,
            )
            approved_appointment_id = appointment["appointment_id"]

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

    async def create(self, data: dict, *, booked_by: UUID, booked_by_role: str,
                      _patient_profile_id_override=None, _doctor_profile_id_override=False,
                      appointment_request_id: UUID | None = None) -> dict:
        patient_profile_id = _patient_profile_id_override or await _resolve_patient_profile_id(self.session, data["patient_id"])
        doctor_profile_id = await _resolve_doctor_profile_id(self.session, data["doctor_id"]) if data.get("doctor_id") and not _doctor_profile_id_override else data.get("doctor_id")

        payload = {
            "clinic_id": str(data["clinic_id"]), "patient_id": str(patient_profile_id),
            "doctor_id": str(doctor_profile_id), "ca_id": str(data["ca_id"]) if data.get("ca_id") else None,
            "cycle_id": str(data["cycle_id"]) if data.get("cycle_id") else None,
            "appointment_request_id": str(appointment_request_id) if appointment_request_id else None,
            "appointment_date": data["appointment_date"], "start_time": data["start_time"], "end_time": data["end_time"],
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
        return appointment

    async def get(self, appointment_id: UUID) -> dict:
        appt = await self.repo.get(appointment_id)
        if not appt:
            raise NotFoundError("Appointment not found", code="APPOINTMENT_NOT_FOUND")
        return appt

    async def list(self, **filters) -> list[dict]:
        return await self.repo.list(**filters)

    async def _write_audit(self, appointment_id: UUID, *, changed_by: UUID, changed_by_role: str,
                            previous_status, new_status, previous_date=None, new_date=None,
                            previous_time=None, new_time=None, change_reason=None) -> None:
        await self.audit.create({
            "appointment_id": str(appointment_id), "changed_by": str(changed_by), "changed_by_role": changed_by_role,
            "previous_status": previous_status, "new_status": new_status, "previous_date": previous_date,
            "new_date": new_date, "previous_time": previous_time, "new_time": new_time, "change_reason": change_reason,
        })

    async def update_status(self, appointment_id: UUID, *, status: str, changed_by: UUID, changed_by_role: str, cancellation_reason=None) -> dict:
        appt = await self.get(appointment_id)
        updated = await self.repo.update_status(appointment_id, status=status, cancelled_by=changed_by if status == "cancelled" else None, cancellation_reason=cancellation_reason)
        await self._write_audit(appointment_id, changed_by=changed_by, changed_by_role=changed_by_role, previous_status=appt["status"], new_status=status, change_reason=cancellation_reason)
        await emit_event(
            self.session, aggregate_type="appointment", aggregate_id=appointment_id,
            event_type="appointment_cancelled" if status == "cancelled" else "appointment_status_changed",
            payload={"appointment_id": str(appointment_id), "status": status},
        )
        return updated  # type: ignore[return-value]

    async def reschedule(self, appointment_id: UUID, data: dict, *, changed_by: UUID, changed_by_role: str) -> dict:
        old = await self.get(appointment_id)
        new_appointment = await self.create(
            {
                "clinic_id": old["clinic_id"], "patient_id": old["patient_id"], "doctor_id": old["doctor_id"],
                "ca_id": old["ca_id"], "cycle_id": old["cycle_id"], "appointment_date": data["appointment_date"],
                "start_time": data["start_time"], "end_time": data["end_time"], "appointment_type": old["appointment_type"],
            },
            booked_by=changed_by, booked_by_role=changed_by_role,
            _patient_profile_id_override=old["patient_id"], _doctor_profile_id_override=True,
        )
        await self.repo.reschedule(appointment_id, new_appointment_id=new_appointment["appointment_id"])
        await self._write_audit(
            appointment_id, changed_by=changed_by, changed_by_role=changed_by_role, previous_status=old["status"],
            new_status="rescheduled", previous_date=old["appointment_date"], new_date=data["appointment_date"],
            previous_time=old["start_time"], new_time=data["start_time"], change_reason=data.get("change_reason"),
        )
        await emit_event(
            self.session, aggregate_type="appointment", aggregate_id=appointment_id,
            event_type="appointment_rescheduled", payload={"old_appointment_id": str(appointment_id), "new_appointment_id": str(new_appointment["appointment_id"])},
        )
        return new_appointment

    async def audit_log(self, appointment_id: UUID) -> list[dict]:
        return await self.audit.list_for_appointment(appointment_id)
