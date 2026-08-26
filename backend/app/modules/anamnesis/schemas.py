from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AnamnesisOptionRead(BaseModel):
    option_id: str
    option_label: str
    option_value: str
    display_order: int


class AnamnesisQuestionRead(BaseModel):
    question_id: str
    type: str
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
    options: list[AnamnesisOptionRead] = []


class AnamnesisStart(BaseModel):
    taken_by: str = "patient"
    assessment_stage: str = Field(default="registration", pattern="^(registration|main)$")
    # The visit this anamnesis was captured/edited during, for the doctor
    # portal's per-visit bundle. Validated same-patient in the service.
    appointment_id: UUID | None = None


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
    version: int
    assessment_stage: str
    appointment_id: UUID | None = None
    status: str
    completed_at: datetime | None
    created_at: datetime


class AnamnesisResponseRead(BaseModel):
    response_id: str
    anamnesis_id: str
    question_id: str
    response_value: str | None
    response_values: list[str] | None
