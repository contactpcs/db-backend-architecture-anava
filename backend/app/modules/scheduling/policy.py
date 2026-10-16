"""Appointment policy note shown before booking (design D8 —
Documents/design_appointment_policy_and_refunds.md).

Built from the clinic's live settings on every request, so the note can never
disagree with the rules the booking code enforces: the hold length, the
reschedule notice (clinics.reschedule_min_hours), the refund tiers
(reference.cancellation_policy_tiers) and the fixed device-session rules."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.scheduling.service import DEVICE_MAKEUP_WINDOW_DAYS, SAME_DAY_DEVICE_SESSION_REASON, reschedule_min_hours


def _hours(h: float) -> str:
    h = float(h)
    return f"{h:g} hour" + ("" if h == 1 else "s")


def _pct(p: float) -> str:
    return f"{float(p):g}%"


def describe_refund_tiers(tiers: list[dict]) -> list[str]:
    """tiers sorted highest min_hours_before first (resolve_tiers order).
    Mirrors resolve_cancellation_refund_percent: the first tier whose
    threshold the notice clears applies; below every threshold, 0 %."""
    if not tiers:
        return ["Cancellations are not refunded."]
    lines = []
    upper = None  # the next-higher threshold, so each band reads "between X and Y"
    for tier in sorted(tiers, key=lambda t: float(t["min_hours_before"]), reverse=True):
        hours, pct = float(tier["min_hours_before"]), float(tier["refund_percent"])
        if upper is None:
            when = "Cancelled any time before the start time" if hours == 0 else f"Cancelled {_hours(hours)} or more before the start time"
        elif hours == 0:
            when = f"Cancelled less than {_hours(upper)} before the start time"
        else:
            when = f"Cancelled between {hours:g} and {_hours(upper)} before the start time"
        lines.append(f"{when}: {_pct(pct)} refund of the amount paid." if pct else f"{when}: no refund.")
        upper = hours
    lowest = float(tiers[-1]["min_hours_before"])
    if lowest > 0:
        lines.append(f"Cancelled less than {_hours(lowest)} before the start time: no refund.")
    return lines


def build_policy_note(values: dict) -> list[dict]:
    """Pure: values -> titled sections of plain-language points."""
    booking = []
    if values["payment_required"]:
        booking += [
            "You book a slot on your doctor's calendar. Your booking is confirmed only after payment.",
            f"A slot you select is held for {values['hold_minutes']} minutes while you pay. "
            "If payment is not completed in time, the hold is released and the booking is cancelled automatically.",
        ]
    else:
        booking.append("You book a slot on your doctor's calendar. Your booking is confirmed immediately.")
    booking += [
        "The amount shown before you pay is the consultation or session fee plus the platform and convenience fee.",
        "Device sessions and protocol follow-ups prescribed by your doctor already have a date. "
        "You choose the time; each one is paid when you book it.",
    ]

    cancel = ["You can cancel a booking at any time before it starts. A reason is required."]
    same = values["refund_tiers_appointment"] == values["refund_tiers_device_session"]
    if same:
        cancel += describe_refund_tiers(values["refund_tiers_appointment"])
    else:
        cancel += ["Consultations: " + " ".join(describe_refund_tiers(values["refund_tiers_appointment"]))]
        cancel += ["Device sessions: " + " ".join(describe_refund_tiers(values["refund_tiers_device_session"]))]
    cancel += [
        "Only bookings that have been paid are eligible for a refund. An unpaid booking is simply released.",
        "Online payments are refunded automatically to the original payment method; "
        "banks usually take 5–7 working days to show the credit. Cash payments are refunded at the clinic counter.",
        "Sessions prescribed in your treatment plan that you have not yet booked cannot be cancelled from the app. "
        "Please contact the clinic.",
    ]

    reschedule = [
        f"You can reschedule a booked appointment up to {_hours(values['reschedule_min_hours'])} before it starts.",
        "If you have already paid, the payment moves to the new slot. You are not charged again.",
        "If you have not paid yet, you can still reschedule; payment is then due for the new slot.",
        "If you missed a paid appointment, you can reschedule it; your payment carries over.",
    ]

    attendance = [
        f"A paid appointment that is not attended is marked as a no-show {_hours(values['no_show_grace_hours'])} after its start time.",
        "A prescribed session whose date passes without a slot being booked is marked as missed.",
    ]

    device = [
        SAME_DAY_DEVICE_SESSION_REASON,
        f"A missed or no-show device session can only be rescheduled to one of the {DEVICE_MAKEUP_WINDOW_DAYS} days right after "
        "the last scheduled session of your treatment course.",
    ]

    return [
        {"key": "booking", "title": "Booking and payment", "points": booking},
        {"key": "cancellation", "title": "Cancellation and refunds", "points": cancel},
        {"key": "reschedule", "title": "Rescheduling", "points": reschedule},
        {"key": "attendance", "title": "Missed appointments", "points": attendance},
        {"key": "device_sessions", "title": "Device sessions", "points": device},
    ]


class AppointmentPolicyService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def for_clinic(self, clinic_id: UUID) -> dict:
        from app.modules.admin.repository import CancellationPolicyRepository

        settings = get_settings()
        tiers = CancellationPolicyRepository(self.session)

        def slim(rows: list[dict]) -> list[dict]:
            return [{"min_hours_before": float(r["min_hours_before"]), "refund_percent": float(r["refund_percent"])} for r in rows]

        values = {
            "payment_required": settings.appointment_payment_required,
            "hold_minutes": settings.appointment_hold_minutes,
            "reschedule_min_hours": await reschedule_min_hours(self.session, clinic_id),
            "no_show_grace_hours": settings.appointment_no_show_paid_grace_hours,
            "device_makeup_window_days": DEVICE_MAKEUP_WINDOW_DAYS,
            "refund_tiers_appointment": slim(await tiers.resolve_tiers(session_type="appointment", clinic_id=clinic_id)),
            "refund_tiers_device_session": slim(await tiers.resolve_tiers(session_type="device_session", clinic_id=clinic_id)),
        }
        return {
            "clinic_id": clinic_id,
            "values": values,
            "sections": build_policy_note(values),
            "acknowledgement": "I have read and agree to the booking, cancellation, rescheduling and refund policy.",
        }
