"""Adapter shapes for `API_ENDPOINTS (2).md` (the "Receptionist Module API
Specification", v2.0) — only the subset backed by real, already-built
functionality: patient registration (§3), patient list + profile (§4.1-4.2),
self-registration approvals (§7), notifications (§9.1-9.4, partial), staff
profile (§11, partial), and master data (§13.1, §13.3). Everything else in
that doc (appointments, billing, reports, settings, patient documents —
that module exists but serves clinical EEG data, a semantic mismatch with
the spec's identity-document intent, not adapted here) references
tables/modules this backend doesn't have yet, or doesn't honestly match —
not built here, see the integration guide for the full list and why.

These endpoints wrap the EXISTING Cognito-based receptionist registration
(app/modules/auth/router.py's receptionist-signup/*) and the existing
patients module — no second password system, no new verification-state
table. Several fields in the spec have no real backing (a separate
`registration_id`/`verification_id` concept, a `PT-####` display sequence);
where that's true, this adapter uses the closest real value (our UUIDs) and
the integration guide documents the deviation explicitly rather than
fabricating data."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SendCodeRequest(BaseModel):
    channel: str = Field(pattern="^(phone|email)$")
    contact: str
    # Not in the original spec's 3.1 request body (which only has
    # channel+contact) — our underlying Cognito SignUp call needs these
    # attributes at account-creation time, not deferred to the final step.
    # Documented as a deviation in the integration guide.
    first_name: str
    last_name: str
    dob: date | None = None
    gender: str | None = Field(default=None, pattern="^(male|female|other)$")


class SendCodeResponse(BaseModel):
    verification_id: str
    channel: str
    contact: str
    code_length: int = 6
    expires_in_seconds: int = 600
    sent_at: datetime


class VerifyCodeRequest(BaseModel):
    verification_id: str
    code: str


class VerifyCodeResponse(BaseModel):
    verification_id: str
    channel: str
    contact: str
    verified: bool
    verified_at: datetime
    registration_token: str


class PasswordPolicyResponse(BaseModel):
    min_length: int = 8
    require_uppercase: bool = False
    require_digit: bool = False
    require_symbol: bool = False


class PersonalDetails(BaseModel):
    first_name: str
    last_name: str
    gender: str = Field(pattern="^(male|female|other)$")
    date_of_birth: date


class AddressDetails(BaseModel):
    street: str | None = None
    city: str
    state: str
    country: str = "IN"
    pincode: str | None = None


class GuardianDetails(BaseModel):
    name: str
    relation: str
    contact_number: str


class ConsentDetails(BaseModel):
    accepted: bool
    signature_captured: bool = False


class RegisterPatientRequest(BaseModel):
    registration_token: str
    personal: PersonalDetails
    address: AddressDetails
    clinic_id: UUID
    guardian: GuardianDetails | None = None
    password: str = Field(min_length=8)
    consent: ConsentDetails


class RegisterPatientResponse(BaseModel):
    patient_id: UUID
    full_name: str
    login_id: str
    login_channel: str
    registration_status: str
    consent_status: str
    created_at: datetime


class Pagination(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool


class PatientListItem(BaseModel):
    patient_id: UUID
    full_name: str
    initials: str
    age: int | None
    gender: str | None
    phone: str | None
    assigned_doctor: str | None
    # Not in the original spec's row shape — added because it's real,
    # already-tracked data (patients.registration_status) directly useful
    # in this same list. See PatientService._REGISTRATION_STEPS for the
    # ordered value set.
    registration_status: str
    # Both always null — depend on the appointments module, explicitly
    # excluded from this adapter's scope.
    last_visit: str | None = None
    next_appointment: str | None = None


class PatientListResponse(BaseModel):
    items: list[PatientListItem]
    pagination: Pagination


class RegistrationListItem(BaseModel):
    # No separate registration_requests table — a "registration" here IS a
    # self-registered patients row with approval_status='pending'/etc, so
    # registration_id is that row's real patient UUID, not a REG-#### code.
    registration_id: UUID
    full_name: str
    contact: str
    contact_type: str
    submitted_on: str | None
    status: str
    linked_patient_id: UUID | None = None
    allowed_actions: list[str]


class RegistrationListResponse(BaseModel):
    items: list[RegistrationListItem]
    pagination: Pagination


class ApproveRejectResponse(BaseModel):
    registration_id: UUID
    status: str
    patient_id: UUID | None = None
    full_name: str | None = None


class MyProfileResponse(BaseModel):
    """Only real fields — the spec's employee_id/department/working_hours/
    joining_date/emergency_contact/security.* have no backing table
    anywhere in this schema (checked directly, not assumed)."""

    profile_id: UUID
    full_name: str
    email: str
    phone: str | None
    role: str
    clinic: str | None
    is_active: bool


class UpdateMyProfileRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)
    confirm_password: str


class ChangePasswordResponse(BaseModel):
    changed_at: datetime


class PatientPersonal(BaseModel):
    date_of_birth: date | None
    age: int | None
    gender: str | None


class PatientContact(BaseModel):
    phone: str | None
    email: str | None
    address: str | None


class PatientProfileResponse(BaseModel):
    """Real fields only — medical_summary/current_medications/care/
    registration_timeline from the original spec have no backing (they're
    doctor-authored clinical data this module was never meant to see, or
    reference tables — diagnoses/medications — that don't exist)."""

    patient_id: UUID
    full_name: str
    is_active: bool
    registration_status: str
    approval_status: str
    personal: PatientPersonal
    contact: PatientContact
    assigned_doctor: str | None
    guardian_name: str | None = None
    guardian_relationship: str | None = None
    guardian_contact: str | None = None


class NotificationItem(BaseModel):
    notification_id: UUID
    category: str
    message: str
    is_read: bool
    when: datetime


class NotificationCounts(BaseModel):
    unread: int
    total: int


class NotificationListResponse(BaseModel):
    items: list[NotificationItem]
    counts: NotificationCounts


class UnreadCountResponse(BaseModel):
    total_unread: int


class ToggleReadRequest(BaseModel):
    # Only True is honestly supported — real Cognito-adjacent notifications
    # table has no "mark unread" concept, unlike the spec's bidirectional
    # toggle. Sending false raises a clear error rather than silently no-op.
    is_read: bool = True


class ToggleReadResponse(BaseModel):
    notification_id: UUID
    is_read: bool
    total_unread: int


class MarkAllReadResponse(BaseModel):
    marked_count: int
    total_unread: int


class DoctorListItem(BaseModel):
    doctor_id: UUID
    name: str
    department: str | None
    label: str


class DoctorListResponse(BaseModel):
    items: list[DoctorListItem]


class EnumOption(BaseModel):
    value: str
    label: str


class EnumsResponse(BaseModel):
    """Real values only — the exact set this backend's own Pydantic
    validators accept, not the spec's aspirational list (e.g. identity_type
    is omitted entirely — no such field exists anywhere in registration)."""

    gender: list[EnumOption]
    relationship: list[EnumOption]
