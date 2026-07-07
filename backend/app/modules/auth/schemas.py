from datetime import date
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class PublicPatientRegister(BaseModel):
    """Public self-registration entry point — LOCAL DEV ONLY (404s once
    auth_mode == 'cognito', see /auth/register below). Real patient signup
    goes through the OTP wizard (PatientSignupStart/Verify/Complete) instead,
    which this single-step shape can't represent (email-or-mobile choice,
    OTP verification gating the password step)."""

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


class PatientSignupStart(BaseModel):
    """Step 1 of the real (auth_mode == 'cognito') patient signup wizard —
    collects demographics + clinic + the chosen signup channel, then starts
    Cognito's SignUp (which auto-sends the OTP to that channel). Nothing is
    written to our own DB yet; the wizard is stateless server-side — the
    frontend carries these same fields through to /signup/complete."""

    first_name: str
    last_name: str
    dob: date | None = None
    gender: str | None = Field(default=None, pattern="^(male|female|other)$")
    city: str | None = None
    state: str | None = None
    country: str | None = None
    primary_clinic_id: UUID
    method: str = Field(pattern="^(email|mobile)$")
    # Email address (method=='email') or E.164 phone ("+91XXXXXXXXXX",
    # method=='mobile') — whichever the patient chose to sign up with.
    contact: str


class PatientSignupResend(BaseModel):
    contact: str


class PatientSignupVerify(BaseModel):
    contact: str
    code: str


class PatientSignupComplete(BaseModel):
    """Step 2 — after the OTP is verified, set the real password. Repeats
    the same demographic fields from Start since nothing was persisted
    server-side between steps; primary_clinic_id is re-validated here too
    (open/active, has admins) before the profiles/patients row is created."""

    first_name: str
    last_name: str
    dob: date | None = None
    gender: str | None = Field(default=None, pattern="^(male|female|other)$")
    city: str | None = None
    state: str | None = None
    country: str | None = None
    primary_clinic_id: UUID
    method: str = Field(pattern="^(email|mobile)$")
    contact: str
    password: str = Field(min_length=8)
    confirm_password: str


class VerifyChannelStart(BaseModel):
    """Post-signup verification of the OTHER channel — e.g. a patient who
    signed up with mobile still needs to verify email before it becomes a
    valid login alias. Authenticated (needs the caller's own Cognito access
    token, taken from the Authorization header, not this body). value is the
    actual email/phone to add — the signup-time channel's Cognito user has
    no value at all yet for the other attribute."""

    attribute: str = Field(pattern="^(email|phone_number)$")
    value: str


class VerifyChannelConfirm(BaseModel):
    attribute: str = Field(pattern="^(email|phone_number)$")
    code: str
    # Repeated from the /start call (stateless wizard, same pattern as the
    # signup steps) — needed to overwrite our own profiles.email/phone with
    # the real verified value once Cognito confirms the code.
    value: str


class LocalLoginRequest(BaseModel):
    """Dev-only. Real login (Stage 13) is POST /auth/login below — this
    backend calls Cognito's InitiateAuth directly with the same email/
    password our own login form already collects (decided over the Hosted-
    UI-redirect alternative, to keep the existing custom-styled login pages
    instead of handing the user off to an AWS-hosted page). Accepts either
    field here — email is more usable for a frontend login form; cognito_sub
    still works for scripts/tests. Password is deliberately not part of this
    endpoint: dev mode never checks one."""

    cognito_sub: str | None = None
    email: str | None = None


class LoginRequest(BaseModel):
    """POST /auth/login body — real Cognito password auth (auth_mode ==
    'cognito' only; local dev keeps using LocalLoginRequest above). username
    is an email or a phone number — either works once that channel is a
    verified alias on the account, regardless of which one signup used."""

    username: str
    password: str


class NewPasswordRequest(BaseModel):
    """POST /auth/login/new-password — completes the NEW_PASSWORD_REQUIRED
    challenge /auth/login raises on a staff account's first login (Cognito's
    auto-emailed temp password). session is the value login's error
    returned; the frontend never sees or stores it beyond that round-trip."""

    username: str
    new_password: str = Field(min_length=8)
    session: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # Only populated by real Cognito login — local-login has no refresh
    # token. Returned now so it's available once silent-refresh is built;
    # nothing consumes it yet.
    refresh_token: str | None = None


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
    consent_signed: bool = True
    consent_type_required: str | None = None
    # Self-registration wizard resume support — set whenever role=='patient'
    # and self_registered=TRUE, on EVERY /auth/me call (not just right after
    # POST /auth/register), so a patient who logs back in mid-wizard can be
    # routed to whichever step they left off at instead of always /consent.
    self_registered: bool = False
    patient_id: UUID | None = None
    registration_status: str | None = None
    # doctors.doctor_id (public ID) — role=='doctor' only. FK columns store
    # profiles.id everywhere, but /doctors/{doctor_id}/... path params expect
    # this public ID, not profiles.id — the frontend has no other way to
    # learn its own doctor_id without this.
    doctor_id: UUID | None = None
    # Cognito-mode patient signup only — always True in local mode (no OTP
    # step there). Drives the "verify your email/phone" banner for a patient
    # who's only confirmed the channel they originally signed up with.
    email_verified: bool = True
    phone_verified: bool = True
