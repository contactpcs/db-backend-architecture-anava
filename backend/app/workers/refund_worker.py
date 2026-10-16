"""Completes online refunds for cancelled paid appointments (design D3 —
Documents/design_appointment_policy_and_refunds.md).

A cancellation only records what is owed (payments.refund_status =
'pending'). Moving money is done here, outside the cancel request's
transaction, so a gateway call can never be made for a cancellation that
later rolls back:

    pending, no refund id   create the Razorpay refund (or adopt one the
                            gateway already has for this payment — guards
                            against a pass whose DB write was lost after the
                            gateway call succeeded)
    pending, refund id set  poll its status
    processed               payments.status -> refunded / partially_refunded
    failed                  logged, left for staff

Cash payments have no razorpay_payment_id and are never picked up — the
front desk confirms those (PATCH /payments/{id}/status refunded). Webhooks
refund.processed / refund.failed reach the same end state; whichever lands
first wins.
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

REFUND_LOCK_KEY = 8412774  # "anava:refund-worker"

_engine = get_worker_engine()
_session_factory = async_sessionmaker(_engine, expire_on_commit=False, autoflush=False)


async def process_once() -> dict:
    async with _session_factory() as session:
        async with session.begin():
            got_lock = (await session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": REFUND_LOCK_KEY})).scalar()
            if not got_lock:
                return {"processed": 0, "waiting": 0, "failed": 0, "skipped": True}
            await session.execute(text("SELECT set_config('app.current_user_role', 'system', true)"))
            done = await process_batch(session)

    if done["processed"] or done["failed"] or done["waiting"]:
        logger.info("refund_worker_pass", **done)
    return done


async def process_batch(session) -> dict:
    """One batch inside the caller's transaction (role already 'system')."""
    done = {"processed": 0, "waiting": 0, "failed": 0, "skipped": False}
    service = PaymentService(session)
    for payment in await PaymentRepository(session).list_pending_online_refunds():
        # One savepoint per payment: a failure on one row never rolls
        # back the refunds already recorded for the others.
        async with session.begin_nested():
            try:
                if payment["razorpay_refund_id"]:
                    refund = razorpay_client.fetch_refund(payment["razorpay_refund_id"])
                else:
                    refund = razorpay_client.refund_payment(
                        razorpay_payment_id=payment["razorpay_payment_id"],
                        amount=float(payment["cancellation_refund_amount"]),
                        notes={"payment_id": str(payment["payment_id"]), "appointment_id": str(payment["appointment_id"])},
                    )
            except Exception as exc:  # gateway refused or unreachable
                await service.apply_refund_result(
                    payment, razorpay_refund_id=None, refund_status="failed", source="system", failure_reason=str(exc)[:500]
                )
                done["failed"] += 1
                continue

            gateway_status = refund.get("status")
            outcome = "processed" if gateway_status == "processed" else "failed" if gateway_status == "failed" else "pending"
            if outcome == "pending" and payment["razorpay_refund_id"]:
                done["waiting"] += 1  # already recorded, nothing new to log
                continue
            await service.apply_refund_result(
                payment,
                razorpay_refund_id=refund.get("id"),
                refund_status=outcome,
                source="system",
                gateway_response=refund,
                failure_reason="Razorpay reported the refund as failed" if outcome == "failed" else None,
            )
            done[outcome if outcome != "pending" else "waiting"] += 1
    return done


async def run_refund_worker_forever() -> None:
    settings = get_settings()
    interval = settings.refund_worker_interval_seconds
    logger.info("refund_worker_started", interval_seconds=interval)
    while True:
        try:
            await process_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad pass kill the loop
            logger.exception("refund_worker_pass_failed", error=str(exc))
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(run_refund_worker_forever())
