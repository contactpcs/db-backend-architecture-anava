from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

ConsentType = Literal[
    "patient_onboarding",
    "patient_clinic_exit",
    "patient_clinic_transfer",
    "patient_relocation_transfer",
    "staff_onboarding",
    "staff_offboarding",
    "clinic_join_anava",
    "clinic_leave_anava",
]
StaffRole = Literal["super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist"]


class Clause(BaseModel):
    """One checkbox on a consent form, defined by the super admin."""

    code: str = Field(pattern=r"^[a-z0-9_]{2,40}$")
    label: str = Field(min_length=1, max_length=1000)
    required: bool = True


def _unique_codes(clauses: list[Clause]) -> list[Clause]:
    codes = [c.code for c in clauses]
    if len(codes) != len(set(codes)):
        raise ValueError("clause codes must be unique")
    return clauses


class ConsentTemplateRead(BaseModel):
    template_id: UUID
    consent_type: str
    role: str | None = None
    language: str
    version: int
    title: str
    content: str
    clauses: list[Clause]
    content_hash: str | None
    is_active: bool
    published_at: datetime | None
    created_at: datetime


class ConsentTemplateCreate(BaseModel):
    consent_type: ConsentType
    role: StaffRole | None = None
    language: str = Field(default="en", pattern=r"^[a-z]{2}$")
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=50000)
    clauses: list[Clause] = Field(default_factory=list, max_length=20)

    _codes = field_validator("clauses")(_unique_codes)


class ConsentTemplateUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, min_length=1, max_length=50000)
    clauses: list[Clause] | None = Field(default=None, max_length=20)

    @field_validator("clauses")
    @classmethod
    def _codes(cls, v: list[Clause] | None) -> list[Clause] | None:
        return _unique_codes(v) if v is not None else v


class ConsentTemplatePublish(BaseModel):
    # patient_onboarding only: every patient who signed an earlier version
    # must sign this one before using the portal again.
    require_reconsent: bool = False


class ConsentRecordCreate(BaseModel):
    consent_type: ConsentType
    patient_id: UUID | None = None
    staff_id: UUID | None = None
    clinic_id: UUID | None = None
    region_id: UUID | None = None
    role: StaffRole | None = None


class ConsentStatusUpdate(BaseModel):
    status: Literal["signed", "revoked"]
    # signed: codes of the ticked clauses + the hash of the text the client
    # displayed (from /consent-records/me/pending).
    accepted: list[str] = Field(default_factory=list, max_length=20)
    rendered_hash: str | None = None


class ClauseResponse(Clause):
    accepted: bool


class Signer(BaseModel):
    signer_type: Literal["self", "guardian"]
    name: str
    relation: str | None = None
    contact: str | None = None
    guardian_reason: str | None = None
    # The patient the guardian signs for.
    on_behalf_of: str | None = None


class ConsentDocument(BaseModel):
    """What the signer sees (pending) or saw (signed)."""

    consent_id: UUID
    consent_type: str
    status: str
    template_id: UUID
    version: int
    language: str
    title: str
    content: str
    clauses: list[ClauseResponse] | list[Clause]
    rendered_hash: str
    signer: Signer | None
    signed_at: datetime | None = None
    pdf_available: bool = False


class ConsentRecordRead(BaseModel):
    consent_id: UUID
    consent_type: str
    template_id: UUID
    patient_id: UUID | None
    staff_id: UUID | None
    clinic_id: UUID | None
    region_id: UUID | None
    status: str
    signed_at: datetime | None
    signed_by: UUID | None
    signer_type: str | None = None
    signer_name: str | None = None
    content_hash_at_signing: str | None
    rendered_hash: str | None = None
    responses: list[ClauseResponse] | None = None
    pdf_available: bool = False
    revoked_at: datetime | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: dict) -> "ConsentRecordRead":
        return cls(**row, pdf_available=bool(row.get("pdf_s3_key")))


class PdfLink(BaseModel):
    url: str
    filename: str
