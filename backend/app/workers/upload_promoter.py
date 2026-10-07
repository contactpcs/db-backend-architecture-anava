"""Promotion job for patient uploads (BACKEND_S3_PROMPT.md, section 4).

A patient's file lands in the quarantine bucket as incoming/{upload_id}.
GuardDuty Malware Protection scans it and tags the object. This job then
either copies it into the patient records bucket (row -> 'unverified') or
leaves it in quarantine (row -> 'rejected'). All the deciding is in
FileService.promote_upload(); this module only finds out WHICH uploads to
look at and gives each one its own transaction.

Not started by the API process, unlike the other workers: it has to run with
its own IAM role (anava-upload-promoter), because the API's role cannot read
the quarantine bucket. Two ways to run it, same image as the API:

    python -m app.workers.upload_promoter incoming/<upload_id>
        One upload. This is what the EventBridge rule on "GuardDuty Malware
        Protection Object Scan Result" starts (ECS RunTask, the object key
        passed in as the argument). The scan status in the event is not
        passed and not trusted: the object's own tag is read before copying.

    python -m app.workers.upload_promoter
        Every row still 'scanning'. A catch-up for events that were lost, and
        the pass that gives up on uploads whose bytes never arrived. Safe to
        schedule every few minutes; a row that is not ready is left alone.

Running it twice for the same upload, or twice at once, is harmless: the row
is locked and only a 'scanning' row is ever changed.
"""

import asyncio
import sys
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import as_system, get_worker_engine
from app.modules.files.repository import MedicalHistoryFileRepository
from app.modules.files.service import FileService

logger = structlog.get_logger()


async def run(keys: list[str]) -> dict[str, str]:
    """keys: quarantine object keys (incoming/{upload_id}) or bare upload ids.
    Empty = every row still 'scanning'. Returns {upload_id: status}."""
    engine = get_worker_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    outcome: dict[str, str] = {}
    try:
        async with session_factory() as session:
            if keys:
                upload_ids = [UUID(key.removeprefix("incoming/")) for key in keys]
            else:
                async with session.begin(), as_system(session):
                    upload_ids = await MedicalHistoryFileRepository(session).scanning_ids()
            for upload_id in upload_ids:
                try:
                    async with session.begin():
                        outcome[str(upload_id)] = await FileService(session).promote_upload(upload_id)
                except Exception:
                    # One bad upload must not stop the rest; it stays
                    # 'scanning' and the next catch-up run tries it again.
                    logger.exception("upload_promotion_failed", upload_id=str(upload_id))
                    outcome[str(upload_id)] = "error"
    finally:
        await engine.dispose()
    logger.info("upload_promotion_run_complete", outcome=outcome)
    return outcome


if __name__ == "__main__":
    results = asyncio.run(run(sys.argv[1:]))
    sys.exit(1 if "error" in results.values() else 0)
