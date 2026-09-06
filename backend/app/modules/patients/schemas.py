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
    email: EmailStr | None = None
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
    guardian_name: str | None = None
    guardian_relationship: str | None = None
    guardian_contact: str | None = None
    is_active: bool | None = None


class PatientSelfUpdate(BaseModel):
    """Patient self-service edit (PATCH /patients/{id}/self). Everything a
    patient may change about their own record — mrn, approval_status,
    is_active, primary_doctor_id and other staff-owned fields are
    deliberately absent, matching the admin PatientUpdate minus is_active."""

    first_name: str | None = None
    last_name: str | None = None
    email: EmailStr | None = None
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
    guardian_name: str | None = None
    guardian_relationship: str | None = None
    guardian_contact: str | None = None
    # Self-reported, no server-side verification — patients/profile page's
    # "Medical Information" + remaining "Personal Information" fields.
    language_pref: str | None = None
    blood_group: str | None = None
    allergies: str | None = None
    occupation: str | None = None
    marital_status: str | None = None
    insurance_provider: str | None = None
    insurance_policy: str | None = None
    weight_kg: float | None = None
    height_ft: int | None = None
    height_in: int | None = None
    government_id: str | None = None
    id_type: str | None = None


class PatientRead(BaseModel):
    patient_id: UUID
    profile_id: UUID
    mrn: str
    registration_status: str
    primary_clinic_id: UUID | None
    primary_doctor_id: UUID | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    guardian_name: str | None = None
    guardian_relationship: str | None = None
    guardian_contact: str | None = None
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
    city: str | None = None
    state: str | None = None
    country: str | None = None
    pincode: str | None = None
    language_pref: str | None = None
    blood_group: str | None = None
    allergies: str | None = None
    occupation: str | None = None
    marital_status: str | None = None
    insurance_provider: str | None = None
    insurance_policy: str | None = None
    weight_kg: float | None = None
    height_ft: int | None = None
    height_in: int | None = None
    government_id: str | None = None
    id_type: str | None = None
    profile_is_active: bool = True
    doctor_name: str | None = None
    doctor_first_name: str | None = None
    doctor_last_name: str | None = None
    doctor_phone: str | None = None
    doctor_specialization: str | None = None
    # Self-registration gate — 'not_required' forever for staff-registered
    # patients (unaffected, matches pre-existing behavior). Self-registered
    # patients start 'pending' and only reach 'approved'/'rejected' once a
    # receptionist decides, after registration_status='registration_complete'.
    self_registered: bool = False
    approval_status: str = "not_required"
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    rejection_reason: str | None = None
    profile_completion_percentage: int = 0
    profile_completion_missing_fields: list[str] = Field(default_factory=list)


class PatientApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    rejection_reason: str | None = None


class DoctorAllocation(BaseModel):
    doctor_id: UUID


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
    active_instance_id: UUID | None
    created_at: datetime


class ExitInitiate(BaseModel):
    consent_id: UUID
    reason: str | None = None


class VisitSummaryRead(BaseModel):
    """Everything tied to one visit, for the doctor portal's per-visit
    toggle: registration (initial visit only), anamnesis, PRS instances, and
    protocol prescriptions authored at this visit. Nested items stay raw
    dicts (each module's own repository/service already shapes them) rather
    than duplicating typed schemas cross-module — this endpoint composes,
    it doesn't reinvent."""

    appointment_id: UUID
    appointment_type: str
    appointment_date: date
    registration: dict | None = None
    anamnesis: dict | None = None
    prs_instances: list[dict] = Field(default_factory=list)
    protocols: list[dict] = Field(default_factory=list)
