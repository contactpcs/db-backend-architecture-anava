"""Request/response models for the Device Session module.

Field names match SQL/v1/56_device_session_records.sql exactly — this is a
thin typed layer over that schema, not a re-interpretation of it. Fixed
vocabularies (symptom, severity, event_type, sos_type, delivery_mode, ...)
are Literal[...] types copied verbatim from that file's CHECK constraints,
so a value ruled out at the API boundary is ruled out for the same reason
the database would reject it.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------
# Fixed vocabularies — copied from 53's CHECK constraints
# --------------------------------------------------------------------------

# chk_device_sessions_status
SessionStatus = Literal["not_started", "in_progress", "paused", "completed", "stopped_early"]

# chk_device_sessions_pause_stop_reason
PauseStopReason = Literal["patient_discomfort", "adverse_event", "device_setup_issue", "device_glitch", "power_outage", "other"]

# chk_dss_symptom
Symptom = Literal["tingling", "itching", "burning", "headache", "fatigue", "sleepiness", "dizziness", "skin_redness", "nausea", "other"]

# chk_dss_severity / chk_dsae_severity
Severity = Literal["mild", "moderate", "severe"]

# chk_dsae_event_type
AdverseEventType = Literal["sharp_burning_pain", "skin_burn_lesion", "dizziness", "severe_headache", "nausea_vomiting", "other"]

# chk_dss2_delivery_mode
DeliveryMode = Literal["ca_administered", "patient_app"]

# chk_dss2_status
ScaleStatus = Literal["pending", "in_progress", "completed", "frozen"]

# chk_dsm_media_type
MediaType = Literal["photo", "video"]

# chk_dsse_sos_type
SosType = Literal["discomfort", "unwell", "other", "emergency"]

# chk_tvns_session_settings_wavelength / _pattern (90_tvns_device.sql)
Wavelength = Literal["alternant", "biphasic"]
StimPattern = Literal["continuous", "modulation", "intermittent"]

# tVNS device dial steps, enforced here rather than as a SQL CHECK because
# the step size changes partway through the range (see
# 90_tvns_device.sql's column comments) — a single allow-list next to the
# UI copy that renders it is clearer than an IN-list with ~40 values.
TVNS_FREQUENCY_HZ_HZ = tuple(range(1, 100)) + tuple(range(100, 1001, 100))
TVNS_PULSE_WIDTH_US = tuple(range(50, 300, 10)) + tuple(range(300, 501, 50))
TVNS_DURATION_MIN = (5, 10, 15, 20, 30, 40, 50, 60) + tuple(range(70, 241, 10))


# --------------------------------------------------------------------------
# device_sessions — header
# --------------------------------------------------------------------------


class ChecklistUpdate(BaseModel):
    """The CA's pre-session safety checklist. All fields optional — a CA
    fills the form incrementally, and each POST /checklist call upserts
    whatever fields it carries onto the header row (lazily created on the
    first write)."""

    payment_verified: bool | None = None
    payment_override_reason: str | None = None
    device_brand: str | None = None
    device_serial_number: str | None = None
    actual_intensity_ma: Decimal | None = Field(default=None, max_digits=4, decimal_places=2)
    intensity_deviates: bool | None = None
    intensity_deviation_reason: str | None = None
    actual_duration_min: int | None = None
    duration_deviates: bool | None = None
    duration_deviation_reason: str | None = None
    actual_ramp_up_sec: int | None = None
    ramp_up_deviates: bool | None = None
    ramp_up_deviation_reason: str | None = None
    actual_ramp_down_sec: int | None = None
    ramp_down_deviates: bool | None = None
    ramp_down_deviation_reason: str | None = None
    montage_verified: bool | None = None
    contraindication_checklist: dict | None = None
    patient_consent: dict | None = None
    ca_declaration: dict | None = None


class DeviceFitUpdate(BaseModel):
    """Live-session device-fit checklist plus the impedance reading."""

    device_fit_checklist: dict = Field(default_factory=dict)
    impedance_kohm: Decimal | None = Field(default=None, max_digits=6, decimal_places=2)


class PauseStopRequest(BaseModel):
    pause_stop_reason: PauseStopReason
    pause_stop_reason_detail: str | None = None


class CompleteSessionRequest(BaseModel):
    """Required only when completing before the 75% elapsed-duration
    threshold (see device_sessions/service.py complete()); omit entirely
    for a normal full-duration completion."""

    early_completion_override_reason: str | None = None


class NextSessionConfirmation(BaseModel):
    """Shape stored as device_sessions.next_session_confirmation JSONB."""

    patient_confirmed: bool
    requested_date: str | None = None
    requested_slot: str | None = None
    note: str | None = None


class TvnsSessionSettingsCreate(BaseModel):
    """tVNS device settings for one session (core.tvns_session_settings).
    Frequency/pulse-width/duration are each a fixed step list rather than a
    free range — see TVNS_FREQUENCY_HZ_HZ / TVNS_PULSE_WIDTH_US /
    TVNS_DURATION_MIN above for why the step size isn't uniform."""

    wavelength: Wavelength
    pattern: StimPattern
    strength_pct: int = Field(ge=0, le=100)
    frequency_hz: int
    pulse_width_us: int
    duration_min: int

    @field_validator("frequency_hz")
    @classmethod
    def _validate_frequency(cls, v: int) -> int:
        if v not in TVNS_FREQUENCY_HZ_HZ:
            raise ValueError(f"frequency_hz must be one of the device's allowed steps, got {v}")
        return v

    @field_validator("pulse_width_us")
    @classmethod
    def _validate_pulse_width(cls, v: int) -> int:
        if v not in TVNS_PULSE_WIDTH_US:
            raise ValueError(f"pulse_width_us must be one of the device's allowed steps, got {v}")
        return v

    @field_validator("duration_min")
    @classmethod
    def _validate_duration(cls, v: int) -> int:
        if v not in TVNS_DURATION_MIN:
            raise ValueError(f"duration_min must be one of the device's allowed presets, got {v}")
        return v


