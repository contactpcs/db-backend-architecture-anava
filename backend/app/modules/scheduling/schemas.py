from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, Field


class WeeklyScheduleCreate(BaseModel):
    clinic_id: UUID
    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    slot_duration_minutes: int = 30
    break_start: time | None = None
    break_end: time | None = None
    max_appointments: int | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    is_active: bool = True


class WeeklyScheduleRead(BaseModel):
    schedule_id: UUID
    doctor_id: UUID
    clinic_id: UUID
    day_of_week: int
    start_time: time
    end_time: time
    slot_duration_minutes: int
    break_start: time | None = None
    break_end: time | None = None
    is_active: bool


class MyWeeklyScheduleItem(BaseModel):
    """Same shape as WeeklyScheduleCreate minus clinic_id — resolved from the
    caller doctor's own denormalized doctors.clinic_id, not chosen by the
    caller (same reasoning as MyScheduleOverrideCreate)."""

    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    slot_duration_minutes: int = 30
    break_start: time | None = None
    break_end: time | None = None
    max_appointments: int | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    is_active: bool = True


class MyWeeklyScheduleReplace(BaseModel):
    """Atomic replace of the caller doctor's own weekly template — delete
    every existing rule, insert this set. Matches v1's upsert_weekly_schedule
    (delete-then-insert), since there's no natural per-day PATCH when a
    doctor is redrawing their whole week at once in one form submit."""

    items: list[MyWeeklyScheduleItem]


class ScheduleOverrideCreate(BaseModel):
    clinic_id: UUID
    override_date: date
    is_available: bool = False
    start_time: time | None = None
    end_time: time | None = None
    slot_duration_minutes: int | None = None
    reason: str | None = None


class MyScheduleOverrideCreate(BaseModel):
    """Same shape minus clinic_id — resolved from the caller doctor's own
    denormalized doctors.clinic_id, not chosen by the caller."""

    override_date: date
    is_available: bool = False
    start_time: time | None = None
    end_time: time | None = None
    slot_duration_minutes: int | None = None
    reason: str | None = None


class ScheduleOverrideRead(BaseModel):
    override_id: UUID
    doctor_id: UUID
    clinic_id: UUID
    override_date: date
    is_available: bool
    start_time: time | None = None
    end_time: time | None = None
    reason: str | None


class AvailabilitySlotRead(BaseModel):
    date: date
    start_time: time
    end_time: time
    is_available: bool


# The four original visit types, always valid regardless of the
# billable_items catalog — keeps booking working in an environment where
# nobody has priced anything yet (reference.billable_items ships empty).
# Anything else must exist as an active reference.billable_items row
# (category='appointment') — enforced in AppointmentService.create, not
# here, since Pydantic can't do a DB lookup at parse time. A super admin
# adding a new appointment_type under Billable Items makes it bookable
# immediately, no deploy.
APPOINTMENT_TYPES = "^(initial|follow_up|device_session|protocol_followup)$"


class AppointmentCreate(BaseModel):
    clinic_id: UUID
    patient_id: UUID
    doctor_id: UUID
    ca_id: UUID | None = None
    cycle_id: UUID | None = None
    appointment_date: date
    start_time: time
    end_time: time | None = None
    appointment_type: str = Field(default="initial", min_length=1)
    reason: str | None = None
    patient_complaint: str | None = None


class AppointmentUpdate(BaseModel):
    """Free-text edits only.

    appointment_type is deliberately NOT editable. Under the four-type model the
    type decides which pool the visit books against — a device_session holds a
    slot of clinic device capacity, a consultation holds a slot on a doctor's
    calendar. Changing it after the fact would leave the row holding the wrong
    kind of slot, with no way for either pool to notice. A visit booked as the
    wrong type is cancelled and rebooked, not relabelled.
    """

    notes: str | None = None
    patient_complaint: str | None = None


class AppointmentReschedule(BaseModel):
    appointment_date: date
    start_time: time
    end_time: time | None = None
    change_reason: str | None = None


class AppointmentStatusUpdate(BaseModel):
    # 'confirmed' and 'scheduled' are gone: payment is what confirms a visit, so
    # 'paid' says it once instead of two states saying it twice. 'selected' is
    # not settable here either — a slot is claimed through the booking
    # endpoints, which also write the time and the hold in the same statement.
    # 'paid' is not settable here either — the Razorpay webhook
    # (payments/router.py: POST /webhooks/razorpay -> handle_webhook) is the
    # only sanctioned route to 'paid', so a payment row always exists behind
    # the transition. This used to be a backdoor: staff could PATCH straight
    # to 'paid' with no payment record at all.
    status: str = Field(pattern="^(checked_in|in_progress|completed|cancelled|no_show)$")
    cancellation_reason: str | None = None


# ── patient self-service ────────────────────────────────────────────────────


class MyAppointmentBook(BaseModel):
    """Booking an initial or follow-up. The patient sends only WHEN — clinic,
    doctor and patient are all resolved server-side from their own record, so
    there is nothing to spoof."""

    appointment_date: date
    start_time: time
    reason: str | None = None
    patient_complaint: str | None = None


class MyClaimSlot(BaseModel):
    """Putting a time on a protocol-generated 'planned' session.

    appointment_date is optional: omit it to claim the date the doctor
    prescribed, or supply a later one to pick up a session whose date has
    already passed (an overdue 'planned' row simply stays planned until the
    patient is ready)."""

    start_time: time
    appointment_date: date | None = None


class MyCancel(BaseModel):
    reason: str = Field(min_length=1)


