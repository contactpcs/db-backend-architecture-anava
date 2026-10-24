"""One reminder for session feedback nobody has given yet.

Feedback is asked the moment a visit is marked completed (event_relay.py turns
the completion into a "How was your session today?" notification, pushed live
so the app opens the feedback sheet at once). This worker adds a single
reminder, session_feedback_reminder_after_hours later, to patients who still
haven't answered, inside the 48-hour window the form stays open for.

It only emits an outbox event; event_relay.py writes the notification and the
live push. Two guards keep it to one reminder per visit: the NOT EXISTS checks
below (an earlier reminder notification, or a reminder event still waiting in
the outbox), and the advisory lock that stops every API instance sweeping at
once. Closed when feedback exists: the relay handler checks that again.

Runs as RLS role 'system', like the hold sweeper.
"""

import asyncio

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.core.db import get_worker_engine
from app.core.events import emit_event

logger = structlog.get_logger()

FEEDBACK_REMINDER_LOCK_KEY = 8412773  # "anava:feedback-reminder", next to the hold sweeper's key
FEEDBACK_WINDOW_HOURS = 48

_session_factory = async_sessionmaker(get_worker_engine(), expire_on_commit=False, autoflush=False)

# Completed more than N hours ago (completed_at is set by the status change,
# see scheduling/repository.update_status), still inside the window,
# no feedback row, and no reminder yet, sent or queued.
_DUE_SQL = text(
    "SELECT a.appointment_id FROM appointments a "
    "WHERE a.status = 'completed' "
    "AND a.completed_at <= now() - make_interval(secs => :after_secs) "
    "AND a.completed_at >= now() - make_interval(hours => :window_hours) "
    "AND NOT EXISTS (SELECT 1 FROM session_feedback f WHERE f.appointment_id = a.appointment_id) "
    "AND NOT EXISTS (SELECT 1 FROM notifications n WHERE n.entity_type = 'session_feedback' AND n.entity_id = a.appointment_id "
    "  AND n.metadata ->> 'kind' = 'reminder') "
    "AND NOT EXISTS (SELECT 1 FROM outbox_events o WHERE o.event_type = 'session_feedback_reminder' "
    "  AND o.aggregate_id = a.appointment_id::text AND o.failed_at IS NULL) "
    "ORDER BY a.completed_at LIMIT 200"
)


async def remind_once() -> dict:
    """One pass. Safe to call from every API instance at once."""
    settings = get_settings()
    async with _session_factory() as session:
        async with session.begin():
            got_lock = (await session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": FEEDBACK_REMINDER_LOCK_KEY})).scalar()
            if not got_lock:
                return {"skipped": True}
            await session.execute(text("SELECT set_config('app.current_user_role', 'system', true)"))
            due = (
                await session.execute(
                    _DUE_SQL,
                    {"after_secs": settings.session_feedback_reminder_after_hours * 3600, "window_hours": FEEDBACK_WINDOW_HOURS},
                )
            ).fetchall()
            for (appointment_id,) in due:
                await emit_event(
                    session,
                    aggregate_type="appointment",
                    aggregate_id=appointment_id,
                    event_type="session_feedback_reminder",
                    payload={"appointment_id": str(appointment_id)},
                )
    if due:
        logger.info("session_feedback_reminders_queued", count=len(due))
    return {"queued": len(due), "skipped": False}


async def run_feedback_reminder_forever() -> None:
    settings = get_settings()
    interval = settings.session_feedback_reminder_interval_seconds
    logger.info("feedback_reminder_started", interval_seconds=interval, after_hours=settings.session_feedback_reminder_after_hours)
    while True:
        try:
            await remind_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad pass kill the loop
            logger.exception("feedback_reminder_pass_failed", error=str(exc))
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(run_feedback_reminder_forever())
