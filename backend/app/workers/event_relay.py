"""Outbox relay (Architecture ADR-006 / Section 25.2). Drains outbox_events,
resolves who should be notified for each event type, writes notifications
rows, and publishes to each recipient's Redis channel for the SSE feed.

Run continuously: `python -m app.workers.event_relay`
Uses Postgres LISTEN/NOTIFY (SQL/17_outbox_events.sql trigger) to wake
immediately on insert; polls every 5s as a fallback in case a NOTIFY is
missed during a relay restart (documented gap, not a bug — see Architecture
Section 25.2).

Recipient-resolution handlers below are NOT exhaustive — only enough are
wired to prove the pattern works end-to-end (Development Plan Stage 11 GO
condition). Add a handler per event_type in EVENT_HANDLERS as each one's
notification requirement becomes concrete, rather than speculatively
covering all ~30 event types in the catalog now.
"""

import asyncio
import json
import time
from typing import Any

import asyncpg
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.core.db import get_migration_engine
from app.core.pubsub import publish_to_user
from app.modules.notifications.repository import NotificationRepository

logger = structlog.get_logger()
settings = get_settings()

POLL_INTERVAL_SECONDS = 5.0
HEARTBEAT_SECONDS = 300.0

# Live counters for the heartbeat log and GET /api/v1/health/live — the only
# way to tell from outside whether the relay is idle because there is nothing
# to do, or because it cannot see or write what it should.
RELAY_STATE: dict[str, Any] = {
    "started_at": None,
    "last_drain_at": None,
    "last_event_at": None,
    "processed_total": 0,
    "handler_failures_total": 0,
    "live_push_failures_total": 0,
    "last_error": None,
}

# rls_notif_insert requires rls_user_role() to be a real staff role — set by
# AuthContextMiddleware inside an HTTP request. This worker has no request
# (no HTTP call, no JWT, nothing to impersonate) — it's writing on behalf of
# the system for an arbitrary recipient, not as any one staff member, so
# there's no role to set even if we wanted to. Same "bare script, no RLS
# context, scoped anava_app role rejects the INSERT regardless" issue
# get_migration_engine()'s docstring describes for seed scripts — the fix is
# the same: use the master connection, not the scoped one, for this
# system-level write. Created once at import time (this is a long-running
# worker, not a short script) rather than per drain cycle.
_relay_engine = get_migration_engine()
_relay_session_factory = async_sessionmaker(_relay_engine, expire_on_commit=False, autoflush=False)


async def _handle_appointment_booked(session, payload: dict[str, Any]) -> list[dict]:
    """Notifies the doctor a new appointment landed on their calendar, and the
    patient that their booking went through.

    Reads the row itself rather than trusting payload shape — the two emit
    sites (staff booking on a patient's behalf vs. a patient's own
    book_initial/book_follow_up) send different payloads, and the old
    doctor_id-from-payload version silently no-opped for the patient
    self-booking path (its payload carries no doctor_id at all), so a patient
    booking their own appointment got told nothing. appointment_id is the one
    thing both emit sites always send.

    Worded by status, not a separate 'confirmed' state — this app's status
    vocabulary has no such value; 'paid' IS confirmed (payment bypassed or
    already settled), 'selected' means the slot is held pending payment.
    """
    appointment_id = payload.get("appointment_id")
    if not appointment_id:
        return []
    row = (
        (
            await session.execute(
                text("SELECT patient_id, doctor_id, status, appointment_type FROM appointments WHERE appointment_id = :id"),
                {"id": appointment_id},
            )
        )
        .mappings()
        .first()
    )
    if not row:
        return []

    notifications = []
    if row["doctor_id"]:
        notifications.append(
            {
                "recipient_id": str(row["doctor_id"]),
                "type": "appointment",
                "title": "New appointment booked",
                "body": f"Appointment {appointment_id} was booked on your calendar.",
                "entity_type": "appointment",
                "entity_id": appointment_id,
            }
        )
    kind = (row["appointment_type"] or "").replace("_", " ").title()
    notifications.append(
        {
            "recipient_id": str(row["patient_id"]),
            "type": "appointment",
            "title": "Your appointment is confirmed" if row["status"] == "paid" else "Your appointment is booked",
            "body": (
                f"{kind} appointment confirmed."
                if row["status"] == "paid"
                else f"{kind} appointment held — complete payment to confirm your slot."
            ),
            "entity_type": "appointment",
            "entity_id": appointment_id,
        }
    )
    return notifications


