from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AnamnesisQuestionRead(BaseModel):
    question_id: str
    section_number: int
    section_title: str
    question_code: str
    question_text: str
    answer_type: str
    is_required: bool
    display_order: int
    depends_on_question_id: str | None
    depends_on_value: str | None
    helper_text: str | None


class AnamnesisStart(BaseModel):
    taken_by: str = "patient"


class ResponseItem(BaseModel):
    question_id: str
    response_value: str | None = None
    response_values: list[str] | None = None


class AnamnesisResponsesSubmit(BaseModel):
    responses: list[ResponseItem]
    complete: bool = False


class AnamnesisAssessmentRead(BaseModel):
    anamnesis_id: str
    patient_id: UUID
    submitted_by: UUID | None
    taken_by: str
    cycle_id: UUID | None
    version: int
    status: str
    completed_at: datetime | None
    created_at: datetime


class AnamnesisResponseRead(BaseModel):
    response_id: str
    anamnesis_id: str
    question_id: str
    response_value: str | None
    response_values: list[str] | None
