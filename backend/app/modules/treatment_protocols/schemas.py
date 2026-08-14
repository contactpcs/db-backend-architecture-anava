"""Request/response models for the Treatment Protocol module.

Mirrors the 8-step wizard in Documents/anava_db_layer_stack.html:
  1 Device -> 2 Condition -> 3 Diagnosis -> 4 Placement
  -> 5 Dosing -> 6 Scales -> 7 Schedule -> 8 Review & Push

Steps 1-3 are catalogue reads (reference schema). Steps 4-6 are resolved
against the per-device placement/dosing tables. Step 7 is a preview that
touches nothing. Step 8 is the single write that creates the protocol and
its appointments.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

# The six device families the catalogue is split across. The wizard's
# per-device tables (tdcs_placements, rtms_dosing, ...) are keyed off this
# exact vocabulary, so it lives in one place rather than being re-spelled
# in every query.
MODALITIES = ("tDCS", "HD-tDCS", "taVNS", "TPS", "rTMS", "other")

# reference.neuromod_devices.modality -> the table-name stem for that
# device's placement/dosing tables and the FK column on treatment_protocols.
MODALITY_SLUG = {
    "tDCS": "tdcs",
    "HD-tDCS": "hd_tdcs",
    "taVNS": "tavns",
    "TPS": "tps",
    "rTMS": "rtms",
    "other": "other",
}

# Evidence levels, strongest first. Step 3 of the wizard ranks a
# multi-diagnosis selection by this to pick the driving suggestion.
EVIDENCE_RANK = {"A": 3, "B": 2, "C": 1}


# --------------------------------------------------------------------------
# Step 1 - Device catalogue
# --------------------------------------------------------------------------


class DeviceCompanyRead(BaseModel):
    company_id: UUID
    company_code: str
    company_name: str
    country: str | None = None
    is_active: bool


class DeviceRead(BaseModel):
    device_id: UUID
    device_code: str
    device_name: str
    model_number: str | None = None
    modality: str
    # 1 = selectable now (tDCS, HD-tDCS); 2 = catalogued but not yet enabled.
    # The wizard greys out phase-2 cards rather than hiding them.
    phase: int
    is_active: bool
    company_id: UUID | None = None
    company_name: str | None = None
    company_code: str | None = None
    # Populated only when the request passed clinic_id: how many units that
    # clinic owns. Null on an unfiltered catalogue read, where the question has
    # no answer. Not the same as clinic_device_schedules.capacity — that is how
    # many sessions may run at once and also depends on assistants on shift.
    clinic_quantity: int | None = None


# --------------------------------------------------------------------------
# Step 2 - Conditions
# --------------------------------------------------------------------------


class ConditionRead(BaseModel):
    condition_id: UUID
    condition_name: str
    display_order: int
    is_active: bool
    diagnosis_count: int = 0
    # Best (strongest) evidence level available for this condition across
    # all its dosing rows - drives the "Evidence A/B/C" chip on the card.
    evidence_level: str | None = None


# --------------------------------------------------------------------------
# Step 3 - Diagnosis codes
# --------------------------------------------------------------------------


class DiagnosisRead(BaseModel):
    diagnosis_id: UUID
    condition_id: UUID
    condition_name: str
    icd10_code: str
    icd10_description: str
    # Suggested montage for the diagnosis's condition, pre-formatted for the
    # table's "Suggested" column ("F3 -> F4"). Null when the selected device
    # has no catalogued placement for that condition.
    suggested_montage: str | None = None
    evidence_level: str | None = None


class ResolutionAlternate(BaseModel):
    condition_id: UUID
    condition_name: str
    evidence_level: str | None = None
    placement_summary: str | None = None
    placement_id: UUID | None = None
    dosing_id: UUID | None = None


class DiagnosisResolution(BaseModel):
    """Step 3's ranked outcome. The wizard applies the winner automatically
    and offers the rest as one-click alternates."""

    driving_condition_id: UUID | None = None
    driving_condition_name: str | None = None
    evidence_level: str | None = None
    placement_id: UUID | None = None
    placement_summary: str | None = None
    dosing_id: UUID | None = None
    suggested_dosing: dict | None = None
    suggested_scales: list[str] = Field(default_factory=list)
    alternates: list[ResolutionAlternate] = Field(default_factory=list)
    note: str | None = None


# --------------------------------------------------------------------------
# Step 4 - Placement
# --------------------------------------------------------------------------


class PlacementRead(BaseModel):
    """One row from whichever per-device placement table applies.

    The six tables genuinely differ in shape (tDCS has anode/cathode sites,
    rTMS has a coil target and type, TPS has an anatomical region), so this
    carries the union and leaves the irrelevant fields null rather than
    pretending they share a column set.
    """

    placement_id: UUID
    condition_id: UUID
    device_id: UUID
    modality: str
    montage_label: str
    is_active: bool
    # tDCS / HD-tDCS
    anode_site: str | None = None
    cathode_site: str | None = None
    return_sites: list[str] | None = None
    # taVNS
    ear_side: str | None = None
    auricular_site: str | None = None
    # TPS / rTMS
    target_region: str | None = None
    hemisphere: str | None = None
    coil_target: str | None = None
    coil_type: str | None = None
    # other
    placement_details: dict | None = None
    summary: str | None = None


class ElectrodeValidationRequest(BaseModel):
    """Step 4 validates a doctor's custom montage before they leave the step.

    The electrode rule is a property of the device, per the wizard's own
    note: tDCS = exactly 1 anode + 1 cathode; HD-tDCS = 1 anode + up to 4
    return cathodes.
    """

    device_id: UUID
    anode_site: str | None = None
    cathode_sites: list[str] = Field(default_factory=list)


class ElectrodeValidationResult(BaseModel):
    valid: bool
    modality: str
    max_cathodes: int
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Step 5 - Dosing
# --------------------------------------------------------------------------


class DosingRead(BaseModel):
    """Union across the six dosing tables, same reasoning as PlacementRead."""

    dosing_id: UUID
    condition_id: UUID
    device_id: UUID
    modality: str
    placement_id: UUID | None = None
    evidence_level: str
    num_sessions_text: str | None = None
    notes: str | None = None
    is_active: bool
    # tDCS
    current_ma_min: Decimal | None = None
    current_ma_max: Decimal | None = None
    # HD-tDCS
    total_current_ma: Decimal | None = None
    per_return_current_ma: Decimal | None = None
    # shared
    session_duration_min: int | None = None
    sessions_per_day: int | None = None
    # taVNS
    intensity_ma: Decimal | None = None
    pulse_width_us: int | None = None
    duty_cycle_on_sec: int | None = None
    duty_cycle_off_sec: int | None = None
    # TPS
    energy_mj: Decimal | None = None
    pulses_per_session: int | None = None
    pulse_rate_hz: Decimal | None = None
    # rTMS
    frequency_hz: Decimal | None = None
    pct_motor_threshold: Decimal | None = None
    train_count: int | None = None
    pulses_per_train: int | None = None
    inter_train_interval_sec: Decimal | None = None
    # other
    dose_details: dict | None = None


# --------------------------------------------------------------------------
# Step 6 - Scales
# --------------------------------------------------------------------------


class ScaleRead(BaseModel):
    scale_id: UUID
    scale_code: str
    scale_name: str
    # Bridge into the PRS questionnaire engine. Nullable: not every
    # recommended scale is built as a PRS scale.
    prs_scale_id: str | None = None
    display_order: int = 0


# --------------------------------------------------------------------------
# Step 7 - Schedule preview
# --------------------------------------------------------------------------


class SchedulePreviewRequest(BaseModel):
    start_date: date
    session_count: int = Field(ge=1, le=90)
    sessions_per_week: int = Field(default=5, ge=1, le=7)
    follow_up_every_n: int | None = Field(default=None, ge=1, le=90)
    # Days the generator should skip (clinic holidays, patient unavailability)
    # and extra one-off dates the doctor painted onto the calendar.
    skip_dates: list[date] = Field(default_factory=list)
    extra_dates: list[date] = Field(default_factory=list)

    @model_validator(mode="after")
    def _follow_up_within_course(self):
        if self.follow_up_every_n is not None and self.follow_up_every_n > self.session_count:
            raise ValueError("follow_up_every_n cannot exceed session_count")
        return self


class ScheduledSession(BaseModel):
    session_number: int
    planned_date: date


class ScheduledFollowUp(BaseModel):
    after_session_number: int
    planned_date: date


class SchedulePreview(BaseModel):
    sessions: list[ScheduledSession]
    follow_ups: list[ScheduledFollowUp]
    session_count: int
    follow_up_count: int
    first_date: date | None = None
    last_date: date | None = None
    week_count: int = 0


# --------------------------------------------------------------------------
# Step 8 - Create / push
# --------------------------------------------------------------------------


class ProtocolScaleAssignment(BaseModel):
    scale_id: UUID | None = None
    # Free-text fallback for a scale the doctor typed that isn't catalogued.
    scale_code: str | None = None
    cadence: str = "At re-assessment only"

    @model_validator(mode="after")
    def _need_one_identifier(self):
        if self.scale_id is None and not (self.scale_code or "").strip():
            raise ValueError("Either scale_id or scale_code is required")
        return self


class ProtocolCreate(BaseModel):
    plan_id: UUID
    device_id: UUID
    # Exactly one placement and one dosing row, matching
    # chk_treatment_protocols_one_placement / _one_dosing. The service maps
    # these onto the correct per-device FK column from the device's modality,
    # so the caller never has to know which of the twelve columns to fill.
    placement_id: UUID
    dosing_id: UUID
    session_count: int = Field(ge=1, le=90)
    follow_up_every_n: int | None = Field(default=None, ge=1, le=90)
    start_date: date
    sessions_per_week: int = Field(default=5, ge=1, le=7)
    skip_dates: list[date] = Field(default_factory=list)
    extra_dates: list[date] = Field(default_factory=list)
    diagnosis_ids: list[UUID] = Field(default_factory=list)
    scales: list[ProtocolScaleAssignment] = Field(default_factory=list)
    # Per-patient deviations from the catalogue dose (reduced current for
    # tolerability, etc). The catalogue row stays the prescribed protocol;
    # this records the deviation from it.
    device_settings: dict = Field(default_factory=dict)
    notes: str | None = None

    @model_validator(mode="after")
    def _follow_up_within_course(self):
        if self.follow_up_every_n is not None and self.follow_up_every_n > self.session_count:
            raise ValueError("follow_up_every_n cannot exceed session_count")
        return self


class ProtocolUpdate(BaseModel):
    """Draft-only edits. Once a protocol is active its clinical parameters
    are frozen - see ProtocolService.update for why."""

    session_count: int | None = Field(default=None, ge=1, le=90)
    follow_up_every_n: int | None = Field(default=None, ge=1, le=90)
    device_settings: dict | None = None
    notes: str | None = None


class ProtocolRead(BaseModel):
    protocol_id: UUID
    plan_id: UUID
    device_id: UUID
    set_by: UUID
    session_count: int
    follow_up_every_n: int | None = None
    status: str
    device_settings: dict = Field(default_factory=dict)
    notes: str | None = None
    activated_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    # Hydrated for display
    device_name: str | None = None
    modality: str | None = None
    company_name: str | None = None
    patient_id: UUID | None = None
    patient_name: str | None = None
    doctor_id: UUID | None = None
    doctor_name: str | None = None
    clinic_id: UUID | None = None
    placement_id: UUID | None = None
    placement_summary: str | None = None
    dosing_id: UUID | None = None
    appointment_count: int = 0


class ProtocolDetail(ProtocolRead):
    placement: PlacementRead | None = None
    dosing: DosingRead | None = None
    sessions: list[ProtocolSessionRead] = Field(default_factory=list)
    follow_ups: list[ProtocolSessionRead] = Field(default_factory=list)


class ProtocolSessionRead(BaseModel):
    """A protocol-born appointments row.

    30_appointments_spine.sql made core.appointments the single spine for
    every clinic visit, so a prescribed device session is an appointment
    with appointment_type='device_session' and status='planned' - not a row
    in a separate table.
    """

    appointment_id: UUID
    appointment_type: str
    session_number: int | None = None
    appointment_date: date
    start_time: str | None = None
    end_time: str | None = None
    status: str
    doctor_id: UUID | None = None
    ca_id: UUID | None = None


class ProtocolPushResult(BaseModel):
    protocol_id: UUID
    status: str
    sessions_created: int
    follow_ups_created: int
    scales_assigned: int
    first_session_date: date | None = None
    last_session_date: date | None = None


# --------------------------------------------------------------------------
# PRS responses (two tables, by requirement)
# --------------------------------------------------------------------------


class DeviceSessionPrsCreate(BaseModel):
    appointment_id: UUID
    instance_id: str
    session_number: int = Field(ge=1)


class FollowUpPrsCreate(BaseModel):
    appointment_id: UUID
    instance_id: str
    after_session_number: int = Field(ge=1)


class PrsResponseRead(BaseModel):
    response_id: UUID
    appointment_id: UUID
    protocol_id: UUID
    patient_id: UUID
    instance_id: str
    session_number: int
    recorded_at: datetime
    kind: str


ProtocolDetail.model_rebuild()