class AppointmentRead(BaseModel):
    appointment_id: UUID
    clinic_id: UUID
    patient_id: UUID
    patient_name: str | None = None
    # Nullable: a device_session carries no doctor at all, because a clinical
    # assistant runs it and it occupies no doctor's calendar. Null here is an
    # honest answer, not missing data — use responsible_doctor_* to ask "which
    # doctor is behind this visit", which is always answerable.
    doctor_id: UUID | None = None
    doctor_name: str | None = None
    # doctors.doctor_id (public ID) — /doctors/{doctor_id}/availability and
    # similar path params expect this, not doctor_id above (profiles.id).
    doctor_public_id: UUID | None = None
    responsible_doctor_id: UUID | None = None
    responsible_doctor_name: str | None = None
    ca_id: UUID | None
    cycle_id: UUID | None = None
    plan_id: UUID | None = None
    protocol_id: UUID | None = None
    # Set only for appointment_type = device_session — which of the clinic's
    # devices this session runs on and books capacity against.
    clinic_device_id: UUID | None = None
    session_number: int | None = None
    appointment_date: date
    # Nullable: a 'planned' row has a date and no time yet — the doctor
    # prescribed the day, the patient picks the hour later.
    start_time: time | None = None
    end_time: time | None = None
    hold_expires_at: datetime | None = None
    appointment_type: str
    status: str
    reason: str | None = None
    patient_complaint: str | None = None
    notes: str | None = None
    cancellation_reason: str | None = None
    # Nullable since 52: a 'planned' row has no slot and no payment, so
    # nobody has booked it. Declaring these required made every protocol-born
    # appointment 500 on serialization — the same shape as the
    # extended_sessions bug on TreatmentPlanRead.
    booked_by: UUID | None = None
    booked_by_role: str | None = None
    cancelled_by: UUID | None = None
    rescheduled_from: UUID | None = None
    rescheduled_to: UUID | None = None
    checked_in_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


# ── clinic device schedule (clinic-admin side) ──────────────────────────────


class DeviceScheduleItem(BaseModel):
    """One weekday of a clinic's device timetable.

    Same shape as MyWeeklyScheduleItem plus `capacity`, deliberately: the admin
    screen is the doctor schedule screen they already understand, with one extra
    field.
    """

    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    slot_duration_minutes: int = 30
    break_start: time | None = None
    break_end: time | None = None
    # How many device sessions may run at once in a slot. Set by the admin
    # judging devices AND assistants on shift together — never computed.
    capacity: int = Field(ge=1)
    is_active: bool = True
    effective_from: date | None = None
    effective_until: date | None = None


class DeviceScheduleReplace(BaseModel):
    """Atomic replace of the clinic's whole device week — there is no natural
    per-day PATCH when the admin is redrawing the entire week in one form."""

    items: list[DeviceScheduleItem]


class DeviceScheduleRead(BaseModel):
    schedule_id: UUID
    clinic_id: UUID
    clinic_device_id: UUID
    day_of_week: int
    start_time: time
    end_time: time
    slot_duration_minutes: int
    break_start: time | None = None
    break_end: time | None = None
    capacity: int
    is_active: bool


class DeviceOverrideCreate(BaseModel):
    override_date: date
    is_available: bool = False
    start_time: time | None = None
    end_time: time | None = None
    # NULL inherits the weekly capacity. Set it when a device is out for service
    # or an assistant is on leave.
    capacity: int | None = Field(default=None, ge=1)
    reason: str | None = None


class DeviceOverrideRead(BaseModel):
    override_id: UUID
    clinic_id: UUID
    clinic_device_id: UUID
    override_date: date
    is_available: bool
    start_time: time | None = None
    end_time: time | None = None
    capacity: int | None = None
    reason: str | None = None


class ClinicDeviceScheduleOverview(BaseModel):
    """One entry per device the clinic owns, for the admin landing screen —
    every device it could set a schedule for, with the week it has so far
    (empty if unset). Hydrated fields (device_name/modality/quantity) mirror
    ClinicDeviceRead so the screen can render without a second fetch."""

    clinic_device_id: UUID
    device_id: UUID
    device_name: str | None = None
    modality: str | None = None
    quantity: int
    week: list[DeviceScheduleRead]


class DeviceSlotRead(BaseModel):
    """A device slot, with the counts that make capacity legible.

    A doctor's slot is available when nobody has it. A device slot is available
    while FEWER THAN capacity people have it — so the raw numbers are returned,
    not just a boolean.
    """

    date: date
    start_time: time
    end_time: time
    capacity: int
    booked: int
    remaining: int
    is_available: bool


# ── clinic device inventory ────────────────────────────────────────────────


class ClinicDeviceCreate(BaseModel):
    device_id: UUID
    # How many units the clinic owns. Not the number of concurrent sessions —
    # that is capacity on the device schedule, which also depends on staffing.
    quantity: int = Field(default=1, ge=1)
    acquired_on: date | None = None
    notes: str | None = None


class ClinicDeviceUpdate(BaseModel):
    quantity: int | None = Field(default=None, ge=1)
    is_active: bool | None = None
    acquired_on: date | None = None
    notes: str | None = None


class ClinicDeviceRead(BaseModel):
    clinic_device_id: UUID
    clinic_id: UUID
    device_id: UUID
    quantity: int
    is_active: bool
    acquired_on: date | None = None
    notes: str | None = None
    # Hydrated from the catalogue so the admin screen shows names, not ids.
    device_code: str | None = None
    device_name: str | None = None
    modality: str | None = None
    phase: int | None = None
    device_is_active: bool | None = None
    company_name: str | None = None