async def _handle_appointment_paid(session, payload: dict[str, Any]) -> list[dict]:
    """Notifies the patient their payment landed and the visit is confirmed.
    Fires from AppointmentService.mark_paid() ('selected' -> 'paid') — the
    hold-then-pay path _handle_appointment_booked's 'booked' message already
    covered as pending. payment_completed (payments/service.py) fires for the
    identical real-world moment but is deliberately NOT given a handler here
    too — mark_paid() is called from the same payment-success codepath, so
    wiring both would double-notify the same patient for one event."""
    appointment_id = payload.get("appointment_id")
    if not appointment_id:
        return []
    row = (
        (
            await session.execute(
                text("SELECT patient_id, appointment_date, start_time FROM appointments WHERE appointment_id = :id"),
                {"id": appointment_id},
            )
        )
        .mappings()
        .first()
    )
    if not row:
        return []
    return [
        {
            "recipient_id": str(row["patient_id"]),
            "type": "appointment",
            "title": "Payment received — appointment confirmed",
            "body": f"Your appointment on {row['appointment_date']} at {row['start_time']} is confirmed.",
            "entity_type": "appointment",
            "entity_id": appointment_id,
        }
    ]


async def _handle_registration_completed(session, payload: dict[str, Any]) -> list[dict]:
    """Notifies the patient their registration is complete, and — for a
    self-registered patient specifically — the clinic's receptionists that a
    new approval request is waiting (staff-registered patients skip approval
    entirely, so they'd have nothing to act on)."""
    row = (
        (
            await session.execute(
                text("SELECT profile_id, self_registered, approval_status, primary_clinic_id FROM patients WHERE patient_id = :id"),
                {"id": payload["patient_id"]},
            )
        )
        .mappings()
        .first()
    )
    if not row:
        return []
    notifications = [
        {
            "recipient_id": str(row["profile_id"]),
            "type": "clinical",
            "title": "Registration complete",
            "body": "Your registration is complete — a doctor has been assigned to your care.",
            "entity_type": "patient",
            "entity_id": payload["patient_id"],
        }
    ]
    if row["self_registered"] and row["approval_status"] == "pending":
        receptionists = (
            await session.execute(
                text(
                    "SELECT profile_id FROM clinic_staff_assignments "
                    "WHERE clinic_id = :cid AND staff_role = 'receptionist' AND is_active = TRUE"
                ),
                {"cid": row["primary_clinic_id"]},
            )
        ).all()
        notifications.extend(
            {
                # notifications.type is a fixed enum (SQL/12b_notifications.sql)
                # with no 'patient_approval' value — 'admin' is the closest fit.
                "recipient_id": str(r.profile_id),
                "type": "admin",
                "title": "New patient awaiting approval",
                "body": "A self-registered patient has completed registration and needs review.",
                "entity_type": "patient",
                "entity_id": payload["patient_id"],
            }
            for r in receptionists
        )
    return notifications


async def _handle_appointment_cancelled(session, payload: dict[str, Any]) -> list[dict]:
    """Notifies whichever side didn't do the cancelling."""
    appt = (
        (
            await session.execute(
                text("SELECT patient_id, doctor_id, cancellation_reason FROM appointments WHERE appointment_id = :id"),
                {"id": payload["appointment_id"]},
            )
        )
        .mappings()
        .first()
    )
    if not appt:
        return []
    cancelled_by_role = payload.get("changed_by_role")
    recipient = appt["doctor_id"] if cancelled_by_role == "patient" else appt["patient_id"]
    return [
        {
            "recipient_id": str(recipient),
            "type": "appointment",
            "title": "Appointment cancelled",
            "body": appt["cancellation_reason"],
            "entity_type": "appointment",
            "entity_id": payload["appointment_id"],
        }
    ]


_STATUS_TITLES = {
    "confirmed": "Your appointment is confirmed",
    "completed": "Your appointment is complete",
    # Patient-facing so they actually find out — a no_show (manual or the
    # auto no_show_sweeper) is otherwise invisible to them until they happen
    # to reopen the appointment. They can reschedule it directly (see
    # PatientBookingService.reschedule_own, which now accepts a no_show
    # source) — that's the reason this needs to reach them at all.
    "no_show": "Your appointment was marked as a no-show — you can reschedule it",
    "missed": "Your prescribed session's date passed without a slot ever being claimed — you can reschedule it",
}


