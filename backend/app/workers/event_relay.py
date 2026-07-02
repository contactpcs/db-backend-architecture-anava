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

from app.config import get_settings
from app.core.db import async_session_factory, engine
from app.core.pubsub import publish_to_user
from app.modules.notifications.repository import NotificationRepository

logger = structlog.get_logger()
settings = get_settings()

POLL_INTERVAL_SECONDS = 5.0


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
    """Notifies the patient their registration is complete."""
    row = (
        await session.execute(
            text("SELECT profile_id FROM patients WHERE patient_id = :id"), {"id": payload["patient_id"]}
        )
    ).mappings().first()
    if not row:
        return []
    return [{
        "recipient_id": str(row["profile_id"]), "type": "clinical", "title": "Registration complete",
        "body": "Your registration is complete — a doctor has been assigned to your care.",
        "entity_type": "patient", "entity_id": payload["patient_id"],
    }]


EVENT_HANDLERS = {
    "appointment_booked": _handle_appointment_booked,
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
    synchronously without starting the long-running listener."""
    processed = 0
    async with async_session_factory() as session:
        async with session.begin():
            rows = (
                await session.execute(
                    text("SELECT * FROM outbox_events WHERE published_at IS NULL ORDER BY created_at LIMIT 100")
                )
            ).mappings().all()
            for row in rows:
                await _process_event(session, dict(row))
                await session.execute(
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
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_forever())
