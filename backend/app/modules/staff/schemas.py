from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class StaffPersonCreate(BaseModel):
    """Shared shape for creating a new staff member's identity + role detail
    in one call. cognito_sub is a placeholder until Stage 13 wires the real
    Cognito invite flow (Flow H) — see service.py docstring.

    Captures every profiles column that's meaningful to collect at
    registration time (not just email/name/phone) so the record is complete
    from day one rather than needing a separate profile-edit step later."""

    email: EmailStr
    first_name: str
    last_name: str
    phone: str | None = None
    clinic_id: UUID
    gender: str | None = Field(default=None, pattern="^(male|female|other)$")
    dob: date | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    pincode: str | None = None
    # Set when this profile fulfills an approved staff_request (see
    # StaffRequestRepository.fulfill) — optional, direct creates (no
    # referral) leave this unset.
    staff_request_id: UUID | None = None


class DoctorCreate(StaffPersonCreate):
    specialization: str | None = None
    license_number: str | None = None
    hospital_affiliation: str | None = None
    max_patient_count: int = 30


class StaffProfileUpdate(BaseModel):
    """Shared profile-level fields every staff *Update schema accepts
    alongside its own role-specific ones — admin edits go through one PATCH,
    not a separate profile-edit endpoint. email is re-validated against the
    org-domain rule on change (see staff/service.py::_apply_profile_update)."""

    first_name: str | None = None
    last_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    gender: str | None = Field(default=None, pattern="^(male|female|other)$")
    dob: date | None = None
    address: str | None = None


class DoctorUpdate(StaffProfileUpdate):
    specialization: str | None = None
    license_number: str | None = None
    hospital_affiliation: str | None = None
    max_patient_count: int | None = None
    availability_status: str | None = Field(default=None, pattern="^(available|at_capacity|on_leave|inactive)$")


class DoctorRead(BaseModel):
    doctor_id: UUID
    profile_id: UUID
    specialization: str | None
    license_number: str | None
    max_patient_count: int
    availability_status: str
    created_at: datetime
    # Joined from profiles — doctors has no name/email/phone columns of its
    # own, and no is_active column either (availability_status is a
    # different concept; profile_is_active is the real consent-gate signal).
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    profile_is_active: bool = True
    # Denormalized primary-clinic column (SQL/20_doctor_clinic_id.sql) —
    # clinic_staff_assignments remains the source of truth for multi-clinic
    # doctor membership, this is a fast-lookup convenience kept in sync at
    # write time (see DoctorRepository.create).
    clinic_id: UUID | None = None


class ClinicalAssistantCreate(StaffPersonCreate):
    qualification: str | None = None


class ClinicalAssistantUpdate(StaffProfileUpdate):
    qualification: str | None = None
    is_active: bool | None = None


class ClinicalAssistantRead(BaseModel):
    ca_id: UUID
    profile_id: UUID
    clinic_id: UUID
    qualification: str | None
    # this role slot's own on/off flag — kept in sync with profile_is_active on
    # every update, see staff/service.py::_split_profile_fields
    is_active: bool
    created_at: datetime
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    profile_is_active: bool = True


class ReceptionistCreate(StaffPersonCreate):
    pass


class ReceptionistUpdate(StaffProfileUpdate):
    is_active: bool | None = None


class ReceptionistRead(BaseModel):
    receptionist_id: UUID
    profile_id: UUID
    clinic_id: UUID
    # this role slot's own on/off flag — kept in sync with profile_is_active on
    # every update, see staff/service.py::_split_profile_fields
    is_active: bool
    created_at: datetime
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    profile_is_active: bool = True


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
    position_role: str = Field(pattern="^(doctor|clinical_assistant|receptionist)$")
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
    candidate_phone: str | None = None
    candidate_credentials: dict = Field(default_factory=dict)
    status: str
    submitted_by: UUID
    reviewed_by: UUID | None
    review_notes: str | None
    created_at: datetime
    # Set once a doctor/CA/receptionist profile is created against this
    # request (see StaffRequestRepository.fulfill) — status itself stays
    # 'approved' forever, this is the actual fulfillment signal.
    fulfilled_profile_id: UUID | None = None
    fulfilled_at: datetime | None = None
