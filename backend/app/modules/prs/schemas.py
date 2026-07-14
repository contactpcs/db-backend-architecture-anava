from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PrsOptionRead(BaseModel):
    option_id: str
    value: str
    label: str
    points: float | None = None
    display_order: int


class PrsQuestionRead(BaseModel):
    question_id: str
    question_text: str
    answer_type: str
    min_value: float | None = None
    max_value: float | None = None
    is_required: bool
    display_order: int
    question_index: int = 0
    options: list[PrsOptionRead] = []


class PatientScaleAssignmentCreate(BaseModel):
    patient_id: UUID
    scale_id: str
    disease_id: str
    assessment_stage: str = Field(pattern="^(general_registration|main_clinical|followup)$")
    assignment_reason: str = Field(default="auto_disease_match", pattern="^(auto_disease_match|ca_selected|doctor_override)$")


class PatientScaleAssignmentRead(BaseModel):
    psa_id: UUID
    patient_id: UUID
    scale_id: str
    disease_id: str | None = None
    assessment_stage: str
    assigned_by: UUID
    assignment_reason: str | None
    is_active: bool
    created_at: datetime


class AssessmentInstanceCreate(BaseModel):
    patient_id: UUID
    disease_id: str
    assessment_stage: str = Field(pattern="^(general_registration|main_clinical|followup)$")
    session_id: UUID | None = None
    cycle_id: UUID | None = None


class AssessmentInstanceRead(BaseModel):
    instance_id: str
    disease_id: str
    patient_id: UUID
    session_id: UUID | None
    cycle_id: UUID | None
    assessment_stage: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    final_result: str | None


class ResponseSubmitItem(BaseModel):
    question_id: str
    given_response: str


class ResponsesSubmit(BaseModel):
    responses: list[ResponseSubmitItem]
    finalize_scale_id: str | None = None  # if set, compute+store the scale score after saving responses


class ResponseRead(BaseModel):
    response_id: str
    instance_id: str
    question_id: str
    given_response: str | None
    response_value: float | None


class AssessmentStartScaleRead(BaseModel):
    scale_id: str
    scale_code: str
    scale_name: str
    is_completed: bool
    questions: list[PrsQuestionRead]


class AssessmentStartRead(BaseModel):
    """POST /prs-assessment-instances — composed for the caller in one round
    trip: which scales are assigned (patient_scale_assignments, not just the
    disease's default catalog — a doctor may have overridden the set), each
    scale's full question+option list, and whether an in-progress instance
    was resumed instead of a new one created."""

    instance_id: str
    is_resumed: bool
    scales: list[AssessmentStartScaleRead]
