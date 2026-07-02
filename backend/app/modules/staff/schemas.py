from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class StaffPersonCreate(BaseModel):
    """Shared shape for creating a new staff member's identity + role detail
    in one call. cognito_sub is a placeholder until Stage 13 wires the real
    Cognito invite flow (Flow H) — see service.py docstring."""

    email: EmailStr
    first_name: str
    last_name: str
    phone: str | None = None
    clinic_id: UUID


class DoctorCreate(StaffPersonCreate):
    specialization: str | None = None
    license_number: str | None = None
    hospital_affiliation: str | None = None
    max_patient_count: int = 30


class DoctorUpdate(BaseModel):
    specialization: str | None = None
    max_patient_count: int | None = None
    availability_status: str | None = Field(
        default=None, pattern="^(available|at_capacity|on_leave|inactive)$"
    )


class DoctorRead(BaseModel):
    doctor_id: UUID
    profile_id: UUID
    specialization: str | None
    license_number: str | None
    max_patient_count: int
    availability_status: str
    created_at: datetime


class ClinicalAssistantCreate(StaffPersonCreate):
    qualification: str | None = None


class ClinicalAssistantRead(BaseModel):
    ca_id: UUID
    profile_id: UUID
    clinic_id: UUID
    qualification: str | None
    is_active: bool
    created_at: datetime


class ReceptionistCreate(StaffPersonCreate):
    pass


class ReceptionistRead(BaseModel):
    receptionist_id: UUID
    profile_id: UUID
    clinic_id: UUID
    is_active: bool
    created_at: datetime


class CaDoctorAssignmentCreate(BaseModel):
    ca_id: UUID
    doctor_id: UUID
    clinic_id: UUID
    is_primary: bool = False


class CaDoctorAssignmentRead(BaseModel):
    cda_id: UUID
    ca_id: UUID
    doctor_id: UUID
    clinic_id: UUID
    is_primary: bool
    assigned_at: datetime
    removed_at: datetime | None


class StaffRequestCreate(BaseModel):
    clinic_id: UUID
    request_type: str = Field(pattern="^(open_position|candidate_referral|staff_removal)$")
    position_role: str = Field(pattern="^(doctor|clinical_assistant|receptionist|clinic_admin)$")
    candidate_name: str | None = None
    candidate_email: EmailStr | None = None
    candidate_phone: str | None = None
    candidate_credentials: dict = Field(default_factory=dict)
    target_staff_id: UUID | None = None


class StaffRequestDecision(BaseModel):
    decision: str = Field(pattern="^(under_review|approved|rejected)$")
    review_notes: str | None = None


class StaffRequestRead(BaseModel):
    request_id: UUID
    clinic_id: UUID
    request_type: str
    position_role: str
    candidate_name: str | None
    candidate_email: str | None
    status: str
    submitted_by: UUID
    reviewed_by: UUID | None
    review_notes: str | None
    created_at: datetime
