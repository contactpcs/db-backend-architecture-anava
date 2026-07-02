from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PatientScaleAssignmentCreate(BaseModel):
    patient_id: UUID
    scale_id: str
    assessment_stage: str = Field(pattern="^(general_registration|main_clinical|followup)$")
    assignment_reason: str = Field(default="auto_disease_match", pattern="^(auto_disease_match|ca_selected|doctor_override)$")


class PatientScaleAssignmentRead(BaseModel):
    psa_id: UUID
    patient_id: UUID
    scale_id: str
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