async def _handle_appointment_status_changed(session, payload: dict[str, Any]) -> list[dict]:
    """Only statuses in _STATUS_TITLES are patient-facing news — checked_in/
    in_progress are internal workflow states nobody needs pushed to them."""
    title = _STATUS_TITLES.get(payload.get("status", ""))
    if not title:
        return []
    row = (
        (await session.execute(text("SELECT patient_id FROM appointments WHERE appointment_id = :id"), {"id": payload["appointment_id"]}))
        .mappings()
        .first()
    )
    if not row:
        return []
    return [
        {
            "recipient_id": str(row["patient_id"]),
            "type": "appointment",
            "title": title,
            "entity_type": "appointment",
            "entity_id": payload["appointment_id"],
        }
    ]


async def _handle_appointment_rescheduled(session, payload: dict[str, Any]) -> list[dict]:
    row = (
        (
            await session.execute(
                text("SELECT patient_id, appointment_date, start_time FROM appointments WHERE appointment_id = :id"),
                {"id": payload["new_appointment_id"]},
            )
        )
        .mappings()
        .first()
    )
    if not row:
        return []
    return [
        {
            "recipient_id": str(row["patient_id"]),
            "type": "appointment",
            "title": "Your appointment was rescheduled",
            "body": f"New time: {row['appointment_date']} at {row['start_time']}.",
            "entity_type": "appointment",
            "entity_id": payload["new_appointment_id"],
        }
    ]


async def _handle_staff_request_submitted(session, payload: dict[str, Any]) -> list[dict]:
    """Notifies the reviewing regional_admin — staff_requests.regional_admin_id
    when set (the normal case, bound at region/clinic setup); falls back to
    resolving the clinic's region's regional_admin for older rows without it."""
    req = (
        (
            await session.execute(
                text("SELECT clinic_id, regional_admin_id, position_role FROM staff_requests WHERE request_id = :id"),
                {"id": payload["request_id"]},
            )
        )
        .mappings()
        .first()
    )
    if not req:
        return []
    recipient_id = req["regional_admin_id"]
    if not recipient_id:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT a.profile_id FROM clinics c "
                        "JOIN admins a ON a.region_id = c.region_id AND a.admin_type = 'regional_admin' "
                        "WHERE c.clinic_id = :cid"
                    ),
                    {"cid": req["clinic_id"]},
                )
            )
            .mappings()
            .first()
        )
        recipient_id = row["profile_id"] if row else None
    if not recipient_id:
        return []
    return [
        {
            # notifications.type is a fixed enum (SQL/12b_notifications.sql) with
            # no 'staff_request' value — 'admin' is the closest existing fit.
            "recipient_id": str(recipient_id),
            "type": "admin",
            "title": "New staff request",
            "body": f"A new {req['position_role']} request was submitted.",
            "entity_type": "staff_request",
            "entity_id": payload["request_id"],
        }
    ]


async def _handle_staff_request_decided(session, payload: dict[str, Any]) -> list[dict]:
    """Notifies the clinic_admin who originally submitted/referred it."""
    req = (
        (
            await session.execute(
                text("SELECT submitted_by, review_notes FROM staff_requests WHERE request_id = :id"), {"id": payload["request_id"]}
            )
        )
        .mappings()
        .first()
    )
    if not req:
        return []
    return [
        {
            "recipient_id": str(req["submitted_by"]),
            "type": "admin",
            "title": f"Your staff request was {payload['decision']}",
            "body": req["review_notes"],
            "entity_type": "staff_request",
            "entity_id": payload["request_id"],
        }
    ]


async def _handle_sos_raised(session, payload: dict[str, Any]) -> list[dict]:
    """Pages whoever is actually running the session — the CA who started it
    (appointments.ca_id, set at DeviceSessionService.start()) and the
    appointment's responsible doctor. Safety-critical, so both get it rather
    than picking one."""
    row = (
        (
            await session.execute(
                text("SELECT ca_id, doctor_id FROM appointments WHERE appointment_id = :id"),
                {"id": payload["appointment_id"]},
            )
        )
        .mappings()
        .first()
    )
    if not row:
        return []
    note_suffix = f" Note: {payload['note']}" if payload.get("note") else ""
    body = f"{payload.get('sos_type', 'SOS')} raised during a device session." + note_suffix
    return [
        {
            "recipient_id": str(recipient),
            "type": "clinical",
            "title": "SOS raised — patient needs attention",
            "body": body,
            "entity_type": "device_session",
            "entity_id": payload["appointment_id"],
        }
        for recipient in (row["ca_id"], row["doctor_id"])
        if recipient
    ]


