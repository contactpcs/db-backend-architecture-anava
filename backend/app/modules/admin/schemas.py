from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class RegionCreate(BaseModel):
    region_name: str
    country: str
    state: str


class RegionUpdate(BaseModel):
    region_name: str | None = None
    regional_admin_id: UUID | None = None
    is_active: bool | None = None


class RegionRead(BaseModel):
    region_id: UUID
    region_name: str
    country: str
    state: str
    regional_admin_id: UUID | None
    is_active: bool
    created_at: datetime


class ClinicCreate(BaseModel):
    """clinic_admin_id is deliberately absent — clinic creation is a 2-step
    flow (create, then POST /clinics/{id}/assign-admin). is_main_branch is
    also absent — the service auto-sets it to True for a region's first
    clinic, callers don't choose it."""

    clinic_code: str
    clinic_name: str
    clinic_type: str = Field(pattern="^(anava_owned|partner|mobile)$")
    region_id: UUID
    address: str | None = None
    city: str | None = None
    state: str | None = None
    phone: str | None = None
    email: str | None = None


class ClinicAdminAssign(BaseModel):
    """Creates a brand-new clinic_admin profile and assigns them to the
    clinic in one call — there's no pre-existing pool of unassigned
    clinic_admin profiles to pick from (no self-serve admin registration
    exists), so this always creates the person too."""

    email: str
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


class RegionalAdminAssign(BaseModel):
    """Creates the region's regional_admin — a person based at the region's
    main-branch clinic (its first-created clinic), which must already exist.
    clinic_id must reference that exact clinic (validated in the service,
    not just any clinic in the region). Every region needs one of these
    before its clinics can onboard a clinic_admin, other staff, or patients."""

    clinic_id: UUID
    email: str
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


class AdminAccountUpdate(BaseModel):
    """Profile-level fields only (matches StaffProfileUpdate's convention) —
    no admin_type/region_id/clinic_id (structural, changed via dedicated
    flows like RegionService.assign_admin, not a generic edit). Unlike
    doctor/CA/receptionist, an admin's email has no org-domain restriction
    to re-check on change (admins are exempt — see staff/service.py::
    _assert_staff_email_domain's docstring)."""

    first_name: str | None = None
    last_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    is_active: bool | None = None


class AdminAccountRead(BaseModel):
    """Joined view over admins+profiles(+regions/clinics) — the only admin
    list endpoint in this module that returns real names/emails, since it's
    a purpose-built management screen rather than a generic resource list."""

    admin_id: UUID
    profile_id: UUID
    admin_type: str
    first_name: str
    last_name: str
    email: str
    phone: str | None
    is_active: bool
    region_id: UUID | None
    region_name: str | None
    clinic_id: UUID | None
    clinic_name: str | None
    created_at: datetime


class ClinicUpdate(BaseModel):
    clinic_name: str | None = None
    clinic_admin_id: UUID | None = None
    is_main_branch: bool | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None


class ClinicStatusUpdate(BaseModel):
    status: str = Field(pattern="^(setup|active|pending_closure|closed)$")


class ClinicRead(BaseModel):
    clinic_id: UUID
    clinic_code: str
    clinic_name: str
    clinic_type: str
    status: str
    region_id: UUID
    clinic_admin_id: UUID | None
    is_main_branch: bool
    address: str | None
    city: str | None
    state: str | None
    phone: str | None
    email: str | None
    created_at: datetime


class ClinicRequestCreate(BaseModel):
    request_type: str = Field(pattern="^(create_clinic|close_clinic|change_admin|change_main_branch)$")
    clinic_type: str | None = None
    clinic_id: UUID | None = None
    region_id: UUID
    payload: dict = Field(default_factory=dict)


class ClinicRequestDecision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|withdrawn)$")
    review_notes: str | None = None


class ClinicRequestRead(BaseModel):
    request_id: UUID
    request_type: str
    clinic_type: str | None
    clinic_id: UUID | None
    region_id: UUID
    submitted_by: UUID
    status: str
    payload: dict
    reviewed_by: UUID | None
    review_notes: str | None
    created_at: datetime


class StaffAssignmentCreate(BaseModel):
    profile_id: UUID
    staff_role: str = Field(pattern="^(clinic_admin|doctor|clinical_assistant|receptionist)$")


class StaffAssignmentRead(BaseModel):
    assignment_id: UUID
    clinic_id: UUID
    profile_id: UUID
    staff_role: str
    is_active: bool
    joined_at: datetime
    removed_at: datetime | None
