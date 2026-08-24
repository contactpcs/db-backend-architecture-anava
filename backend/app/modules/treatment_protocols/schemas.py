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

from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

# The six device families the catalogue is split across. The wizard's
# per-device tables (tdcs_placements, rtms_dosing, ...) are keyed off this
# exact vocabulary, so it lives in one place rather than being re-spelled
# in every query.
MODALITIES = ("tDCS", "HD-tDCS", "taVNS", "TPS", "rTMS", "other")

# reference.neuromod_devices.modality -> the table-name stem for that
# device's placement/dosing tables and the FK column on protocol_plan.
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

# The only cadences core.protocol_scales accepts
# (chk_protocol_scales_cadence, 38). Stored values are these five; the UI
# labels below are accepted on input and normalised, because the prototype's
# dropdown says "At re-assessment only" where the schema says
# 'per_checkpoint'. Sending the label straight through raises 23514.
SCALE_CADENCES = ("baseline", "per_checkpoint", "weekly", "fortnightly", "end_of_treatment")

# Wizard label (and a few obvious spellings) -> stored cadence. Keys are
# compared lower-cased and whitespace-collapsed, so casing in the UI is not
# load-bearing.
CADENCE_ALIASES = {
    "baseline": "baseline",
    "baseline only": "baseline",
    "at baseline": "baseline",
    "at re-assessment only": "per_checkpoint",
    "at reassessment only": "per_checkpoint",
    "re-assessment": "per_checkpoint",
    "per checkpoint": "per_checkpoint",
    "per_checkpoint": "per_checkpoint",
    "every follow-up": "per_checkpoint",
    "weekly": "weekly",
    "every week": "weekly",
    "fortnightly": "fortnightly",
    "every 2 weeks": "fortnightly",
    "biweekly": "fortnightly",
    "end of treatment": "end_of_treatment",
    "end_of_treatment": "end_of_treatment",
    "at discharge": "end_of_treatment",
    # The remaining labels the wizard's CADENCE_OPTIONS offers. Each maps to
    # the nearest cadence the schema actually stores; where the mapping loses
    # information that is called out, because the alternative is a 422 on a
    # dropdown value the UI presents as valid.
    #
    # "After every session" is per_checkpoint: the schema has no
    # release-on-every-session cadence, and per_checkpoint (every follow-up)
    # is the closest thing that exists. If genuinely per-session PRS is
    # required, that is a new cadence value in chk_protocol_scales_cadence,
    # not a mapping.
    "after every session": "per_checkpoint",
    # Two release points; only the later one is representable, so this stores
    # end_of_treatment. Add a separate baseline row to capture both.
    "baseline & end only": "end_of_treatment",
    "baseline and end only": "end_of_treatment",
    # Nearest stored cadence to monthly is fortnightly.
    "monthly": "fortnightly",
}

# core.protocol_scales.answered_by CHECK.
ANSWERED_BY = ("patient", "clinician", "either")


def normalise_cadence(value: str) -> str:
    """UI label -> the value chk_protocol_scales_cadence accepts.

    Raises rather than guessing: a scale silently filed under the wrong
    cadence is released to the patient on the wrong trigger, which is worse
    than a 422 telling the caller exactly which cadences exist.
    """
    key = " ".join((value or "").strip().lower().split())
    if key in CADENCE_ALIASES:
        return CADENCE_ALIASES[key]
    raise ValueError(f"Unknown cadence {value!r}. Expected one of: {', '.join(SCALE_CADENCES)}")


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
    """A scale from the PRS catalogue (51).

    scale_id is reference.prs_scales.scale_id — a TEXT key like 'GAD-7/2026',
    not a UUID. The wizard used to read reference.neuromod_scales, which
    overlapped the PRS catalogue by 5 of 13 rows: PHQ-9, the default
    depression instrument, was never a PRS scale at all, so prescribing it
    queued a questionnaire the engine could not render.
    """

    scale_id: str
    scale_code: str
    scale_name: str
    is_common_scale: bool = False
    applicable_for: str | None = None
    is_required: bool = False
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
    """One row of wizard step 6.

    scale_id is required because core.protocol_scales.scale_id is NOT NULL
    with an FK into reference.neuromod_scales. There is deliberately no
    free-text fallback: a scale the catalogue does not know cannot be released
    to the patient as a PRS task, so accepting the name and storing nothing
    would be the same silent discard this module already had. An uncatalogued
    scale is added to the catalogue first.
    """

    # reference.prs_scales.scale_id — TEXT ('GAD-7/2026'), not a UUID. See
    # ScaleRead.
    scale_id: str
    # Accepts the wizard's own labels ("At re-assessment only") and stores the
    # value chk_protocol_scales_cadence allows.
    cadence: str = "per_checkpoint"
    # How long the patient has once it is released. None = no deadline.
    window_days: int | None = Field(default=None, ge=1)
    answered_by: str = "patient"
    display_order: int = 0

    @model_validator(mode="after")
    def _normalise(self):
        object.__setattr__(self, "cadence", normalise_cadence(self.cadence))
        if self.answered_by not in ANSWERED_BY:
            raise ValueError(f"answered_by must be one of: {', '.join(ANSWERED_BY)}")
        return self


