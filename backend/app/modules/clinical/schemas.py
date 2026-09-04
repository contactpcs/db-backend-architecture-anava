from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ProtocolRequestCreate(BaseModel):
    patient_id: UUID
    doctor_id: UUID
    clinic_id: UUID
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
    protocol_details: dict
    status: str
    doctor_notes: str | None
    submitted_at: datetime


class MyProfileResponse(BaseModel):
    """Same shape as reception/schemas.py's MyProfileResponse, extended with
    the same profile fields DoctorRead carries (staff/schemas.py) so the CA
    profile page can match the doctor page's layout — only real fields (no
    employee_id/department/working_hours/joining_date/qualification display,
    none of which have a backing column anywhere in this schema)."""

    profile_id: UUID
    full_name: str
    email: str
    phone: str | None
    role: str
    clinic: str | None
    is_active: bool
    gender: str | None = None
    dob: date | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    pincode: str | None = None
    language_pref: str | None = None


class UpdateMyProfileRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    gender: str | None = Field(default=None, pattern="^(male|female|other)$")
    dob: date | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    pincode: str | None = None
    language_pref: str | None = None
