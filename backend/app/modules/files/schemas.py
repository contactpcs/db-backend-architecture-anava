from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PresignUploadRequest(BaseModel):
    doc_type: str = Field(pattern="^(eeg|medical_history)$")
    file_name: str
    clinic_id: UUID


class PresignUploadResponse(BaseModel):
    s3_key: str
    upload_url: str


class FileConfirmCreate(BaseModel):
    doc_type: str = Field(pattern="^(eeg|medical_history)$")
    s3_key: str
    file_name: str
    clinic_id: UUID
    # eeg-specific
    eeg_type: str | None = None
    duration_minutes: int | None = None
    # medical-history-specific
    document_type: str | None = None
    document_date: date | None = None
    source_provider: str | None = None
    description: str | None = None


class FileReviewUpdate(BaseModel):
    clinical_findings: str | None = None
    is_abnormal: bool | None = None
    status: str | None = Field(default=None, pattern="^(raw_uploaded|report_pending|report_ready|reviewed)$")


class FileRead(BaseModel):
    file_id: UUID
    doc_type: str
    patient_id: UUID
    clinic_id: UUID
    file_name: str
    file_size: int | None
    checksum: str | None
    status: str | None
    is_abnormal: bool | None
    created_at: datetime