class ProtocolInstanceCreate(BaseModel):
    """Opens an episode of care directly (58).

    No treatment_cycles any more — patient/doctor/clinic are carried on the
    instance itself, not resolved from a separate cycle row. A treatment plan
    is the clinical picture (anamnesis, history, assessments) and is not a
    prerequisite for prescribing a device course.
    """

    patient_id: UUID
    doctor_id: UUID
    ca_id: UUID | None = None
    clinic_id: UUID
    instance_type: str = "initial"
    notes: str | None = None


class ProtocolInstanceRead(BaseModel):
    instance_id: UUID
    patient_id: UUID
    doctor_id: UUID
    ca_id: UUID | None = None
    clinic_id: UUID
    instance_type: str
    created_by: UUID
    instance_number: int
    status: str
    notes: str | None = None
    created_at: datetime
    updated_at: datetime
    # Hydrated for display
    patient_name: str | None = None
    created_by_name: str | None = None
    protocol_count: int = 0


class ProtocolConditionAssignment(BaseModel):
    """One row of wizard step 2.

    Either a catalogue condition or free text from the "Other condition" box,
    never both — chk_protocol_conditions_shape enforces
    num_nonnulls(condition_id, other_text) = 1.
    """

    condition_id: UUID | None = None
    other_text: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        text_set = bool((self.other_text or "").strip())
        if self.condition_id is not None and text_set:
            raise ValueError("Provide either condition_id or other_text, not both")
        if self.condition_id is None and not text_set:
            raise ValueError("Provide either condition_id or other_text")
        if text_set:
            object.__setattr__(self, "other_text", self.other_text.strip())
        return self


