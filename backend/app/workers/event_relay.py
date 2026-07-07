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
    """Notifies the doctor a new appointment was booked on their calendar."""
    doctor_id = payload.get("doctor_id")
    if not doctor_id:
        return []
    return [{
        "recipient_id": doctor_id, "type": "appointment", "title": "New appointment booked",
        "body": f"Appointment {payload.get('appointment_id')} was booked on your calendar.",
        "entity_type": "appointment", "entity_id": payload.get("appointment_id"),
    }]


async def _handle_registration_completed(session, payload: dict[str, Any]) -> list[dict]:
    """Notifies the patient their registration is complete, and — for a
    self-registered patient specifically — the clinic's receptionists that a
    new approval request is waiting (staff-registered patients skip approval
    entirely, so they'd have nothing to act on)."""
    row = (
        await session.execute(
            text(
                "SELECT profile_id, self_registered, approval_status, primary_clinic_id "
                "FROM patients WHERE patient_id = :id"
            ),
            {"id": payload["patient_id"]},
        )
    ).mappings().first()
    if not row:
        return []
    notifications = [{
        "recipient_id": str(row["profile_id"]), "type": "clinical", "title": "Registration complete",
        "body": "Your registration is complete — a doctor has been assigned to your care.",
        "entity_type": "patient", "entity_id": payload["patient_id"],
    }]
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
        notifications.extend({
            # notifications.type is a fixed enum (SQL/12b_notifications.sql)
            # with no 'patient_approval' value — 'admin' is the closest fit.
            "recipient_id": str(r.profile_id), "type": "admin", "title": "New patient awaiting approval",
            "body": "A self-registered patient has completed registration and needs review.",
            "entity_type": "patient", "entity_id": payload["patient_id"],
        } for r in receptionists)
    return notifications


async def _handle_appointment_cancelled(session, payload: dict[str, Any]) -> list[dict]:
    """Notifies whichever side didn't do the cancelling."""
    appt = (
        await session.execute(
            text("SELECT patient_id, doctor_id, cancellation_reason FROM appointments WHERE appointment_id = :id"),
            {"id": payload["appointment_id"]},
        )
    ).mappings().first()
    if not appt:
        return []
    cancelled_by_role = payload.get("changed_by_role")
    recipient = appt["doctor_id"] if cancelled_by_role == "patient" else appt["patient_id"]
    return [{
        "recipient_id": str(recipient), "type": "appointment", "title": "Appointment cancelled",
        "body": appt["cancellation_reason"], "entity_type": "appointment", "entity_id": payload["appointment_id"],
    }]


_STATUS_TITLES = {"confirmed": "Your appointment is confirmed", "completed": "Your appointment is complete"}


async def _handle_appointment_status_changed(session, payload: dict[str, Any]) -> list[dict]:
    """Only confirmed/completed are patient-facing news — checked_in/
    in_progress are internal workflow states nobody needs pushed to them."""
    title = _STATUS_TITLES.get(payload.get("status"))
    if not title:
        return []
    row = (
        await session.execute(text("SELECT patient_id FROM appointments WHERE appointment_id = :id"), {"id": payload["appointment_id"]})
    ).mappings().first()
    if not row:
        return []
    return [{
        "recipient_id": str(row["patient_id"]), "type": "appointment", "title": title,
        "entity_type": "appointment", "entity_id": payload["appointment_id"],
    }]


async def _handle_appointment_rescheduled(session, payload: dict[str, Any]) -> list[dict]:
    row = (
        await session.execute(
            text("SELECT patient_id, appointment_date, start_time FROM appointments WHERE appointment_id = :id"),
            {"id": payload["new_appointment_id"]},
        )
    ).mappings().first()
    if not row:
        return []
    return [{
        "recipient_id": str(row["patient_id"]), "type": "appointment", "title": "Your appointment was rescheduled",
        "body": f"New time: {row['appointment_date']} at {row['start_time']}.",
        "entity_type": "appointment", "entity_id": payload["new_appointment_id"],
    }]


async def _handle_appointment_request_submitted(session, payload: dict[str, Any]) -> list[dict]:
    """Notifies every active receptionist at the request's clinic."""
    req = (
        await session.execute(
            text("SELECT clinic_id, request_type FROM appointment_requests WHERE request_id = :id"), {"id": payload["request_id"]}
        )
    ).mappings().first()
    if not req:
        return []
    receptionists = (
        await session.execute(
            text(
                "SELECT profile_id FROM clinic_staff_assignments "
                "WHERE clinic_id = :cid AND staff_role = 'receptionist' AND is_active = TRUE"
            ),
            {"cid": req["clinic_id"]},
        )
    ).all()
    return [
        {
            "recipient_id": str(r.profile_id), "type": "appointment", "title": "New appointment request",
            "body": f"A patient submitted a {req['request_type']} appointment request.",
            "entity_type": "appointment_request", "entity_id": payload["request_id"],
        }
        for r in receptionists
    ]


