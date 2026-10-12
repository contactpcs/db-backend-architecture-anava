from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Scope = Literal["overall", "last_consultation"]


class FinalReportSign(BaseModel):
    scope: Scope
    # The doctor's own wording, e.g. "MDD + mild psychotic episodes". Required.
    diagnosis: str = Field(max_length=2000)
    # patient_clinical_notes the doctor ticked for inclusion.
    note_ids: list[UUID] = Field(default_factory=list, max_length=200)
    # The "I confirm" checkbox — the e-signature. Must be true.
    confirm_sign: bool = False


class NoteCandidate(BaseModel):
    note_id: UUID
    category: str
    note_text: str
    created_at: datetime
    doctor_name: str | None = None
    appointment_id: UUID | None = None
    # Belongs to the chosen scope's window — what the picker pre-ticks.
    in_scope: bool


class FinalReportPreview(BaseModel):
    scope: Scope
    # Same shape as FinalReportRead.snapshot, with doctor_notes still empty.
    sections: dict
    note_candidates: list[NoteCandidate]


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
