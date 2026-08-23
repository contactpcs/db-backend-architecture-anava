from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ProtocolRequestCreate(BaseModel):
    patient_id: UUID
    doctor_id: UUID
    clinic_id: UUID
    protocol_details: dict = Field(default_factory=dict)


class ProtocolRequestDecision(BaseModel):
    decision: str = Field(pattern="^(approved|modification_requested|rejected)$")
    doctor_notes: str | None = None


class ProtocolRequestRead(BaseModel):
    request_id: UUID
    patient_id: UUID
    clinical_assistant_id: UUID
    doctor_id: UUID
    clinic_id: UUID | None
    protocol_details: dict
    status: str
    doctor_notes: str | None
    submitted_at: datetime
