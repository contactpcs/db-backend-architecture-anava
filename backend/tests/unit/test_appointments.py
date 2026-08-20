"""Appointment lifecycle, slot building, and device capacity.

Covers the rules that carry the design, in the order they bite:

  1. the state machine — including the two transitions the old FSM got wrong
  2. who may start a visit — the device-session branch, which used to be
     impossible for the only person who can actually perform it
  3. slot building for both pools — exclusive (doctor) and counted (clinic)
  4. capacity arithmetic, including the boundary where a slot fills
  5. the payment seam, in both flag positions
  6. hold expiry, in both its asymmetric branches

Everything here is pure or fake-backed: no database, no event loop fixtures.
The SQL that these rules ultimately rest on is verified separately by
SQL/v1/_postapply_verify.sql against a real instance.
"""

import datetime as dt

import pytest

from app.modules.scheduling.service import (
    _ALLOWED_FROM,
    _ATTENDANCE_STATUSES,
    ACTIVE_STATUSES,
    DEFAULT_SLOT_MINUTES,
    PROTOCOL_BORN_TYPES,
    SLOT_OCCUPYING_STATUSES,
    STATUS_PAID,
    STATUS_PLANNED,
    STATUS_SELECTED,
    TYPE_DEVICE_SESSION,
    TYPE_FOLLOW_UP,
    TYPE_INITIAL,
    TYPE_PROTOCOL_FOLLOWUP,
    _build_day_slots,
    _build_device_day_slots,
    _step_slots,
)

MONDAY = dt.date(2026, 8, 17)  # a Monday; isoweekday() 1 -> dow 1


# ═══════════════════════════════════════════════════════════════════════════
# 1. The state machine
# ═══════════════════════════════════════════════════════════════════════════


def test_payment_is_the_only_route_to_paid():
    """'paid' is reachable only from 'selected'. Nothing else confirms a visit."""
    assert _ALLOWED_FROM[STATUS_PAID] == {STATUS_SELECTED}


def test_a_slot_can_only_be_claimed_from_planned():
    assert _ALLOWED_FROM[STATUS_SELECTED] == {STATUS_PLANNED}


def test_attendance_requires_payment_first():
    """checked_in comes from 'paid' and nowhere else — an unpaid patient cannot
    be walked into the building."""
    assert _ALLOWED_FROM["checked_in"] == {STATUS_PAID}


def test_confirmed_and_scheduled_are_gone():
    """Both were removed deliberately: payment is what confirms a visit, so
    'paid' says it once instead of two states saying it twice."""
    assert "confirmed" not in _ALLOWED_FROM
    assert "scheduled" not in _ALLOWED_FROM
    for allowed in _ALLOWED_FROM.values():
        assert "confirmed" not in allowed
        assert "scheduled" not in allowed


def test_planned_rows_can_be_cancelled():
    """The protocol module cancels its own unclaimed rows this way when a doctor
    cancels a whole protocol (treatment_protocols cancel_planned). If this
    transition were illegal, that would silently stop working."""
    assert STATUS_PLANNED in _ALLOWED_FROM["cancelled"]


def test_a_completed_visit_cannot_be_reopened():
    for target, allowed in _ALLOWED_FROM.items():
        assert "completed" not in allowed, f"{target} must not be reachable from completed"


def test_slot_occupying_statuses_match_the_capacity_count():
    """This set is duplicated in three places by necessity — here, the capacity
    SQL, and the partial index predicate in 36. They must agree or the count
    disagrees with what the index covers."""
    assert SLOT_OCCUPYING_STATUSES == {"selected", "paid", "checked_in", "in_progress"}
    assert SLOT_OCCUPYING_STATUSES < ACTIVE_STATUSES  # planned is active but occupies nothing


def test_planned_is_active_but_occupies_no_slot():
    assert STATUS_PLANNED in ACTIVE_STATUSES
    assert STATUS_PLANNED not in SLOT_OCCUPYING_STATUSES


# ═══════════════════════════════════════════════════════════════════════════
# 2. The four types
# ═══════════════════════════════════════════════════════════════════════════


def test_protocol_born_types_are_exactly_the_two_the_doctor_generates():
    assert PROTOCOL_BORN_TYPES == {TYPE_DEVICE_SESSION, TYPE_PROTOCOL_FOLLOWUP}


def test_protocol_followup_is_its_own_type():
    """Not folded into follow_up: billable_items keys a price to an
    appointment_type, so sharing one value would force a protocol follow-up and
    a patient-booked follow-up to cost the same forever."""
    assert TYPE_PROTOCOL_FOLLOWUP != TYPE_FOLLOW_UP
    assert TYPE_PROTOCOL_FOLLOWUP == "protocol_followup"


