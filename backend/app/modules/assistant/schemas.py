from datetime import date, datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Language = Literal["en", "hi", "mr"]
ConsentKind = Literal["assistant", "feedback"]


# -- consent ---------------------------------------------------------------------


class ConsentAccept(BaseModel):
    kind: ConsentKind
    language: Language = "en"


class ConsentState(BaseModel):
    kind: ConsentKind
    version: str
    accepted: bool
    accepted_at: datetime | None = None


class AssistantStatus(BaseModel):
    """What the app needs before opening the chat: whether to show the consent
    sheet and the Demo label, and today's remaining questions."""

    consent: ConsentState
    demo: bool
    ai_enabled: bool
    daily_limit: int
    used_today: int


# -- chat ------------------------------------------------------------------------


class AskRequest(BaseModel):
    conversation_id: UUID
    message: str = Field(min_length=1, max_length=500)
    language: Language = "en"
    input_mode: Literal["text", "voice", "chip"] = "text"


class AskReply(BaseModel):
    conversation_id: UUID
    reply: str
    answered_by: Literal["safety", "data", "faq", "ai", "fallback", "limit"]
    intent: str | None = None
    # App screen key for the button shown with the reply (pay, reschedule, book,
    # payments, sessions, appointment_detail, call_clinic, clinic_map, ...).
    action: str | None = None
    safety_screen: Literal["S0", "S1", "S2", "S3", "S4"] | None = None
    # Show the small "Demo assistant" line under AI-written replies.
    demo: bool = False
    remaining_today: int


class MessageRead(BaseModel):
    message_id: UUID
    conversation_id: UUID
    role: Literal["patient", "assistant"]
    body: str
    language: Language
    answered_by: str | None = None
    intent: str | None = None
    safety_screen: str | None = None
    created_at: datetime


# -- feedback --------------------------------------------------------------------


class PendingFeedback(BaseModel):
    appointment_id: UUID
    appointment_type: str
    appointment_date: date
    start_time: time | None = None
    session_number: int | None = None
    device_name: str | None = None
    # Which question set to show: device = F1-F11, consult = F1, F6, C1-C3, F10, F11.
    form: Literal["device", "consult"]


SideEffect = Literal["tingling", "itching", "burning", "redness", "headache", "dizziness", "tiredness", "nausea", "concentration", "other"]


class FeedbackSubmit(BaseModel):
    """One completed form. Every answer is optional (each question can be
    skipped). status='skipped' records "Skip all" and must carry no answers."""

    status: Literal["submitted", "skipped"] = "submitted"
    form_version: str = Field(min_length=1, max_length=40)
    language: Language = "en"
    input_mode: Literal["tap", "voice", "mixed"] = "tap"
    overall_rating: int | None = Field(None, ge=1, le=5)  # F1
    comfort: Literal["comfortable", "tolerable", "uncomfortable"] | None = None  # F2
    felt_after: Literal["better", "no_change", "worse"] | None = None  # F3
    side_effects: list[SideEffect] = Field(default_factory=list, max_length=10)  # F4
    side_effect_severity: Literal["mild", "moderate", "severe"] | None = None  # F4a
    side_effect_ongoing: bool | None = None  # F4b
    next_intensity: Literal["lower", "same", "higher", "not_sure"] | None = None  # F5
    wait_band: Literal["on_time", "under_15", "15_30", "over_30"] | None = None  # F6
    device_ok: bool | None = None  # F7
    device_issue: Literal["fit", "stopped", "pads", "other"] | None = None  # F7a
    staff_rating: int | None = Field(None, ge=1, le=5)  # F8
    facility_rating: int | None = Field(None, ge=1, le=5)  # F9
    explained_rating: int | None = Field(None, ge=1, le=5)  # C1
    questions_answered: Literal["yes", "partly", "no"] | None = None  # C2
    booking_rating: int | None = Field(None, ge=1, le=5)  # C3
    nps: int | None = Field(None, ge=0, le=10)  # F10
    comment: str | None = Field(None, max_length=2000)  # F11


class FeedbackResult(BaseModel):
    feedback_id: UUID
    status: Literal["submitted", "skipped"]
    # Shown by the app before the thank-you message, when not null.
    safety_screen: Literal["S0", "S1", "S2", "S3", "S4"] | None = None
    safety_message: str | None = None
