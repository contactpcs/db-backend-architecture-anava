"""Auto-marks an unattended appointment 'no_show' instead of leaving it stuck
at 'paid'/'checked_in' forever with nobody noticing (see the eng review this
came out of: a missed, paid appointment was visually indistinguishable from a
normal upcoming one, forever, unless a staff member happened to manually flag
it — which nothing ever prompted them to do).

TWO INDEPENDENT BRANCHES, each its own grace window (app/config.py):

    paid, never checked in       -> no_show once appointment_no_show_paid_grace_hours
                                     have passed since the slot's own start time.
                                     appointment_date/start_time are naive
                                     wall-clock IST columns (see scheduling/
                                     service.py's own _now_ist_naive — reused
                                     here rather than re-deriving the same
                                     IST cutoff a second, driftable way), so
                                     this branch compares against a Python-
                                     computed IST cutoff, not raw SQL NOW()
                                     (which is UTC and would silently shift
                                     the cutoff by 5:30 hours — the exact bug
                                     _now_ist_naive's own docstring names).

    checked_in, session never    -> no_show once
    started/finished                appointment_no_show_checked_in_grace_hours
                                     have passed since checked_in_at, a real
                                     TIMESTAMPTZ column — safe to compare
                                     against SQL NOW() directly.

Once no_show, PatientBookingService.reschedule_own (scheduling/service.py)
now accepts it as a valid rescheduling source — this worker's whole point is
to get a missed slot INTO that reschedulable state automatically instead of
waiting on a staff member to notice and mark it by hand.
"""

import asyncio
import datetime as dt

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.core.db import get_migration_engine
from app.core.events import emit_event
from app.modules.scheduling.service import _now_ist_naive

logger = structlog.get_logger()

# Distinct from hold_sweeper's key — same reasoning (every API instance runs
# this loop; without the lock, N workers across M instances would race each
# other's UPDATEs).
NO_SHOW_SWEEP_LOCK_KEY = 8412773  # "anava:no-show-sweeper"

_engine = get_migration_engine()
_session_factory = async_sessionmaker(_engine, expire_on_commit=False, autoflush=False)


async def sweep_once() -> dict:
    """One pass. Safe to call from every API instance concurrently — same
    pg_try_advisory_xact_lock pattern as hold_sweeper.sweep_once, and the same
    'system' RLS role for the same reason (appointments has RLS forced; a
    worker with no logged-in user needs app.current_user_role admitted
    explicitly by rls_appt_update, or the UPDATE silently touches zero rows)."""
    settings = get_settings()
    paid_cutoff = _now_ist_naive() - dt.timedelta(hours=settings.appointment_no_show_paid_grace_hours)

    async with _session_factory() as session:
        async with session.begin():
            got_lock = (await session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": NO_SHOW_SWEEP_LOCK_KEY})).scalar()
            if not got_lock:
                return {"skipped": True}

            await session.execute(text("SELECT set_config('app.current_user_role', 'system', true)"))

            # -- branch 1: paid, slot time + grace hours has passed, never checked in --
            await session.execute(
                text(
                    "INSERT INTO appointment_audit_logs (appointment_id, changed_by, changed_by_role, "
                    "previous_status, new_status, change_reason) "
                    "SELECT appointment_id, NULL, 'system', 'paid', 'no_show', "
                    "'Slot time passed without check-in' "
                    "FROM appointments WHERE status = 'paid' AND (appointment_date + start_time) < :cutoff"
                ),
                {"cutoff": paid_cutoff},
            )
            paid_result = await session.execute(
                text(
                    "UPDATE appointments SET status = 'no_show', updated_at = NOW() "
                    "WHERE status = 'paid' AND (appointment_date + start_time) < :cutoff "
                    "RETURNING appointment_id"
                ),
                {"cutoff": paid_cutoff},
            )
            paid_ids = [row[0] for row in paid_result.fetchall()]

            # -- branch 2: checked in, session never started/finished --
            await session.execute(
                text(
                    "INSERT INTO appointment_audit_logs (appointment_id, changed_by, changed_by_role, "
                    "previous_status, new_status, change_reason) "
                    "SELECT appointment_id, NULL, 'system', 'checked_in', 'no_show', "
                    "'Checked in but session never started or completed' "
                    "FROM appointments WHERE status = 'checked_in' "
                    "AND checked_in_at < NOW() - (:hours || ' hours')::interval"
                ),
                {"hours": settings.appointment_no_show_checked_in_grace_hours},
            )
            checked_in_result = await session.execute(
                text(
                    "UPDATE appointments SET status = 'no_show', updated_at = NOW() "
                    "WHERE status = 'checked_in' AND checked_in_at < NOW() - (:hours || ' hours')::interval "
                    "RETURNING appointment_id"
                ),
                {"hours": settings.appointment_no_show_checked_in_grace_hours},
            )
            checked_in_ids = [row[0] for row in checked_in_result.fetchall()]

            # Reuses the same event_type the manual PATCH .../status path
            # emits (scheduling/service.py::update_status) — event_relay.py's
            # existing _handle_appointment_status_changed handler needs no
            # new wiring, just a 'no_show' entry in its _STATUS_TITLES.
            for appointment_id in (*paid_ids, *checked_in_ids):
                await emit_event(
                    session,
                    aggregate_type="appointment",
                    aggregate_id=appointment_id,
                    event_type="appointment_status_changed",
                    payload={"appointment_id": str(appointment_id), "status": "no_show", "changed_by_role": "system"},
                )

            result = {
                "no_show_from_paid": len(paid_ids),
                "no_show_from_checked_in": len(checked_in_ids),
                "skipped": False,
            }

    if result["no_show_from_paid"] or result["no_show_from_checked_in"]:
        logger.info("appointments_auto_no_show", **result)
    return result


async def run_no_show_sweeper_forever() -> None:
    """Background loop started from the FastAPI lifespan — see app/main.py."""
    settings = get_settings()
    interval = settings.appointment_no_show_sweep_interval_seconds
    logger.info(
        "no_show_sweeper_started",
        interval_seconds=interval,
        paid_grace_hours=settings.appointment_no_show_paid_grace_hours,
        checked_in_grace_hours=settings.appointment_no_show_checked_in_grace_hours,
    )
    while True:
        try:
            await sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad pass kill the loop
            logger.exception("no_show_sweeper_pass_failed", error=str(exc))
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(run_no_show_sweeper_forever())