async def _handle_appointment_request_decided(session, payload: dict[str, Any]) -> list[dict]:
    req = (
        await session.execute(
            text("SELECT patient_id, review_notes FROM appointment_requests WHERE request_id = :id"), {"id": payload["request_id"]}
        )
    ).mappings().first()
    if not req:
        return []
    return [{
        "recipient_id": str(req["patient_id"]), "type": "appointment",
        "title": f"Your appointment request was {payload['decision']}",
        "body": req["review_notes"], "entity_type": "appointment_request", "entity_id": payload["request_id"],
    }]


async def _handle_staff_request_submitted(session, payload: dict[str, Any]) -> list[dict]:
    """Notifies the reviewing regional_admin — staff_requests.regional_admin_id
    when set (the normal case, bound at region/clinic setup); falls back to
    resolving the clinic's region's regional_admin for older rows without it."""
    req = (
        await session.execute(
            text("SELECT clinic_id, regional_admin_id, position_role FROM staff_requests WHERE request_id = :id"),
            {"id": payload["request_id"]},
        )
    ).mappings().first()
    if not req:
        return []
    recipient_id = req["regional_admin_id"]
    if not recipient_id:
        row = (
            await session.execute(
                text(
                    "SELECT a.profile_id FROM clinics c "
                    "JOIN admins a ON a.region_id = c.region_id AND a.admin_type = 'regional_admin' "
                    "WHERE c.clinic_id = :cid"
                ),
                {"cid": req["clinic_id"]},
            )
        ).mappings().first()
        recipient_id = row["profile_id"] if row else None
    if not recipient_id:
        return []
    return [{
        # notifications.type is a fixed enum (SQL/12b_notifications.sql) with
        # no 'staff_request' value — 'admin' is the closest existing fit.
        "recipient_id": str(recipient_id), "type": "admin", "title": "New staff request",
        "body": f"A new {req['position_role']} request was submitted.",
        "entity_type": "staff_request", "entity_id": payload["request_id"],
    }]


async def _handle_staff_request_decided(session, payload: dict[str, Any]) -> list[dict]:
    """Notifies the clinic_admin who originally submitted/referred it."""
    req = (
        await session.execute(
            text("SELECT submitted_by, review_notes FROM staff_requests WHERE request_id = :id"), {"id": payload["request_id"]}
        )
    ).mappings().first()
    if not req:
        return []
    return [{
        "recipient_id": str(req["submitted_by"]), "type": "admin",
        "title": f"Your staff request was {payload['decision']}",
        "body": req["review_notes"], "entity_type": "staff_request", "entity_id": payload["request_id"],
    }]


EVENT_HANDLERS = {
    "appointment_booked": _handle_appointment_booked,
    "appointment_cancelled": _handle_appointment_cancelled,
    "appointment_status_changed": _handle_appointment_status_changed,
    "appointment_rescheduled": _handle_appointment_rescheduled,
    "appointment_request_submitted": _handle_appointment_request_submitted,
    "appointment_request_decided": _handle_appointment_request_decided,
    "staff_request_submitted": _handle_staff_request_submitted,
    "staff_request_decided": _handle_staff_request_decided,
    "registration_completed": _handle_registration_completed,
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
        await publish_to_user(note["recipient_id"], json.dumps({
            "type": record["type"], "title": record["title"], "body": record["body"],
            "notification_id": str(record["notification_id"]),
        }))


async def drain_outbox() -> int:
    """Processes all currently-unpublished events once. Returns count processed.
    Exposed separately from run_forever() so tests/scripts can drain
    synchronously without starting the long-running listener.

    Each row gets its own transaction, and a handler failure is caught and
    logged rather than left to propagate — one bad notification (e.g. a
    handler using a type value the notifications table's CHECK constraint
    rejects) must never take down every future notification with it, which
    is exactly what letting one shared transaction/loop crash used to do."""
    async with _relay_session_factory() as session:
        async with session.begin():
            rows = (
                await session.execute(
                    text("SELECT * FROM outbox_events WHERE published_at IS NULL ORDER BY created_at LIMIT 100")
                )
            ).mappings().all()
            rows = [dict(r) for r in rows]

    processed = 0
    for row in rows:
        async with _relay_session_factory() as session:
            try:
                async with session.begin():
                    await _process_event(session, row)
                    await session.execute(
                        text("UPDATE outbox_events SET published_at = NOW() WHERE outbox_id = :id"),
                        {"id": row["outbox_id"]},
                    )
            except Exception:
                logger.exception("event_relay_handler_failed", outbox_id=str(row["outbox_id"]), event_type=row["event_type"])
                async with _relay_session_factory() as cleanup_session:
                    async with cleanup_session.begin():
                        await cleanup_session.execute(
                            text("UPDATE outbox_events SET published_at = NOW() WHERE outbox_id = :id"),
                            {"id": row["outbox_id"]},
                        )
        processed += 1
    return processed


async def run_forever() -> None:
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    wake = asyncio.Event()
    await conn.add_listener("outbox_new_event", lambda *_: wake.set())
    logger.info("event_relay_started")
    try:
        while True:
            n = await drain_outbox()
            if n:
                logger.info("event_relay_drained", count=n)
            wake.clear()
            try:
                await asyncio.wait_for(wake.wait(), timeout=POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        await conn.close()
        await _relay_engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_forever())