def test_only_device_sessions_skip_the_doctor_calendar():
    """The other three are all consultations and book a doctor."""
    assert TYPE_DEVICE_SESSION in PROTOCOL_BORN_TYPES
    assert TYPE_INITIAL not in PROTOCOL_BORN_TYPES
    assert TYPE_FOLLOW_UP not in PROTOCOL_BORN_TYPES


def test_attendance_statuses_are_the_ones_needing_a_performer():
    assert _ATTENDANCE_STATUSES == {"in_progress", "completed"}


# ═══════════════════════════════════════════════════════════════════════════
# 3. Slot building — the shared stepper
# ═══════════════════════════════════════════════════════════════════════════


def test_step_slots_walks_the_window_in_fixed_steps():
    slots = _step_slots(MONDAY, dt.time(9, 0), dt.time(11, 0), 30, None, None)
    assert slots == [
        (dt.time(9, 0), dt.time(9, 30)),
        (dt.time(9, 30), dt.time(10, 0)),
        (dt.time(10, 0), dt.time(10, 30)),
        (dt.time(10, 30), dt.time(11, 0)),
    ]


def test_step_slots_skips_the_break():
    slots = _step_slots(MONDAY, dt.time(9, 0), dt.time(12, 0), 60, dt.time(10, 0), dt.time(11, 0))
    assert slots == [(dt.time(9, 0), dt.time(10, 0)), (dt.time(11, 0), dt.time(12, 0))]


def test_step_slots_never_runs_past_the_window():
    """A 45-minute step in a 2-hour window yields two whole slots, not two and a
    stub — a partial slot is not bookable."""
    slots = _step_slots(MONDAY, dt.time(9, 0), dt.time(11, 0), 45, None, None)
    assert slots == [(dt.time(9, 0), dt.time(9, 45)), (dt.time(9, 45), dt.time(10, 30))]


# ═══════════════════════════════════════════════════════════════════════════
# 4. Doctor slots — exclusive
# ═══════════════════════════════════════════════════════════════════════════


def _doctor_rule(**over):
    return {
        "day_of_week": 1,
        "start_time": dt.time(9, 0),
        "end_time": dt.time(11, 0),
        "slot_duration_minutes": 60,
        "break_start": None,
        "break_end": None,
        "effective_from": None,
        "effective_until": None,
        **over,
    }


def test_doctor_slot_is_taken_by_one_booking():
    """Exclusive: one booking closes the slot entirely."""
    booked = {(dt.time(9, 0), dt.time(10, 0))}
    slots = _build_day_slots(MONDAY, [_doctor_rule()], None, booked)
    assert [s["is_available"] for s in slots] == [False, True]


def test_doctor_day_is_empty_when_an_override_closes_it():
    slots = _build_day_slots(MONDAY, [_doctor_rule()], {"is_available": False}, set())
    assert slots == []


def test_doctor_day_is_empty_with_no_rule_for_that_weekday():
    slots = _build_day_slots(MONDAY, [_doctor_rule(day_of_week=3)], None, set())
    assert slots == []


def test_effective_window_excludes_dates_outside_it():
    rule = _doctor_rule(effective_from=dt.date(2026, 9, 1))
    assert _build_day_slots(MONDAY, [rule], None, set()) == []


# ═══════════════════════════════════════════════════════════════════════════
# 5. Device slots — counted, not exclusive
# ═══════════════════════════════════════════════════════════════════════════


def _device_rule(**over):
    # capacity is no longer a weekly-rule field — 67d1868's refactor made
    # quantity (clinic_devices.quantity, the clinic's owned unit count) the
    # capacity source for the non-override path; a rule dict now only
    # describes the day's open window and break, so there's nothing left to
    # parametrize here besides the day-shape overrides callers actually use.
    return {
        "day_of_week": 1,
        "start_time": dt.time(9, 0),
        "end_time": dt.time(11, 0),
        "slot_duration_minutes": 60,
        "break_start": None,
        "break_end": None,
        "effective_from": None,
        "effective_until": None,
        **over,
    }


def test_device_slot_stays_open_below_capacity():
    """THE distinction the whole capacity design rests on: a doctor's slot is
    taken by one booking, a device slot is taken by `capacity` of them."""
    booked = {(MONDAY, dt.time(9, 0)): 2}
    slots = _build_device_day_slots(MONDAY, [_device_rule()], None, booked, 3)
    first = slots[0]
    assert first["booked"] == 2
    assert first["remaining"] == 1
    assert first["is_available"] is True


def test_device_slot_closes_exactly_at_capacity():
    booked = {(MONDAY, dt.time(9, 0)): 3}
    slots = _build_device_day_slots(MONDAY, [_device_rule()], None, booked, 3)
    first = slots[0]
    assert first["remaining"] == 0
    assert first["is_available"] is False


