from datetime import date, datetime, time
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


class BillableItemCreate(BaseModel):
    """appointment_type XOR device_id, matching chk_billable_items_category_shape
    — the service nulls out whichever one doesn't apply to `category`.

    clinic_id: None = platform default price. Set = an override for that one
    clinic only. Not editable after creation (see BillableItemUpdate) — a
    price scoped to the wrong clinic is deactivated and recreated, not moved."""

    item_code: str
    category: str = Field(pattern="^(appointment|device_session)$")
    appointment_type: str | None = None
    device_id: UUID | None = None
    clinic_id: UUID | None = None
    name: str
    description: str | None = None
    price: float = Field(ge=0)
    currency: str = "INR"
    duration_minutes: int | None = None


class BillableItemUpdate(BaseModel):
    """category/appointment_type/device_id are not editable after creation —
    reprice by deactivating this row and creating a new active one instead
    (matches the unique-one-active-per-appointment_type/device_id index)."""

    name: str | None = None
    description: str | None = None
    price: float | None = Field(default=None, ge=0)
    currency: str | None = None
    duration_minutes: int | None = None
    is_active: bool | None = None


class BillableItemRead(BaseModel):
    item_id: UUID
    item_code: str
    category: str
    appointment_type: str | None
    device_id: UUID | None
    clinic_id: UUID | None
    name: str
    description: str | None
    price: float
    currency: str
    duration_minutes: int | None
    is_active: bool
    created_by: UUID | None
    updated_by: UUID | None
    created_at: datetime
    updated_at: datetime


class PlatformFeeUpdate(BaseModel):
    fee_percent: float = Field(ge=0, le=100)


class PlatformFeeRead(BaseModel):
    session_type: str
    fee_percent: float
    updated_by: UUID | None
    updated_at: datetime


class CancellationPolicyTierCreate(BaseModel):
    """clinic_id: None = platform default tier, applies to every clinic with
    no tiers of its own for this session_type. Set = an override tier set
    for that one clinic (see CancellationPolicyRepository.resolve_tiers —
    any clinic-specific row makes the whole default set stop applying to
    that clinic, so a clinic override must be a complete tier set, not a
    single patched threshold)."""

    clinic_id: UUID | None = None
    session_type: str = Field(pattern="^(appointment|device_session)$")
    min_hours_before: float = Field(ge=0)
    refund_percent: float = Field(ge=0, le=100)


class CancellationPolicyTierUpdate(BaseModel):
    min_hours_before: float | None = Field(default=None, ge=0)
    refund_percent: float | None = Field(default=None, ge=0, le=100)


class CancellationPolicyTierRead(BaseModel):
    tier_id: UUID
    clinic_id: UUID | None
    session_type: str
    min_hours_before: float
    refund_percent: float
    created_by: UUID | None
    updated_by: UUID | None
    created_at: datetime
    updated_at: datetime


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
    # Day-to-day open/closed toggle — independent of `status` below (that's
    # the onboarding lifecycle; this is "open for business today"). See
    # 65_clinic_hours_and_operational_status.sql.
    is_operational: bool | None = None


class ClinicStatusUpdate(BaseModel):
    status: str = Field(pattern="^(setup|active|pending_closure|closed)$")


class ClinicRead(BaseModel):
    clinic_id: UUID
    clinic_code: str
    clinic_name: str
    clinic_type: str
    status: str
    is_operational: bool
    region_id: UUID
    clinic_admin_id: UUID | None
    is_main_branch: bool
    address: str | None
    city: str | None
    state: str | None
    phone: str | None
    email: str | None
    created_at: datetime


class ClinicWeeklyHoursItem(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    end_time: time


class ClinicWeeklyHoursReplace(BaseModel):
    items: list[ClinicWeeklyHoursItem]


class ClinicWeeklyHoursRead(BaseModel):
    hours_id: UUID
    clinic_id: UUID
    day_of_week: int
    start_time: time
    end_time: time
    created_by: UUID | None
    updated_by: UUID | None
    created_at: datetime
    updated_at: datetime


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
