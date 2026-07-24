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


class SessionCreate(BaseModel):
    patient_id: UUID
    doctor_id: UUID | None = None
    ca_id: UUID | None = None
    cycle_id: UUID | None = None
    clinic_id: UUID | None = None
    session_date: datetime
    session_phase: str | None = Field(
        default=None,
        pattern="^(clinical_assistant|doctor_consultation|additional_tests|doctor_additional_review|treatment|home_treatment_visit)$",
    )
    session_number_in_cycle: int | None = None


class SessionStatusUpdate(BaseModel):
    status: str = Field(pattern="^(in_progress|completed|cancelled|missed)$")
    outcome: str | None = Field(
        default=None,
        pattern="^(session1_complete|treatment_plan_given|additional_tests_requested|session3_complete|home_treatment_visit_complete)$",
    )


class SessionRead(BaseModel):
    session_id: UUID
    patient_id: UUID
    doctor_id: UUID | None
    ca_id: UUID | None
    cycle_id: UUID | None
    session_date: datetime
    session_phase: str | None
    status: str
    outcome: str | None
    payment_status: str | None


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
    extended_sessions: int
    status: str
    parent_plan_id: UUID | None
    created_at: datetime


class TreatmentSessionCreate(BaseModel):
    plan_id: UUID
    session_id: UUID
    patient_id: UUID
    ca_id: UUID
    session_number: int
    billing_type: str = Field(pattern="^(standard|extended)$")


class TreatmentSessionStatusUpdate(BaseModel):
    status: str = Field(pattern="^(in_progress|completed|missed)$")
    session_notes: str | None = None
    patient_feedback: str | None = None


class TreatmentSessionRead(BaseModel):
    ts_id: UUID
    plan_id: UUID
    session_id: UUID
    patient_id: UUID
    ca_id: UUID
    session_number: int
    billing_type: str
    status: str
    payment_status: str


class DoctorSessionNoteCreate(BaseModel):
    session_id: UUID
    cycle_id: UUID
    patient_id: UUID
    doctor_id: UUID
    session_number: int
    session_phase: str = Field(pattern="^(doctor_consultation|doctor_additional_review)$")
    chief_complaint: str | None = None
    clinical_observations: str | None = None
    assessment: str | None = None
    treatment_plan_notes: str | None = None
    follow_up_instructions: str | None = None
    referrals: str | None = None
    note_content: str | None = None