class ProtocolCreate(BaseModel):
    # 45 re-parented the protocol onto protocol_instances; 47 made instance_id
    # the only parent; 48 dropped plan_id entirely — protocol_plan no longer
    # references treatment_plans at all.
    instance_id: UUID
    device_id: UUID
    # Exactly one of placement_id (catalogue) or custom_montage_id (a
    # doctor-authored core.protocol_custom_montages row, 38/54) is set,
    # matching chk_protocol_plan_one_placement. The service maps a
    # catalogue placement_id onto the correct per-device FK column from the
    # device's modality, so the caller never has to know which of the six
    # columns to fill; a custom_montage_id needs no such mapping - there is
    # only one column for it.
    #
    # dosing_id is required when placement_id is used (chk_protocol_plan_
    # one_dosing, unchanged) and must be absent when custom_montage_id is
    # used (chk_protocol_plan_dosing_requires_catalogue_placement, 54) - a
    # custom montage has no catalogued dosing row to legitimately point at.
    # The prescription still gets fully captured either way, through
    # prescribed_current_ma/prescribed_duration_min/ramp_seconds below,
    # which have always been independent of dosing_id (39) - dosing_id is
    # provenance ("which catalogued dose was this nominally based on"), not
    # the prescription itself.
    placement_id: UUID | None = None
    dosing_id: UUID | None = None
    custom_montage_id: UUID | None = None
    session_count: int = Field(ge=1, le=90)
    follow_up_every_n: int | None = Field(default=None, ge=1, le=90)
    start_date: date
    sessions_per_week: int = Field(default=5, ge=1, le=7)
    skip_dates: list[date] = Field(default_factory=list)
    extra_dates: list[date] = Field(default_factory=list)
    conditions: list[ProtocolConditionAssignment] = Field(default_factory=list)
    diagnosis_ids: list[UUID] = Field(default_factory=list)
    scales: list[ProtocolScaleAssignment] = Field(default_factory=list)
    # Step 1: which consultation authored this. Provenance only - sessions are
    # still found through appointments.protocol_id, never by walking back
    # through this column.
    authored_in_appointment_id: UUID | None = None
    # Step 5, the prescribed dose. Optional at create so a half-finished draft
    # can be saved, but fn_check_protocol_prescription_complete (39) refuses to
    # let a protocol reach 'active' without current, duration and cadence -
    # a NULL current at the bedside is an unanswerable question, not a blank.
    prescribed_current_ma: Decimal | None = Field(default=None, gt=0, le=4)
    prescribed_duration_min: int | None = Field(default=None, gt=0, le=120)
    ramp_seconds: int = Field(default=30, ge=0, le=120)
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

    @model_validator(mode="after")
    def _cadence_is_storable(self):
        # chk_protocol_plan_sessions_per_week (39) allows exactly these.
        # The schedule generator accepts 4 and 6 as well, so a value that
        # previews fine would fail at INSERT - caught here instead.
        if self.sessions_per_week not in (1, 2, 3, 5, 7):
            raise ValueError("sessions_per_week must be one of 1, 2, 3, 5, 7 (1x/2x/3x/5x/Daily)")
        return self

    @model_validator(mode="after")
    def _exactly_one_placement_source(self):
        # Mirrors chk_protocol_plan_one_placement (54) as a readable 422
        # instead of a raised constraint - same reasoning ProtocolService.
        # create()'s pre-flight checks already apply to placement/dosing
        # device-mismatch below.
        if bool(self.placement_id) == bool(self.custom_montage_id):
            raise ValueError("Provide exactly one of placement_id or custom_montage_id")
        # Mirrors chk_protocol_plan_dosing_requires_catalogue_placement (54):
        # dosing_id iff placement_id.
        if bool(self.placement_id) != bool(self.dosing_id):
            raise ValueError("dosing_id is required with placement_id, and not allowed with custom_montage_id")
        return self


class ProtocolUpdate(BaseModel):
    """Draft-only edits. Once a protocol is active its clinical parameters
    are frozen - see ProtocolService.update for why.

    The prescription fields are editable here because a draft saved from an
    incomplete step 5 has to be completable; activation then enforces that
    they are all present.
    """

    session_count: int | None = Field(default=None, ge=1, le=90)
    follow_up_every_n: int | None = Field(default=None, ge=1, le=90)
    prescribed_current_ma: Decimal | None = Field(default=None, gt=0, le=4)
    prescribed_duration_min: int | None = Field(default=None, gt=0, le=120)
    ramp_seconds: int | None = Field(default=None, ge=0, le=120)
    sessions_per_week: int | None = None
    device_settings: dict | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _cadence_is_storable(self):
        if self.sessions_per_week is not None and self.sessions_per_week not in (1, 2, 3, 5, 7):
            raise ValueError("sessions_per_week must be one of 1, 2, 3, 5, 7 (1x/2x/3x/5x/Daily)")
        return self


class ProtocolRead(BaseModel):
    protocol_id: UUID
    instance_id: UUID
    instance_number: int | None = None
    instance_status: str | None = None
    device_id: UUID
    set_by: UUID
    session_count: int
    follow_up_every_n: int | None = None
    status: str
    device_settings: dict = Field(default_factory=dict)
    notes: str | None = None
    # 39
    authored_in_appointment_id: UUID | None = None
    prescribed_current_ma: Decimal | None = None
    prescribed_duration_min: int | None = None
    ramp_seconds: int | None = None
    sessions_per_week: int | None = None
    supersedes_protocol_id: UUID | None = None
    activated_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    # Hydrated for display
    device_name: str | None = None
    modality: str | None = None
    company_name: str | None = None
    patient_id: UUID | None = None
    # patients.patient_id (public ID) — GET /patients/{id} and every other
    # patient-scoped route expect this, not patient_id above (profiles.id).
    patient_public_id: UUID | None = None
    patient_name: str | None = None
    doctor_id: UUID | None = None
    doctor_name: str | None = None
    clinic_id: UUID | None = None
    placement_id: UUID | None = None
    placement_summary: str | None = None
    dosing_id: UUID | None = None
    custom_montage_id: UUID | None = None
    appointment_count: int = 0