class TvnsSessionSettingsRead(BaseModel):
    tvns_session_setting_id: UUID
    device_session_record_id: UUID
    wavelength: str
    pattern: str
    strength_pct: int
    frequency_hz: Decimal
    pulse_width_us: int
    duration_min: Decimal
    created_at: datetime
    updated_at: datetime


class DeviceInfo(BaseModel):
    """GET /device-sessions/{id}/device-info — device name (always, from
    protocol_plan.device_id) and pinned unit id/serial (only if the protocol
    pinned one). Lets the live-session screen show the actual device instead
    of a manual-entry field, resolvable before any device_sessions header
    row exists."""

    device_name: str
    device_unit_id: UUID | None = None
    device_unit_serial_number: str | None = None


class DeviceSessionRead(BaseModel):
    device_session_record_id: UUID
    appointment_id: UUID
    protocol_id: UUID
    # Denormalised from appointments.ca_id/executor_role at session start —
    # who actually ran this session, Doctor or Clinical Assistant.
    performed_by_id: UUID | None = None
    performed_by_role: str | None = None

    payment_verified: bool
    payment_override_reason: str | None = None
    device_brand: str | None = None
    device_serial_number: str | None = None
    actual_intensity_ma: Decimal | None = None
    intensity_deviates: bool
    intensity_deviation_reason: str | None = None
    actual_duration_min: int | None = None
    duration_deviates: bool
    duration_deviation_reason: str | None = None
    actual_ramp_up_sec: int | None = None
    ramp_up_deviates: bool
    ramp_up_deviation_reason: str | None = None
    actual_ramp_down_sec: int | None = None
    ramp_down_deviates: bool
    ramp_down_deviation_reason: str | None = None
    montage_verified: bool
    contraindication_checklist: dict = Field(default_factory=dict)
    patient_consent: dict | None = None
    ca_declaration: dict | None = None

    session_status: SessionStatus
    device_fit_checklist: dict = Field(default_factory=dict)
    impedance_kohm: Decimal | None = None
    started_at: datetime | None = None
    paused_at: datetime | None = None
    resumed_at: datetime | None = None
    stopped_at: datetime | None = None
    completed_at: datetime | None = None
    pause_stop_reason: str | None = None
    pause_stop_reason_detail: str | None = None
    early_completion_override_reason: str | None = None
    next_session_confirmation: dict | None = None

    created_by: UUID
    created_at: datetime
    updated_at: datetime


class DeviceSessionDetail(DeviceSessionRead):
    """The CA "return to session" resume view / summary screen — header
    plus every hydrated child list."""

    symptoms: list[SymptomRead] = Field(default_factory=list)
    adverse_events: list[AdverseEventRead] = Field(default_factory=list)
    notes: list[NoteRead] = Field(default_factory=list)
    activities: list[ActivityRead] = Field(default_factory=list)
    scales: list[SessionScaleRead] = Field(default_factory=list)
    feedback: FeedbackRead | None = None
    media: list[MediaRead] = Field(default_factory=list)
    events: list[EventRead] = Field(default_factory=list)
    sos_events: list[SosEventRead] = Field(default_factory=list)
    tvns_settings: TvnsSessionSettingsRead | None = None


# --------------------------------------------------------------------------
# device_session_symptoms
# --------------------------------------------------------------------------


class SymptomCreate(BaseModel):
    symptom: Symptom
    severity: Severity
    note: str | None = None


class SymptomRead(BaseModel):
    symptom_record_id: UUID
    device_session_record_id: UUID
    symptom: str
    severity: str
    note: str | None = None
    recorded_by: UUID
    recorded_at: datetime


# --------------------------------------------------------------------------
# device_session_adverse_events
# --------------------------------------------------------------------------


