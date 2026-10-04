"""What the doctor and the patient are told when a slot is held, booked and
paid for (app/workers/event_relay.py). The row is stubbed; the query itself
runs against a real database in tests/integration/test_event_relay.py's relay."""

import datetime as dt

from app.workers import event_relay

APPOINTMENT_ID = "11111111-1111-1111-1111-111111111111"


class _Session:
    """Answers the handler's one query with a fixed appointment row."""

    def __init__(self, **overrides):
        self.row = {
            "patient_id": "patient-1",
            "doctor_id": "doctor-1",
            "status": "paid",
            "appointment_type": "initial_consultation",
            "appointment_date": dt.date(2026, 10, 5),
            "start_time": dt.time(10, 30),
            "first_name": "Asha",
            "last_name": "Rao",
            **overrides,
        }

    async def execute(self, *_args):
        return self

    def mappings(self):
        return self

    def first(self):
        return self.row


def _by_recipient(notes: list[dict]) -> dict[str, tuple[str, str]]:
    return {note["recipient_id"]: (note["title"], note["body"]) for note in notes}


async def test_payment_tells_patient_and_doctor_the_slot_is_booked():
    notes = _by_recipient(await event_relay._handle_appointment_paid(_Session(), {"appointment_id": APPOINTMENT_ID}))

    assert notes["patient-1"] == (
        "Payment successful — your slot is booked",
        "Initial consultation on Mon, 05 Oct 2026 at 10:30 AM is confirmed.",
    )
    assert notes["doctor-1"] == ("Slot booked", "Asha Rao booked Mon, 05 Oct 2026 at 10:30 AM (Initial consultation).")


async def test_held_slot_is_not_called_booked():
    notes = _by_recipient(await event_relay._handle_appointment_booked(_Session(status="selected"), {"appointment_id": APPOINTMENT_ID}))

    assert notes["patient-1"][0] == "Your slot is held"
    assert notes["doctor-1"][0] == "New booking, awaiting payment"
    assert "Asha Rao is holding Mon, 05 Oct 2026 at 10:30 AM" in notes["doctor-1"][1]


async def test_booking_that_needs_no_payment_is_booked_at_once():
    notes = _by_recipient(await event_relay._handle_appointment_booked(_Session(), {"appointment_id": APPOINTMENT_ID}))

    assert notes["patient-1"][0] == "Your slot is booked"
    assert notes["doctor-1"][0] == "Slot booked"


async def test_appointment_without_a_doctor_or_a_time_still_notifies_the_patient():
    session = _Session(doctor_id=None, appointment_date=None, start_time=None)

    notes = await event_relay._handle_appointment_paid(session, {"appointment_id": APPOINTMENT_ID})

    assert [note["recipient_id"] for note in notes] == ["patient-1"]
    assert notes[0]["body"] == "Initial consultation on a time still to be set is confirmed."