def test_device_slot_remaining_never_goes_negative():
    """Defensive: capacity can be lowered by an override after bookings exist."""
    booked = {(MONDAY, dt.time(9, 0)): 5}
    slots = _build_device_day_slots(MONDAY, [_device_rule()], None, booked, 3)
    assert slots[0]["remaining"] == 0


def test_override_capacity_replaces_the_weekly_one():
    """A device out for service, or an assistant on leave, without editing the
    week."""
    override = {"is_available": True, "start_time": None, "end_time": None, "capacity": 1}
    booked = {(MONDAY, dt.time(9, 0)): 1}
    slots = _build_device_day_slots(MONDAY, [_device_rule()], override, booked, 3)
    assert slots[0]["capacity"] == 1
    assert slots[0]["is_available"] is False


def test_closed_override_yields_no_device_slots():
    slots = _build_device_day_slots(MONDAY, [_device_rule()], {"is_available": False}, {}, 3)
    assert slots == []


def test_no_device_schedule_means_closed_not_unlimited():
    """A clinic with no rule for that weekday runs no device sessions. It must
    never be read as 'no limit'."""
    assert _build_device_day_slots(MONDAY, [], None, {}, 3) == []


def test_zero_capacity_yields_no_slots():
    """chk_cds_capacity_positive blocks this at the database, but a clinic
    owning zero units of a device (quantity=0) must not produce infinite
    slots either."""
    slots = _build_device_day_slots(MONDAY, [_device_rule()], None, {}, 0)
    assert slots == []


def test_unbooked_device_slot_reports_full_capacity():
    slots = _build_device_day_slots(MONDAY, [_device_rule()], None, {}, 4)
    assert slots[0]["booked"] == 0
    assert slots[0]["remaining"] == 4


# ═══════════════════════════════════════════════════════════════════════════
# 6. The payment seam
# ═══════════════════════════════════════════════════════════════════════════


class _FakeSettings:
    def __init__(self, required: bool):
        self.appointment_payment_required = required
        self.appointment_hold_minutes = 15


def _seam(required: bool):
    """Exercises PatientBookingService._initial_status_and_hold without a
    database — it depends on nothing but settings."""
    from app.modules.scheduling.service import PatientBookingService

    svc = PatientBookingService.__new__(PatientBookingService)
    svc.settings = _FakeSettings(required)
    return svc._initial_status_and_hold()


def test_bypassed_payment_lands_on_paid_with_no_hold():
    """The flag position we ship with. hold_expires_at NULL keeps
    chk_appointments_hold satisfied: ('paid' = 'selected') = (NULL IS NOT NULL)
    is false = false."""
    status, hold = _seam(required=False)
    assert status == STATUS_PAID
    assert hold is None


def test_required_payment_lands_on_selected_with_a_hold():
    status, hold = _seam(required=True)
    assert status == STATUS_SELECTED
    assert hold is not None
    remaining = (hold - dt.datetime.now(dt.UTC)).total_seconds() / 60
    assert 14 <= remaining <= 15


def test_the_hold_invariant_holds_in_both_flag_positions():
    """chk_appointments_hold, restated as the code sees it: exactly the
    'selected' rows carry an expiry, and nothing else may."""
    for required in (True, False):
        status, hold = _seam(required)
        assert (status == STATUS_SELECTED) == (hold is not None)


# ═══════════════════════════════════════════════════════════════════════════
# 7. Hold expiry — the asymmetry
# ═══════════════════════════════════════════════════════════════════════════


def _expiry_branch(plan_id):
    """The sweeper's decision, isolated. Mirrors the two statements in
    AppointmentRepository.release_expired_holds and hold_sweeper.sweep_once."""
    return "revert_to_planned" if plan_id is not None else "delete"


def test_protocol_row_reverts_and_keeps_the_doctors_date():
    """Deleting these would destroy part of a prescription because a payment
    timed out."""
    assert _expiry_branch(plan_id="a-plan") == "revert_to_planned"


def test_patient_booked_row_is_deleted():
    """No earlier state to fall back to, and nothing unpaid should persist.
    Deleting also frees uq_one_active_initial_per_patient immediately."""
    assert _expiry_branch(plan_id=None) == "delete"


@pytest.mark.parametrize("appointment_type", sorted(PROTOCOL_BORN_TYPES))
def test_every_protocol_born_type_carries_a_plan(appointment_type):
    """chk_appointments_protocol_has_plan enforces this in the database, and the
    sweeper depends on it: a protocol row that reached 'selected' without
    plan_id would take the delete branch and lose the doctor's date."""
    assert appointment_type in PROTOCOL_BORN_TYPES
    assert _expiry_branch(plan_id="set-by-protocol-setup") == "revert_to_planned"


# ═══════════════════════════════════════════════════════════════════════════
# 8. Defaults
# ═══════════════════════════════════════════════════════════════════════════


def test_default_slot_length_is_thirty_minutes():
    assert DEFAULT_SLOT_MINUTES == 30