async def _handle_sos_acknowledged(session, payload: dict[str, Any]) -> list[dict]:
    """Closes the loop back to the patient who raised it."""
    row = (
        (await session.execute(text("SELECT patient_id FROM appointments WHERE appointment_id = :id"), {"id": payload["appointment_id"]}))
        .mappings()
        .first()
    )
    if not row:
        return []
    return [
        {
            "recipient_id": str(row["patient_id"]),
            "type": "clinical",
            "title": "Your SOS has been acknowledged",
            "body": "A member of staff is attending to you.",
            "entity_type": "device_session",
            "entity_id": payload["appointment_id"],
        }
    ]


async def _handle_patient_registration_decided(session, payload: dict[str, Any]) -> list[dict]:
    """Notifies the patient whether their receptionist-reviewed registration
    was approved. payload's patient_id is patients.patient_id (the public
    id) — notifications.recipient_id needs profiles.id, hence the join."""
    row = (
        (
            await session.execute(
                text("SELECT profile_id, rejection_reason FROM patients WHERE patient_id = :id"),
                {"id": payload["patient_id"]},
            )
        )
        .mappings()
        .first()
    )
    if not row:
        return []
    approved = payload.get("decision") == "approved"
    return [
        {
            "recipient_id": str(row["profile_id"]),
            "type": "admin",
            "title": "Your registration has been approved" if approved else "Your registration was not approved",
            "body": "You can now book appointments." if approved else row["rejection_reason"],
            "entity_type": "patient",
            "entity_id": payload["patient_id"],
        }
    ]


async def _handle_device_session_ended(session, payload: dict[str, Any]) -> list[dict]:
    """device_session.completed / .stopped: the device-session service moves
    the appointment to 'completed' through the repository directly, so no
    appointment_status_changed event is emitted for it — this is that event."""
    if not payload.get("appointment_id"):
        return []
    return await _handle_appointment_status_changed(session, {"appointment_id": payload["appointment_id"], "status": "completed"})


EVENT_HANDLERS = {
    "appointment_booked": _handle_appointment_booked,
    # A patient claiming a slot on a doctor-prescribed (planned) session is
    # the same news as a booking: the slot is now held/confirmed.
    "appointment_slot_claimed": _handle_appointment_booked,
    "device_session.completed": _handle_device_session_ended,
    "device_session.stopped": _handle_device_session_ended,
    "appointment_paid": _handle_appointment_paid,
    "appointment_cancelled": _handle_appointment_cancelled,
    "appointment_status_changed": _handle_appointment_status_changed,
    "appointment_rescheduled": _handle_appointment_rescheduled,
    "staff_request_submitted": _handle_staff_request_submitted,
    "staff_request_decided": _handle_staff_request_decided,
    "registration_completed": _handle_registration_completed,
    "sos_raised": _handle_sos_raised,
    "sos_acknowledged": _handle_sos_acknowledged,
    "patient_registration_decided": _handle_patient_registration_decided,
}


async def _process_event(session, event: dict) -> None:
    handler = EVENT_HANDLERS.get(event["event_type"])
    if not handler:
        return
    payload = json.loads(event["payload"]) if isinstance(event["payload"], str) else event["payload"]
    notifications = await handler(session, payload)
    repo = NotificationRepository(session)
    for note in notifications:
        record = await repo.create(note)
        # The notifications row is the durable record; the Redis push only
        # makes it appear live. Redis being down must not roll the row back
        # (the event is marked published either way, so it would be lost).
        try:
            await publish_to_user(
                note["recipient_id"],
                json.dumps(
                    {
                        "type": record["type"],
                        "title": record["title"],
                        "body": record["body"],
                        "notification_id": str(record["notification_id"]),
                    }
                ),
            )
        except Exception as exc:
            RELAY_STATE["live_push_failures_total"] += 1
            logger.warning("event_relay_live_push_failed", recipient_id=str(note["recipient_id"]), error=repr(exc))


