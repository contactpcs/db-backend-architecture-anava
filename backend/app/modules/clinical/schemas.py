from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TreatmentCycleCreate(BaseModel):
    patient_id: UUID  # patients.patient_id — resolved internally
    doctor_id: UUID  # doctors.doctor_id — resolved internally
    clinic_id: UUID
    cycle_type: str = Field(pattern="^(initial|followup)$")
    cycle_number: int = 1


class TreatmentCycleStatusUpdate(BaseModel):
    status: str = Field(pattern="^(completed|cancelled)$")


class TreatmentCycleRead(BaseModel):
    cycle_id: UUID
    patient_id: UUID
    doctor_id: UUID
    ca_id: UUID | None
    clinic_id: UUID
    cycle_type: str
    cycle_number: int
    status: str
    created_at: datetime


class ProtocolRequestCreate(BaseModel):
    patient_id: UUID
    doctor_id: UUID
    clinic_id: UUID
    cycle_id: UUID | None = None
    protocol_details: dict = Field(default_factory=dict)


class ProtocolRequestDecision(BaseModel):
    decision: str = Field(pattern="^(approved|modification_requested|rejected)$")
    doctor_notes: str | None = None


class ProtocolRequestRead(BaseModel):
    request_id: UUID
    patient_id: UUID
    clinical_assistant_id: UUID
    doctor_id: UUID
    clinic_id: UUID | None
    cycle_id: UUID | None
    protocol_details: dict
    status: str
    doctor_notes: str | None
    submitted_at: datetime


class TreatmentPlanCreate(BaseModel):
    patient_id: UUID
    doctor_id: UUID
    cycle_id: UUID
    device_type: str
    protocol_details: dict = Field(default_factory=dict)
    sessions_prescribed: int = 5
    standard_sessions: int = 5
    parent_plan_id: UUID | None = None


class TreatmentPlanUpdate(BaseModel):
    sessions_prescribed: int | None = None
    status: str | None = Field(default=None, pattern="^(active|completed|superseded)$")


class TreatmentPlanRead(BaseModel):
    plan_id: UUID
    patient_id: UUID
    doctor_id: UUID
    cycle_id: UUID
    device_type: str
    sessions_prescribed: int
    standard_sessions: int
    # Nullable in the DB (05_tables_core.sql: "extended_sessions" INTEGER, no
    # NOT NULL and no default) — sessions beyond the standard allowance, which
    # do not exist until some are added. Declaring it a bare int made every
    # create return 500: the INSERT succeeded, then response serialization
    # rejected the NULL and the transaction rolled back, so the plan silently
    # never persisted.
    extended_sessions: int | None = None
    status: str
    parent_plan_id: UUID | None
    created_at: datetime
