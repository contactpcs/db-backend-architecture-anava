"""Releases expired appointment holds.

A 'selected' appointment occupies a slot it has not paid for. hold_expires_at
(31 §1) is the only thing that lets that slot come back when the payment never
lands — without this worker, one abandoned checkout blocks its slot forever and
a busy calendar silently fills with dead holds nobody can see.

WHAT IT DOES, and why the two branches differ:

    no protocol_id       patient-booked (initial / follow_up). The row moves
                         to 'cancelled' (cancellation_reason set), same as any
                         other cancellation — already excluded from
                         uq_one_active_initial_per_patient and from the
                         slot-occupying set, so the patient can book again and
                         the slot frees immediately. The attempt survives in
                         appointment history + payments history instead of
                         vanishing, so the patient portal can show it as a
                         locked, failed attempt rather than nothing at all.

    protocol_id set      protocol-born (device_session / protocol_followup).
                         Reverts to 'planned' with its time cleared. The
                         doctor's prescribed DATE survives; only the slot is
                         released. Deleting these would destroy part of a
                         prescription because a payment timed out.

Both branches null hold_expires_at (chk_appointments_hold permits an expiry
only on 'selected'). Only the protocol-born branch also nulls start_time/
end_time — a cancelled patient-booked row keeps the slot it failed to pay for,
same as any other cancelled appointment.

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
                    "WHERE status = 'selected' AND hold_expires_at < NOW() "
                    "AND protocol_id IS NOT NULL"
                )
            )
            # Only 'pending'/'failed' payments can ever be attached to a
            # still-'selected' appointment — a 'paid' one would have already
            # moved the appointment out of 'selected' via mark_paid() — so
            # flipping them to 'failed' here is safe: nothing that ever
            # completed is at risk.
            #
            # Logged first, same as any other payment status change — an
            # abandoned checkout is exactly the case worth a durable "why did
            # this never complete" record.
            await session.execute(
                text(
                    "INSERT INTO payment_logs (payment_id, status, amount, currency, payment_method, "
                    "razorpay_order_id, razorpay_payment_id, source, failure_reason, changed_by_role) "
                    "SELECT payment_id, 'failed', amount, currency, payment_method, razorpay_order_id, razorpay_payment_id, "
                    "'system', 'Booking hold expired before payment completed', 'system' "
                    "FROM payments WHERE appointment_id IN ("
                    "SELECT appointment_id FROM appointments "
                    "WHERE status = 'selected' AND hold_expires_at < NOW() AND protocol_id IS NULL"
                    ")"
                )
            )
            await session.execute(
                text(
                    "UPDATE payments SET status = 'failed', updated_at = NOW() WHERE appointment_id IN ("
                    "SELECT appointment_id FROM appointments "
                    "WHERE status = 'selected' AND hold_expires_at < NOW() AND protocol_id IS NULL"
                    ")"
                )
            )
            # Audit row before the status flip, off the same still-'selected'
            # snapshot — appointment_audit_logs is the durable, patient-visible
            # record of the attempt; the appointments row itself moves on to
            # 'cancelled' right after (same table, same statement below).
            await session.execute(
                text(
                    "INSERT INTO appointment_audit_logs (appointment_id, changed_by, changed_by_role, "
                    "previous_status, new_status, change_reason) "
                    "SELECT appointment_id, NULL, 'system', 'selected', 'cancelled', "
                    "'Payment not completed within hold window' "
                    "FROM appointments WHERE status = 'selected' AND hold_expires_at < NOW() AND protocol_id IS NULL"
                )
            )
            cancelled = await session.execute(
                text(
                    "UPDATE appointments SET status = 'cancelled', hold_expires_at = NULL, "
                    "cancellation_reason = 'Payment not completed within hold window', updated_at = NOW() "
                    "WHERE status = 'selected' AND hold_expires_at < NOW() AND protocol_id IS NULL"
                )
            )
            result = {
                # CursorResult has rowcount; the async Result stubs don't expose
                # it. Same ignore the repository layer already uses.
                "reverted_to_planned": reverted.rowcount or 0,  # type: ignore[attr-defined]
                "cancelled_unpaid": cancelled.rowcount or 0,  # type: ignore[attr-defined]
                "skipped": False,
            }

    if result["reverted_to_planned"] or result["cancelled_unpaid"]:
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
