from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

_CONSENT_TYPES = (
    "patient_onboarding|patient_clinic_exit|patient_clinic_transfer|patient_relocation_transfer|"
    "staff_onboarding|staff_offboarding|clinic_join_anava|clinic_leave_anava"
)


class ConsentTemplateRead(BaseModel):
    template_id: UUID
    consent_type: str
    version: int
    title: str
    content: str
    content_hash: str
    is_active: bool


class ConsentRecordCreate(BaseModel):
    consent_type: str = Field(pattern=f"^({_CONSENT_TYPES})$")
    patient_id: UUID | None = None
    staff_id: UUID | None = None
    clinic_id: UUID


class ConsentSignRequest(BaseModel):
    signature_data: str
    witness_id: UUID | None = None
    ip_address: str | None = None


class ConsentStatusUpdate(BaseModel):
    status: str = Field(pattern="^(signed|revoked)$")
    sign: ConsentSignRequest | None = None
    revoke_reason: str | None = None


class ConsentRecordRead(BaseModel):
    consent_id: UUID
    consent_type: str
    template_id: UUID
    patient_id: UUID | None
    staff_id: UUID | None
    clinic_id: UUID
    status: str
    signed_at: datetime | None
    signed_by: UUID | None
    witness_id: UUID | None
    content_hash_at_signing: str | None
    revoked_at: datetime | None
    created_at: datetime
