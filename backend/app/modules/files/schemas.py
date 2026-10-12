from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PresignUploadRequest(BaseModel):
    doc_type: str = Field(pattern="^(eeg|medical_history)$")
    file_name: str = Field(min_length=1, max_length=255)
    clinic_id: UUID
    content_type: str = Field(pattern=r"^[\w.+-]+/[\w.+-]+$")
    # Used only when a patient uploads: their row is created at this step
    # (there is no confirm call), so the document details arrive here.
    document_type: str | None = None
    document_date: date | None = None
    source_provider: str | None = None
    description: str | None = None


class PresignUploadResponse(BaseModel):
    s3_key: str
    upload_url: str
    # S3 mode: form fields to send, before the file, in a multipart POST to
    # upload_url. None in local mode: PUT the bytes to /files/upload/{s3_key}.
    upload_fields: dict[str, str] | None = None
    # Set for a patient upload: the row already exists (status 'scanning')
    # and the client must NOT call the confirm endpoint.
    file: dict | None = None


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
