"""Appointment policy, paid-cancellation refunds, device make-up window and the
F-1 reschedule fix (Documents/design_appointment_policy_and_refunds.md).
No DB needed."""

import datetime as dt
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.db import RequestContext
from app.core.exceptions import BusinessRuleError
from app.modules.payments.service import refunded_status_for
from app.modules.scheduling import service as sched
from app.modules.scheduling.policy import build_policy_note, describe_refund_tiers
from app.modules.scheduling.service import AppointmentService, device_makeup_reason, device_makeup_window

OCT = lambda day: dt.date(2026, 10, day)  # noqa: E731
ME = str(uuid4())


def _patient() -> RequestContext:
    return RequestContext(user_id=ME, role="patient", clinic_id=None, region_id=None)


# ── device make-up window (D6) ──────────────────────────────────────────────


def test_window_is_the_two_days_after_the_last_session():
    assert device_makeup_window(OCT(20), today=OCT(15)) == (OCT(21), OCT(22))


def test_window_already_past_starts_today():
    assert device_makeup_window(OCT(20), today=OCT(25)) == (OCT(25), OCT(26))


def test_reason_names_the_course_end_the_dates_and_the_injury_risk():
    reason = device_makeup_reason(OCT(20), OCT(21), OCT(22))
    assert "20 Oct 2026" in reason and "21 Oct 2026" in reason and "22 Oct 2026" in reason
    assert "injury" in reason


def _service() -> AppointmentService:
    svc = AppointmentService(AsyncMock())
    svc.repo = AsyncMock()
    svc.audit = AsyncMock()
    return svc


def _no_show_device_session() -> dict:
    return {
        "appointment_id": uuid4(),
        "patient_id": ME,
        "clinic_id": uuid4(),
        "protocol_id": uuid4(),
        "appointment_type": "device_session",
        "status": "no_show",
        "appointment_date": OCT(10),
        "start_time": dt.time(10, 0),
    }


@pytest.mark.asyncio
async def test_reschedule_options_give_the_make_up_window_with_its_reason():
    svc = _service()
    appt = _no_show_device_session()
    svc.get = AsyncMock(return_value=appt)
    svc.repo.last_device_session_date.return_value = OCT(20)
    with (
        patch.object(sched, "_now_ist_naive", return_value=dt.datetime(2026, 10, 14, 9, 0)),
        patch.object(sched, "reschedule_min_hours", AsyncMock(return_value=24.0)),
    ):
        out = await svc.reschedule_options(appt["appointment_id"], ctx=_patient())
    assert out["can_reschedule"] is True
    assert (out["earliest_date"], out["latest_date"]) == (OCT(21), OCT(22))
    assert "injury" in out["date_rule_reason"]


@pytest.mark.asyncio
async def test_no_show_device_session_cannot_move_inside_the_course():
    svc = _service()
    appt = _no_show_device_session()
    svc.repo.last_device_session_date.return_value = OCT(20)
    with (
        patch.object(sched, "_now_ist_naive", return_value=dt.datetime(2026, 10, 14, 9, 0)),
        patch.object(sched, "_reject_if_past"),
        patch.object(sched, "_assert_clinic_operational", AsyncMock()),
        pytest.raises(BusinessRuleError) as err,
    ):
        await svc._reschedule_protocol_born(
            appt, {"appointment_date": OCT(16), "start_time": dt.time(11, 0)}, changed_by=uuid4(), changed_by_role="patient"
        )
    assert err.value.code == "DEVICE_RESCHEDULE_OUTSIDE_WINDOW"


@pytest.mark.asyncio
async def test_second_device_session_of_a_course_on_one_day_is_refused():
    svc = _service()
    svc.repo.count_device_sessions_on_date.return_value = 1
    with pytest.raises(BusinessRuleError) as err:
        await svc.assert_one_device_session_per_day(_no_show_device_session(), OCT(21))
    assert err.value.code == "SAME_DAY_DEVICE_SESSION"


# ── F-1: rescheduling an unpaid booking ─────────────────────────────────────


@pytest.mark.asyncio
async def test_unpaid_reschedule_clears_the_hold_and_carries_the_pending_checkout():
    svc = _service()
    old_id, new_id, payment_id = uuid4(), uuid4(), uuid4()
    old = {
        "appointment_id": old_id,
        "clinic_id": uuid4(),
        "patient_id": ME,
        "doctor_id": uuid4(),
        "ca_id": None,
        "instance_id": None,
        "appointment_type": "initial",
        "status": "selected",
        "appointment_date": OCT(20),
        "start_time": dt.time(10, 0),
    }
    svc.get = AsyncMock(side_effect=[old, {"appointment_id": new_id}])
    svc.create = AsyncMock(return_value={"appointment_id": new_id})
    with (
        patch.object(sched, "assert_clinic_scope", AsyncMock()),
        patch.object(sched, "emit_event", AsyncMock()),
        patch("app.modules.payments.repository.PaymentRepository") as payments,
    ):
        payments.return_value.get_for_appointment = AsyncMock(return_value={"payment_id": payment_id, "status": "pending"})
        payments.return_value.relink_appointment = AsyncMock()
        await svc.reschedule(
            old_id,
            {"appointment_date": OCT(22), "start_time": dt.time(11, 0)},
            changed_by=uuid4(),
            changed_by_role="patient",
            ctx=_patient(),
        )

    # chk_appointments_hold: the old row may not keep its expiry once it is no longer 'selected'.
    assert svc.repo.update_fields.await_args_list[0].args == (old_id, {"status": "rescheduled", "hold_expires_at": None})
    payments.return_value.relink_appointment.assert_awaited_once_with(payment_id, new_appointment_id=new_id)
    # Unpaid stays unpaid: the new row is never forced to 'paid'.
    assert all(c.args[1].get("status") != "paid" for c in svc.repo.update_fields.await_args_list)


# ── refunds (D1-D3) ─────────────────────────────────────────────────────────


def test_full_refund_marks_refunded_partial_marks_partially_refunded():
    assert refunded_status_for(1050, 1050) == "refunded"
    assert refunded_status_for(1050, 525) == "partially_refunded"


def test_policy_text_for_the_default_twelve_hour_rule():
    lines = describe_refund_tiers([{"min_hours_before": 12, "refund_percent": 100}])
    assert lines == [
        "Cancelled at least 12 hours before the start time: 100% refund of the amount paid.",
        "Cancelled less than 12 hours before the start time: no refund.",
    ]


def test_no_tiers_means_no_refund():
    assert describe_refund_tiers([]) == ["Cancellations are not refunded."]


def test_policy_note_reads_live_values():
    tiers = [{"min_hours_before": 12.0, "refund_percent": 100.0}]
    sections = build_policy_note(
        {
            "payment_required": True,
            "hold_minutes": 15,
            "reschedule_min_hours": 6.0,
            "no_show_grace_hours": 2.0,
            "device_makeup_window_days": 2,
            "refund_tiers_appointment": tiers,
            "refund_tiers_device_session": tiers,
        }
    )
    text = " ".join(p for s in sections for p in s["points"])
    assert [s["key"] for s in sections] == ["booking", "cancellation", "reschedule", "attendance", "device_sessions"]
    assert "15 minutes" in text and "6 hours before it starts" in text and "12 hours" in text
