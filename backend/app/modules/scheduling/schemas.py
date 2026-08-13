from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, Field


class WeeklyScheduleCreate(BaseModel):
    clinic_id: UUID
    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    slot_duration_minutes: int = 30
    break_start: time | None = None
    break_end: time | None = None
    max_appointments: int | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    is_active: bool = True


class WeeklyScheduleRead(BaseModel):
    schedule_id: UUID
    doctor_id: UUID
    clinic_id: UUID
    day_of_week: int
    start_time: time
    end_time: time
    slot_duration_minutes: int
    break_start: time | None = None
    break_end: time | None = None
    is_active: bool


class MyWeeklyScheduleItem(BaseModel):
    """Same shape as WeeklyScheduleCreate minus clinic_id — resolved from the
    caller doctor's own denormalized doctors.clinic_id, not chosen by the
    caller (same reasoning as MyScheduleOverrideCreate)."""

    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    slot_duration_minutes: int = 30
    break_start: time | None = None
    break_end: time | None = None
    max_appointments: int | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    is_active: bool = True


class MyWeeklyScheduleReplace(BaseModel):
    """Atomic replace of the caller doctor's own weekly template — delete
    every existing rule, insert this set. Matches v1's upsert_weekly_schedule
    (delete-then-insert), since there's no natural per-day PATCH when a
    doctor is redrawing their whole week at once in one form submit."""

    items: list[MyWeeklyScheduleItem]


class ScheduleOverrideCreate(BaseModel):
    clinic_id: UUID
    override_date: date
    is_available: bool = False
    start_time: time | None = None
    end_time: time | None = None
    slot_duration_minutes: int | None = None
    reason: str | None = None


class MyScheduleOverrideCreate(BaseModel):
    """Same shape minus clinic_id — resolved from the caller doctor's own
    denormalized doctors.clinic_id, not chosen by the caller."""

    override_date: date
    is_available: bool = False
    start_time: time | None = None
    end_time: time | None = None
    slot_duration_minutes: int | None = None
    reason: str | None = None


class ScheduleOverrideRead(BaseModel):
    override_id: UUID
    doctor_id: UUID
    clinic_id: UUID
    override_date: date
    is_available: bool
    start_time: time | None = None
    end_time: time | None = None
    reason: str | None


class AvailabilitySlotRead(BaseModel):
    date: date
    start_time: time
    end_time: time
    is_available: bool


class AppointmentCreate(BaseModel):
    clinic_id: UUID
    patient_id: UUID
    doctor_id: UUID
    ca_id: UUID | None = None
    cycle_id: UUID | None = None
    appointment_date: date
    start_time: time
    end_time: time | None = None
    appointment_type: str = Field(
        default="initial_assessment",
        pattern="^(initial_assessment|doctor_consultation|ca_session|treatment_session|follow_up|demo_visit|teleconsult)$",
    )
    reason: str | None = None
    patient_complaint: str | None = None


class AppointmentUpdate(BaseModel):
    notes: str | None = None
    patient_complaint: str | None = None
    appointment_type: str | None = Field(
        default=None,
        pattern="^(initial_assessment|doctor_consultation|ca_session|treatment_session|follow_up|demo_visit|teleconsult)$",
    )


class AppointmentReschedule(BaseModel):
    appointment_date: date
    start_time: time
    end_time: time | None = None
    change_reason: str | None = None


class AppointmentStatusUpdate(BaseModel):
    status: str = Field(pattern="^(confirmed|checked_in|in_progress|completed|cancelled|no_show)$")
    cancellation_reason: str | None = None


class AppointmentRead(BaseModel):
    appointment_id: UUID
    clinic_id: UUID
    patient_id: UUID
    patient_name: str | None = None
    doctor_id: UUID
    doctor_name: str | None = None
    # doctors.doctor_id (public ID) — /doctors/{doctor_id}/availability and
    # similar path params expect this, not doctor_id above (profiles.id).
    doctor_public_id: UUID | None = None
    ca_id: UUID | None
    cycle_id: UUID | None = None
    appointment_date: date
    start_time: time
    end_time: time
    appointment_type: str
    status: str
    reason: str | None = None
    patient_complaint: str | None = None
    notes: str | None = None
    cancellation_reason: str | None = None
    booked_by: UUID
    booked_by_role: str
    cancelled_by: UUID | None = None
    rescheduled_from: UUID | None = None
    rescheduled_to: UUID | None = None
    checked_in_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
