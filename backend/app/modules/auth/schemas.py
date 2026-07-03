from datetime import date
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class PublicPatientRegister(BaseModel):
    """Public self-registration entry point (no clinic staff involved) —
    creates an inactive patient profile; the patient then completes the
    rest of the 6-step registration machine themselves before a
    receptionist approves the account (see patients module + Master Doc
    self-registration design, SQL/24_patient_self_registration.sql)."""

    email: EmailStr
    first_name: str
    last_name: str
    phone: str | None = None
    gender: str | None = Field(default=None, pattern="^(male|female|other)$")
    dob: date | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    primary_clinic_id: UUID


class LocalLoginRequest(BaseModel):
    """Dev-only. Real login (Stage 13) goes through Cognito directly from the
    frontend — the backend never handles passwords, only validates the
    resulting Cognito JWT (see core/security.py). Accepts either field —
    email is more usable for a frontend login form; cognito_sub still works
    for scripts/tests. Password is deliberately not part of this endpoint:
    dev mode never checks one, matching that Cognito (not this backend) will
    own password verification once Stage 13 lands."""

    cognito_sub: str | None = None
    email: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PublicPatientRegisterResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # patients.patient_id (public ID) — anamnesis/PRS/disease-selection
    # endpoints all key off this, not profiles.id, so the frontend needs it
    # up front to drive the rest of the self-registration wizard.
    patient_id: UUID


class CurrentUserRead(BaseModel):
    """Backing for GET /auth/me — every authenticated role can call this,
    unlike the super_admin-only debug /whoami. This is the 'who am I' every
    frontend needs right after login."""

    id: UUID
    email: str
    first_name: str
    last_name: str
    role: str
    clinic_id: UUID | None = None
    region_id: UUID | None = None
    is_active: bool = True
    consent_type_required: str | None = None
