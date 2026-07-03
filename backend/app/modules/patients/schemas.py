from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class PatientRegister(BaseModel):
    """Registration Step 1 — demographics (Master Doc Section 6.2)."""

    email: EmailStr
    first_name: str
    last_name: str
    phone: str | None = None
    gender: str | None = Field(default=None, pattern="^(male|female|other)$")
    dob: date | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    pincode: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    primary_clinic_id: UUID


class PatientUpdate(BaseModel):
    """Admin-editable demographic fields — split across profiles (identity)
    and patients (clinical/contact) tables under the hood, but presented as
    one flat PATCH from the caller's side."""

    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    gender: str | None = Field(default=None, pattern="^(male|female|other)$")
    dob: date | None = None
    address: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None


class DiseaseSelectionCreate(BaseModel):
    disease_id: str | None = None
    disease_unknown: bool = False
    is_primary: bool = True


class PatientRead(BaseModel):
    patient_id: UUID
    profile_id: UUID
    mrn: str
    registration_status: str
    primary_clinic_id: UUID | None
    primary_doctor_id: UUID | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    registration_completed_at: datetime | None
    created_at: datetime
    # Joined from profiles — patients has no name/email/phone columns of its
    # own (those live on profiles), and every list/detail screen needs them.
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    gender: str | None = None
    dob: date | None = None
    address: str | None = None
    profile_is_active: bool = True
    # Self-registration gate — 'not_required' forever for staff-registered
    # patients (unaffected, matches pre-existing behavior). Self-registered
    # patients start 'pending' and only reach 'approved'/'rejected' once a
    # receptionist decides, after registration_status='registration_complete'.
    self_registered: bool = False
    approval_status: str = "not_required"
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    rejection_reason: str | None = None


class PatientApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    rejection_reason: str | None = None


class DiseaseSelectionRead(BaseModel):
    pds_id: UUID
    patient_id: UUID
    disease_id: str | None
    disease_unknown: bool
    is_primary: bool


class FollowUpCycleCreate(BaseModel):
    doctor_id: UUID | None = None  # None = keep same doctor as current cycle


class TransferInitiate(BaseModel):
    to_clinic_id: UUID
    transfer_reason: str = Field(pattern="^(clinic_closure|patient_relocation|patient_request|doctor_transfer)$")
    to_doctor_id: UUID | None = None  # None = auto-allocate at new clinic
    notes: str | None = None


class TransferComplete(BaseModel):
    consent_id: UUID


class TransferRead(BaseModel):
    pct_id: UUID
    patient_id: UUID
    from_clinic_id: UUID
    to_clinic_id: UUID
    from_doctor_id: UUID | None
    to_doctor_id: UUID | None
    transfer_reason: str
    status: str
    active_cycle_id: UUID | None
    created_at: datetime


class ExitInitiate(BaseModel):
    consent_id: UUID
    reason: str | None = None
