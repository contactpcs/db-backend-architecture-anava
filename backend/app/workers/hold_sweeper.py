"""Releases expired appointment holds.

A 'selected' appointment occupies a slot it has not paid for. hold_expires_at
(31 §1) is the only thing that lets that slot come back when the payment never
lands — without this worker, one abandoned checkout blocks its slot forever and
a busy calendar silently fills with dead holds nobody can see.

WHAT IT DOES, and why the two branches differ:

    plan_id IS NULL      patient-booked (initial / follow_up). The row is
                         DELETED. There is no earlier state to fall back to,
                         nothing unpaid should persist, and the patient simply
                         books again from an empty screen. Deleting also frees
                         uq_one_active_initial_per_patient immediately.

    plan_id IS NOT NULL  protocol-born (device_session / protocol_followup).
                         Reverts to 'planned' with its time cleared. The
                         doctor's prescribed DATE survives; only the slot is
                         released. Deleting these would destroy part of a
                         prescription because a payment timed out.

Both branches null start_time and end_time together (chk_appointments_time_pair)
and clear hold_expires_at (chk_appointments_hold permits an expiry only on
'selected').

WHILE PAYMENT IS BYPASSED this worker finds nothing: with
appointment_payment_required = False, bookings land on 'paid' with no hold at
all. It is built and wired now so the transition is exercised and tested before
any gateway exists, and so enabling payment is one config flag rather than a
new deployment.
"""

import asyncio

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.core.db import get_migration_engine

logger = structlog.get_logger()

# Arbitrary but fixed app-wide key, distinct from the partition worker's. Every
# API instance runs this loop, so without the lock N uvicorn workers across M
# instances would all sweep at once — mostly harmless, but they would race each
# other's DELETEs and log misleading counts.
HOLD_SWEEP_LOCK_KEY = 8412772  # "anava:hold-sweeper"

_engine = get_migration_engine()
_session_factory = async_sessionmaker(_engine, expire_on_commit=False, autoflush=False)


async def sweep_once() -> dict:
    """One pass. Safe to call from every API instance concurrently.

    pg_try_advisory_xact_lock is transaction-scoped: it releases on commit,
    rollback or disconnect, so a crashed instance cannot wedge the lock.

    Runs as the 'system' role, not as a person. appointments has RLS forced and
    this worker has no logged-in user, so without app.current_user_role set the
    role helper returns NULL, no policy matches, and the UPDATE/DELETE would
    affect ZERO ROWS — silently, not as an error. That exact failure already
    happened once with the Razorpay webhook (25_webhook_system_role_rls.sql);
    rls_appt_delete and rls_appt_update (31 §6) admit 'system' precisely so this
    worker can do its job, and naming it 'system' rather than 'super_admin'
    keeps the audit trail honest about who acted.
    """
    async with _session_factory() as session:
        async with session.begin():
            got_lock = (await session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": HOLD_SWEEP_LOCK_KEY})).scalar()
            if not got_lock:
                return {"skipped": True}

            await session.execute(text("SELECT set_config('app.current_user_role', 'system', true)"))

            reverted = await session.execute(
                text(
                    "UPDATE appointments SET status = 'planned', start_time = NULL, end_time = NULL, "
                    "hold_expires_at = NULL, updated_at = NOW() "
                    "WHERE status = 'selected' AND hold_expires_at < NOW() AND plan_id IS NOT NULL"
                )
            )
            deleted = await session.execute(
                text("DELETE FROM appointments WHERE status = 'selected' AND hold_expires_at < NOW() AND plan_id IS NULL")
            )
            result = {
                "reverted_to_planned": reverted.rowcount or 0,
                "deleted": deleted.rowcount or 0,
                "skipped": False,
            }

    if result["reverted_to_planned"] or result["deleted"]:
        logger.info("appointment_holds_released", **result)
    return result


async def run_hold_sweeper_forever() -> None:
    """Background loop started from the FastAPI lifespan — see app/main.py.

    The interval is deliberately well under the hold window, so a released slot
    becomes bookable again promptly rather than up to a whole window late.
    """
    settings = get_settings()
    interval = settings.appointment_hold_sweep_interval_seconds
    logger.info("hold_sweeper_started", interval_seconds=interval, hold_minutes=settings.appointment_hold_minutes)
    while True:
        try:
            await sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad pass kill the loop
            logger.exception("hold_sweeper_pass_failed", error=str(exc))
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(run_hold_sweeper_forever())
