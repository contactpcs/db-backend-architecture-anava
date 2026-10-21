from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Scope = Literal["overall", "last_consultation"]


class FinalReportSelection(BaseModel):
    """Per report section, the ids the doctor ticked with that section's
    pencil. A missing or null key keeps the scope's default."""

    anamnesis_ids: list[str] | None = Field(None, max_length=500)
    # Main PRS records (prs instance ids).
    prs_ids: list[str] | None = Field(None, max_length=500)
    # Device-session PRS records (prs instance ids).
    session_prs_ids: list[str] | None = Field(None, max_length=500)
    note_ids: list[UUID] | None = Field(None, max_length=500)
    medicine_ids: list[UUID] | None = Field(None, max_length=500)
    protocol_ids: list[UUID] | None = Field(None, max_length=200)
    # Device sessions for section 08, by appointment id.
    session_ids: list[UUID] | None = Field(None, max_length=1000)


class FinalReportDraft(BaseModel):
    scope: Scope
    selection: FinalReportSelection | None = None


class FinalReportSign(BaseModel):
    scope: Scope
    selection: FinalReportSelection | None = None
    # The doctor's own wording, e.g. "MDD + mild psychotic episodes". Required.
    diagnosis: str = Field(max_length=2000)
    # patient_clinical_notes the doctor ticked for inclusion.
    note_ids: list[UUID] = Field(default_factory=list, max_length=200)
    # The "I confirm" checkbox — the e-signature. Must be true.
    confirm_sign: bool = False


class FinalReportPreview(BaseModel):
    scope: Scope
    # Same shape as FinalReportRead.snapshot.
    sections: dict
    # The ids the sections hold, per FinalReportSelection key.
    selection: dict
    # Per section, everything its pencil can offer.
    candidates: dict


class FinalReportMeta(BaseModel):
    report_id: UUID
    patient_id: UUID
    clinic_id: UUID
    doctor_id: UUID
    doctor_name: str | None = None
    scope: Scope
    appointment_id: UUID | None = None
    diagnosis: str
    signature: dict
    content_hash: str
    signed_at: datetime


class FinalReportRead(FinalReportMeta):
    snapshot: dict
