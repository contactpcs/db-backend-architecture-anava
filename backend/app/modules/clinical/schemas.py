from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field


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
