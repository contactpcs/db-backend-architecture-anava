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


class WeeklyScheduleRead(BaseModel):
    schedule_id: UUID
    doctor_id: UUID
    clinic_id: UUID
    day_of_week: int
    start_time: time
    end_time: time
    slot_duration_minutes: int
    is_active: bool


class ScheduleOverrideCreate(BaseModel):
    clinic_id: UUID
    override_date: date
    is_available: bool = False
    start_time: time | None = None
    end_time: time | None = None
    reason: str | None = None


class ScheduleOverrideRead(BaseModel):
    override_id: UUID
    doctor_id: UUID
    clinic_id: UUID
    override_date: date
    is_available: bool
    reason: str | None


class AppointmentRequestCreate(BaseModel):
    clinic_id: UUID
    patient_id: UUID
    doctor_id: UUID | None = None
    cycle_id: UUID | None = None
    request_type: str = Field(default="new", pattern="^(new|reschedule|followup_cycle)$")
    preferred_date_1: date
    preferred_date_2: date | None = None
    preferred_date_3: date | None = None
    preferred_time_window: str = Field(default="any", pattern="^(morning|afternoon|evening|any)$")
    patient_complaint: str | None = None
    urgency: str = Field(default="normal", pattern="^(normal|urgent|emergency)$")


class AppointmentRequestDecision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|cancelled_by_patient)$")
    review_notes: str | None = None
    # required when decision == approved:
    appointment_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    doctor_id: UUID | None = None
    appointment_type: str | None = None


class AppointmentRequestRead(BaseModel):
    request_id: UUID
    clinic_id: UUID
    patient_id: UUID
    doctor_id: UUID | None
    request_type: str
    preferred_date_1: date
    status: str
    approved_appointment_id: UUID | None
    created_at: datetime


class AppointmentCreate(BaseModel):
    clinic_id: UUID
    patient_id: UUID
    doctor_id: UUID
    ca_id: UUID | None = None
    cycle_id: UUID | None = None
    appointment_date: date
    start_time: time
    end_time: time
    appointment_type: str = Field(default="initial_assessment", pattern="^(initial_assessment|doctor_consultation|ca_session|treatment_session|follow_up|demo_visit|teleconsult)$")
    session_phase: str | None = None
    reason: str | None = None
    patient_complaint: str | None = None


class AppointmentReschedule(BaseModel):
    appointment_date: date
    start_time: time
    end_time: time
    change_reason: str | None = None


class AppointmentStatusUpdate(BaseModel):
    status: str = Field(pattern="^(confirmed|checked_in|in_progress|completed|cancelled|no_show)$")
    cancellation_reason: str | None = None


class AppointmentRead(BaseModel):
    appointment_id: UUID
    clinic_id: UUID
    patient_id: UUID
    doctor_id: UUID
    ca_id: UUID | None
    session_id: UUID | None
    appointment_date: date
    start_time: time
    end_time: time
    appointment_type: str
    status: str
    created_at: datetime
