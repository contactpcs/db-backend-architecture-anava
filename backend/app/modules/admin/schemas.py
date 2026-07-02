from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


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
    clinic_code: str
    clinic_name: str
    clinic_type: str = Field(pattern="^(anava_owned|partner|mobile)$")
    region_id: UUID
    clinic_admin_id: UUID
    is_main_branch: bool = False
    address: str | None = None
    city: str | None = None
    state: str | None = None
    phone: str | None = None
    email: str | None = None


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
    clinic_admin_id: UUID
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