class AdverseEventCreate(BaseModel):
    event_type: AdverseEventType
    severity: Severity
    description: str = Field(min_length=1)
    action_taken: str | None = None


class AdverseEventRead(BaseModel):
    ae_record_id: UUID
    device_session_record_id: UUID
    event_type: str
    severity: str
    description: str
    action_taken: str | None = None
    recorded_by: UUID
    recorded_at: datetime


# --------------------------------------------------------------------------
# device_session_notes
# --------------------------------------------------------------------------


class NoteCreate(BaseModel):
    note_text: str = Field(min_length=1)


class NoteRead(BaseModel):
    note_id: UUID
    device_session_record_id: UUID
    note_text: str
    recorded_by: UUID
    recorded_at: datetime


# --------------------------------------------------------------------------
# device_session_activities
# --------------------------------------------------------------------------


class ActivityCreate(BaseModel):
    activities: list[str] = Field(min_length=1)
    free_text: str | None = None
    note: str | None = None


class ActivityRead(BaseModel):
    activity_record_id: UUID
    device_session_record_id: UUID
    activities: list[str]
    free_text: str | None = None
    note: str | None = None
    recorded_by: UUID
    recorded_at: datetime


# --------------------------------------------------------------------------
# device_session_scales
# --------------------------------------------------------------------------


class ScaleDeliveryUpdate(BaseModel):
    delivery_mode: DeliveryMode


class ScaleCompleteRequest(BaseModel):
    prs_instance_id: str


class SessionScaleRead(BaseModel):
    session_scale_id: UUID
    device_session_record_id: UUID
    protocol_scale_id: UUID
    delivery_mode: str
    prs_instance_id: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    # Hydrated from core.protocol_scales for display
    scale_code: str | None = None
    scale_name: str | None = None
    # protocol_scales.prs_scale_id — the PRS questionnaire engine's own
    # scale identity (reference.prs_scales.scale_id, TEXT), NOT
    # protocol_scales.scale_id (a different, UUID-keyed catalogue,
    # reference.neuromod_scales). This is what prs_scale_results.scale_id
    # matches, so it's what a caller needs to filter a disease-scoped
    # instance's results down to just this one scale. Null when the
    # protocol's scale isn't PRS-backed.
    scale_id: str | None = None


class PendingPatientScaleRead(SessionScaleRead):
    """One row from list_pending_for_patient — same shape as SessionScaleRead
    plus enough appointment context (GET /me/device-session-scales, patient
    dashboard) for a patient to see and route to it without already knowing
    which appointment it belongs to."""

    appointment_id: UUID
    appointment_date: date
    session_number: int | None = None


# --------------------------------------------------------------------------
# device_session_feedback
# --------------------------------------------------------------------------

Comfort = Literal["comfortable", "tolerable", "uncomfortable"]
FeltAfter = Literal["better", "no_change", "worse"]
NextIntensity = Literal["decrease", "keep_same", "increase"]


class FeedbackAnswers(BaseModel):
    comfort: Comfort
    felt_after: FeltAfter
    next_intensity: NextIntensity


class FeedbackCreate(BaseModel):
    answers: FeedbackAnswers
    quote: str | None = None


class FeedbackRead(BaseModel):
    feedback_id: UUID
    device_session_record_id: UUID
    answers: dict
    quote: str | None = None
    recorded_by: UUID
    recorded_at: datetime


# --------------------------------------------------------------------------
# device_session_media
# --------------------------------------------------------------------------


class MediaConsentConfirm(BaseModel):
    """No fields — confirming consent is an action, recorded as a
    device_session_events row (event_type='media_consent_confirmed'), not a
    stored value. See chk_dsm_consent_required."""


class MediaCreate(BaseModel):
    media_type: MediaType
    file_key: str = Field(min_length=1)


class MediaRead(BaseModel):
    media_id: UUID
    device_session_record_id: UUID
    recording_consent_confirmed: bool
    media_type: str
    file_key: str
    captured_at: datetime
    uploaded_by: UUID


# --------------------------------------------------------------------------
# device_session_events — audit trail
# --------------------------------------------------------------------------


class EventRead(BaseModel):
    event_id: UUID
    device_session_record_id: UUID
    event_type: str
    payload: dict = Field(default_factory=dict)
    actor_id: UUID
    actor_role: str
    occurred_at: datetime


# --------------------------------------------------------------------------
# device_session_sos_events
# --------------------------------------------------------------------------


class SosEventCreate(BaseModel):
    sos_type: SosType
    note: str | None = None


class SosEventRead(BaseModel):
    sos_id: UUID
    device_session_record_id: UUID
    sos_type: str
    note: str | None = None
    raised_by: UUID
    raised_at: datetime
    acknowledged_by: UUID | None = None
    acknowledged_at: datetime | None = None


DeviceSessionDetail.model_rebuild()