async def drain_outbox(limit: int = 100) -> int:
    """Processes up to `limit` unpublished events. Returns count processed.
    Exposed separately from run_forever() so tests/scripts can drain
    synchronously without starting the long-running listener.

    One transaction per event, claimed with FOR UPDATE SKIP LOCKED: every API
    instance runs this relay (app/main.py lifespan), and SKIP LOCKED is what
    stops two of them sending the same notification twice. A handler failure
    rolls back to a savepoint and is logged — the event is still marked
    published, so one bad notification never blocks the queue behind it."""
    processed = 0
    while processed < limit:
        async with _relay_session_factory() as session:
            async with session.begin():
                # RLS role 'system' (SQL/v1/93): the relay acts for the platform,
                # not a person. Without it, under the ordinary app login it saw
                # zero events and could neither mark them sent nor write
                # notifications — silently. Harmless under the master login.
                await session.execute(text("SELECT set_config('app.current_user_role', 'system', true)"))
                row = (
                    (
                        await session.execute(
                            text(
                                "SELECT * FROM outbox_events WHERE published_at IS NULL ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    break
                event = dict(row)
                try:
                    async with session.begin_nested():
                        await _process_event(session, event)
                except Exception as exc:
                    RELAY_STATE["handler_failures_total"] += 1
                    RELAY_STATE["last_error"] = f"{event['event_type']}: {exc!r}"[:500]
                    logger.exception("event_relay_handler_failed", outbox_id=str(event["outbox_id"]), event_type=event["event_type"])
                await session.execute(
                    text("UPDATE outbox_events SET published_at = NOW() WHERE outbox_id = :id"),
                    {"id": event["outbox_id"]},
                )
        processed += 1
        RELAY_STATE["processed_total"] += 1
        RELAY_STATE["last_event_at"] = time.time()
    RELAY_STATE["last_drain_at"] = time.time()
    return processed


async def relay_backlog() -> dict[str, Any]:
    """Undelivered outbox events as the relay itself sees them (same engine,
    same 'system' role) — 0 while events are clearly being created means the
    relay can't see them (RLS/login), a growing number means it isn't
    draining."""
    async with _relay_session_factory() as session:
        async with session.begin():
            await session.execute(text("SELECT set_config('app.current_user_role', 'system', true)"))
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT count(*) AS n, EXTRACT(EPOCH FROM now() - min(created_at)) AS oldest_s, current_user AS db_login "
                            "FROM outbox_events WHERE published_at IS NULL"
                        )
                    )
                )
                .mappings()
                .one()
            )
    return {"undelivered": int(row["n"]), "oldest_undelivered_seconds": row["oldest_s"], "db_login": row["db_login"]}


async def _heartbeat_forever() -> None:
    """One INFO line every 5 minutes, so the relay is visible in the logs even
    when nothing goes wrong (failures alone used to be the only trace)."""
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        try:
            backlog = await relay_backlog()
        except Exception as exc:
            backlog = {"error": repr(exc)}
        logger.info(
            "event_relay_heartbeat",
            processed_total=RELAY_STATE["processed_total"],
            handler_failures_total=RELAY_STATE["handler_failures_total"],
            live_push_failures_total=RELAY_STATE["live_push_failures_total"],
            **backlog,
        )


async def _listen_and_drain() -> None:
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    wake = asyncio.Event()
    await conn.add_listener("outbox_new_event", lambda *_: wake.set())
    try:
        while True:
            n = await drain_outbox()
            if n:
                logger.info("event_relay_drained", count=n)
            wake.clear()
            try:
                await asyncio.wait_for(wake.wait(), timeout=POLL_INTERVAL_SECONDS)
            except TimeoutError:
                pass
    finally:
        await conn.close()


async def run_forever() -> None:
    """Background loop started from the FastAPI lifespan (app/main.py). A DB
    blip (dropped LISTEN connection, failed drain) restarts the listener
    after a pause instead of silently ending notifications for good."""
    logger.info("event_relay_started")
    RELAY_STATE["started_at"] = time.time()
    heartbeat = asyncio.create_task(_heartbeat_forever())
    try:
        while True:
            try:
                await _listen_and_drain()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                RELAY_STATE["last_error"] = repr(exc)[:500]
                logger.exception("event_relay_crashed_restarting", error=str(exc))
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
    finally:
        heartbeat.cancel()
        await _relay_engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_forever())