class ProtocolConditionRead(BaseModel):
    protocol_condition_id: UUID
    condition_id: UUID | None = None
    condition_name: str | None = None
    other_text: str | None = None


class ProtocolDiagnosisRead(BaseModel):
    protocol_diagnosis_id: UUID
    diagnosis_id: UUID
    icd10_code: str | None = None
    icd10_description: str | None = None
    condition_name: str | None = None


class ProtocolScaleRead(BaseModel):
    protocol_scale_id: UUID
    # Exactly one of these is set (chk_protocol_scales_one_catalogue, 51):
    # prs_scale_id for anything prescribed since, scale_id for rows written
    # against the old neuromod catalogue.
    scale_id: UUID | None = None
    prs_scale_id: str | None = None
    scale_code: str | None = None
    scale_name: str | None = None
    cadence: str
    window_days: int | None = None
    answered_by: str
    display_order: int = 0


class ProtocolDetail(ProtocolRead):
    placement: PlacementRead | None = None
    dosing: DosingRead | None = None
    # Hydrated when custom_montage_id is set instead of placement_id/dosing_id
    # (54) - mutually exclusive with placement/dosing above.
    custom_montage: CustomMontageRead | None = None
    conditions: list[ProtocolConditionRead] = Field(default_factory=list)
    diagnoses: list[ProtocolDiagnosisRead] = Field(default_factory=list)
    scales: list[ProtocolScaleRead] = Field(default_factory=list)
    sessions: list[ProtocolSessionRead] = Field(default_factory=list)
    follow_ups: list[ProtocolSessionRead] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Step 4 - Custom montages (core.protocol_custom_montages, 38)
# --------------------------------------------------------------------------


class CustomMontageCreate(BaseModel):
    """A doctor-authored placement.

    Kept out of reference.tdcs_placements deliberately: that library is
    curated and superadmin-owned, and 32 revokes application writes to it so
    a bug cannot alter a validated montage. clinical_reasoning is NOT NULL in
    the schema and required here for the same reason - a montage departing
    from the validated library carries the reason it was chosen as part of the
    clinical record.
    """

    device_id: UUID
    montage_name: str = Field(min_length=1, max_length=200)
    anode_sites: list[str] = Field(min_length=1, max_length=1)
    cathode_sites: list[str] = Field(min_length=1, max_length=4)
    condition_id: UUID | None = None
    description: str | None = None
    clinical_reasoning: str = Field(min_length=1)


class CustomMontageRead(BaseModel):
    custom_montage_id: UUID
    created_by: UUID
    clinic_id: UUID | None = None
    device_id: UUID
    condition_id: UUID | None = None
    montage_name: str
    anode_sites: list[str]
    cathode_sites: list[str]
    description: str | None = None
    clinical_reasoning: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    device_name: str | None = None
    modality: str | None = None
    condition_name: str | None = None
    created_by_name: str | None = None


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
    # appointments.start_time/end_time are TIME columns — asyncpg returns
    # datetime.time, not a string. Was declared str, which FastAPI's
    # response validation rejected outright (ResponseValidationError) the
    # moment any session actually had a claimed slot.
    start_time: time | None = None
    end_time: time | None = None
    status: str
    doctor_id: UUID | None = None
    ca_id: UUID | None = None


class ProtocolPushResult(BaseModel):
    protocol_id: UUID
    status: str
    sessions_created: int
    follow_ups_created: int
    scales_assigned: int
    conditions_recorded: int = 0
    diagnoses_recorded: int = 0
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
