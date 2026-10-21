from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

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
    # The full form (H1). Only in the super admin's listing; everyone else
    # gets has_full_html and reads the filled form per consent record.
    full_html: str | None = None
    has_full_html: bool = False

    @model_validator(mode="before")
    @classmethod
    def _has_full(cls, data):
        if isinstance(data, dict) and "has_full_html" not in data:
            data = {**data, "has_full_html": bool(data.get("full_html"))}
        return data


# Characters, not bytes; fill.check_full_html enforces the 1 MB byte cap.
FullHtml = Annotated[str | None, Field(max_length=1_048_576)]


class ConsentTemplateCreate(BaseModel):
    consent_type: ConsentType
    role: StaffRole | None = None
    language: str = Field(default="en", pattern=r"^[a-z]{2}$")
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=50000)
    clauses: list[Clause] = Field(default_factory=list, max_length=20)
    full_html: FullHtml = None

    _codes = field_validator("clauses")(_unique_codes)


class ConsentTemplateUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, min_length=1, max_length=50000)
    clauses: list[Clause] | None = Field(default=None, max_length=20)
    # "" removes the full form.
    full_html: FullHtml = None

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
    # A "Read the full consent form" link exists (GET /consent-records/{id}/full).
    has_full_form: bool = False
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
    full_viewed_at: datetime | None = None
    revoked_at: datetime | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: dict) -> "ConsentRecordRead":
        return cls(**row, pdf_available=bool(row.get("pdf_s3_key")))


class FullForm(BaseModel):
    html: str


class Placeholder(BaseModel):
    name: str
    description: str
    # Personal details: only in the full form (as [NAME] or <input name="NAME">).
    full_form_only: bool = False
    # required: publishing a full form without it is refused.
    level: Literal["required", "recommended", "optional"] = "optional"


class FullFormAnalyzeRequest(BaseModel):
    consent_type: ConsentType
    role: StaffRole | None = None
    full_html: str = Field(max_length=1_048_576)


class DetectedField(BaseModel):
    name: str
    description: str
    as_input: bool
    as_text: bool
    known: bool
    required: bool


class FullFormAnalysis(BaseModel):
    """Every field the uploaded form will capture, and what blocks it."""

    fields: list[DetectedField]
    missing_required: list[str]
    missing_recommended: list[str]
    unknown: list[str]
    problems: list[str]
    has_guardian_section: bool


class StarterForm(BaseModel):
    filename: str
    html: str


class PdfLink(BaseModel):
    url: str
    filename: str
