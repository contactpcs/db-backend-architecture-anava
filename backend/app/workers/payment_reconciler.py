"""Makes sure money captured at Razorpay is always recorded (audit H1 —
Documents/design_payment_audit_fixes.md).

A payment becomes 'paid' only when the patient's browser (verify) or
Razorpay's webhook reports it. If both are missed — the tab was closed right
after paying and the webhook was delayed, misconfigured or down — the hold
sweeper cancels the booking and fails the payment while Razorpay keeps the
money. This worker closes that gap by asking Razorpay directly:

    pending  older than the grace period     -> was it captured?
    failed   closed by us within lookback    -> was it captured after all?

A captured attempt goes through PaymentService.update_status like any other
confirmation: the booking is confirmed if it still awaits payment, otherwise
the capture is recorded and refunded in full by the refund worker. Amounts
are checked against what was charged first.
"""

import asyncio

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.core.db import get_worker_engine
from app.integrations import razorpay as razorpay_client
from app.modules.payments.repository import PaymentRepository
from app.modules.payments.service import PaymentService

logger = structlog.get_logger()

RECONCILE_LOCK_KEY = 8412775  # "anava:payment-reconciler"

_engine = get_worker_engine()
_session_factory = async_sessionmaker(_engine, expire_on_commit=False, autoflush=False)


async def process_batch(session) -> dict:
    """One batch inside the caller's transaction (role already 'system')."""
    settings = get_settings()
    done = {"confirmed": 0, "refund_queued": 0, "not_captured": 0, "amount_mismatch": 0, "errors": 0}
    service = PaymentService(session)
    rows = await PaymentRepository(session).list_reconcilable(
        grace_seconds=settings.payment_reconciler_grace_seconds, lookback_hours=settings.payment_reconciler_lookback_hours
    )
    for payment in rows:
        # One savepoint per payment: one bad row never undoes the others.
        try:
            async with session.begin_nested():
                done[await service.reconcile(payment)] += 1
        except Exception as exc:  # gateway unreachable, or a rule refused it
            done["errors"] += 1
            logger.warning("payment_reconcile_error", payment_id=str(payment["payment_id"]), error=str(exc)[:200])
    return done


async def process_once() -> dict:
    if not razorpay_client.is_configured():
        return {"skipped": True}
    async with _session_factory() as session:
        async with session.begin():
            got_lock = (await session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": RECONCILE_LOCK_KEY})).scalar()
            if not got_lock:
                return {"skipped": True}
            await session.execute(text("SELECT set_config('app.current_user_role', 'system', true)"))
            done = await process_batch(session)
    if done["confirmed"] or done["refund_queued"] or done["amount_mismatch"]:
        logger.info("payment_reconciler_pass", **done)
    return done


async def run_payment_reconciler_forever() -> None:
    settings = get_settings()
    interval = settings.payment_reconciler_interval_seconds
    logger.info("payment_reconciler_started", interval_seconds=interval)
    while True:
        try:
            await process_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad pass kill the loop
            logger.exception("payment_reconciler_pass_failed", error=str(exc))
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(run_payment_reconciler_forever())
