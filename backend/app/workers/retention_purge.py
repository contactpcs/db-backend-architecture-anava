"""Retention & erasure worker (Closure Schema Change Requirements doc, item #5;
compliance policy Sections 6, 7). Two independent jobs, run on a schedule:

1. Erasure request processing — classify each open request's data into
   delete_now / retain_locked / compliance_evidence (Section 7.1), then
   execute delete_now items once their 30-day window passes (Section 3.5).
   Never bulk-deletes on receipt — a request only ever triggers classification.
2. Retention sweep — independent of any explicit request. Anonymises a
   patient's identifying profile fields once every linked retention window has
   cleared (the *latest* one, not the earliest — Section 7.2) and no
   legal_hold is set. Drops expired partitions on the 5 partitioned log/session
   tables once their entire date range is past retention (Section 7.3 — a
   DROP, not a DELETE scan).

`patients.retention_basis_cleared_at` / `last_clinical_contact_at` have no
write-through from the app yet (flagged as future work in the v1 design doc)
— this worker computes both itself each run rather than assume they're kept
current elsewhere, so it's correct standalone rather than silently no-op.

Run continuously: `python -m app.workers.retention_purge`
Run one pass (e.g. from a scheduler): `await run_once()`
"""

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import get_migration_engine

logger = structlog.get_logger()

RUN_INTERVAL_SECONDS = 24 * 60 * 60  # daily

_engine = get_migration_engine()
_session_factory = async_sessionmaker(_engine, expire_on_commit=False, autoflush=False)

CLINICAL_RETENTION = timedelta(days=365 * 7)
FINANCIAL_RETENTION = timedelta(days=365 * 8)
ERASURE_DELETE_NOW_GRACE = timedelta(days=30)

# Section 12 mapping table — (category name, table, patient_id column, bucket).
# bucket drives both the erasure-request classification (Job 1) and, for
# delete_now, what Job 1's execution step actually deletes.
DATA_CATEGORIES: list[tuple[str, str, str | None, str]] = [
    ("anamnesis", "anamnesis_assessments", "patient_id", "retain_locked"),
    ("prs_assessments", "prs_assessment_instances", "patient_id", "retain_locked"),
    ("eeg_files", "patient_eeg_files", "patient_id", "retain_locked"),
    ("medical_history_files", "patient_medical_history_files", "patient_id", "retain_locked"),
    ("treatment_plans", "treatment_plans", "patient_id", "retain_locked"),
    ("treatment_sessions", "treatment_sessions", "patient_id", "retain_locked"),
    ("doctor_session_notes", "doctor_session_notes", "patient_id", "retain_locked"),
    ("disease_selection", "patient_disease_selection", "patient_id", "retain_locked"),
    ("appointments", "appointments", "patient_id", "retain_locked"),
    ("sessions", "sessions", "patient_id", "retain_locked"),
    ("payments", "payments", None, "retain_locked"),  # joined via session_id, see _classify_payments
    ("store_orders", "store_orders", "patient_id", "retain_locked"),
    ("consent_records", "consent_records", "patient_id", "compliance_evidence"),
    ("notifications", "notifications", "recipient_id", "delete_now"),
]


async def _compute_retention_clock(session, patient_profile_id: str) -> dict:
    """Recomputes last_clinical_contact_at / retention_basis_cleared_at for one
    patient and writes them back onto core.patients. Returns the computed row."""
    last_clinical = (
        await session.execute(
            text(
                "SELECT GREATEST("
                "  (SELECT max(appointment_date::timestamptz) FROM appointments WHERE patient_id = :pid),"
                "  (SELECT max(session_date) FROM sessions WHERE patient_id = :pid),"
                "  (SELECT max(completed_at) FROM treatment_sessions WHERE patient_id = :pid)"
                ") AS last_clinical"
            ),
            {"pid": patient_profile_id},
        )
    ).scalar()

    last_financial = (
        await session.execute(
            text("SELECT max(p.paid_at) FROM payments p JOIN sessions s ON s.session_id = p.session_id WHERE s.patient_id = :pid"),
            {"pid": patient_profile_id},
        )
    ).scalar()

    clinical_clears = (last_clinical + CLINICAL_RETENTION) if last_clinical else None
    financial_clears = (last_financial + FINANCIAL_RETENTION) if last_financial else None
    candidates = [d for d in (clinical_clears, financial_clears) if d is not None]
    retention_basis_cleared_at = max(candidates) if candidates else None

    await session.execute(
        text("UPDATE patients SET last_clinical_contact_at = :lca, retention_basis_cleared_at = :rbc WHERE profile_id = :pid"),
        {"lca": last_clinical, "rbc": retention_basis_cleared_at, "pid": patient_profile_id},
    )
    return {"last_clinical_contact_at": last_clinical, "retention_basis_cleared_at": retention_basis_cleared_at}


async def classify_erasure_requests(session) -> int:
    """Job 1a — for every erasure_requests row still 'received', walk
    DATA_CATEGORIES and write one erasure_request_items row per category that
    has any data for that patient. Never deletes anything itself."""
    open_requests = (
        (await session.execute(text("SELECT request_id, patient_id FROM erasure_requests WHERE status = 'received'"))).mappings().all()
    )

    classified = 0
    for req in open_requests:
        for name, table, patient_col, bucket in DATA_CATEGORIES:
            if table == "payments":
                exists = (
                    await session.execute(
                        text("SELECT 1 FROM payments p JOIN sessions s ON s.session_id = p.session_id WHERE s.patient_id = :pid LIMIT 1"),
                        {"pid": req["patient_id"]},
                    )
                ).first()
            else:
                exists = (
                    await session.execute(
                        text(f"SELECT 1 FROM {table} WHERE {patient_col} = :pid LIMIT 1"),
                        {"pid": req["patient_id"]},
                    )
                ).first()
            if not exists:
                continue

            legal_basis = {
                "retain_locked": "NMC clinical-record retention (7yr) / Income Tax Act financial retention (8yr)",
                "compliance_evidence": "Proof of lawful processing under DPDP/GDPR — retained independently",
                "delete_now": None,
            }[bucket]
            retention_expires_at = None
            if bucket == "retain_locked":
                clock = await _compute_retention_clock(session, req["patient_id"])
                retention_expires_at = clock["retention_basis_cleared_at"]

            await session.execute(
                text(
                    "INSERT INTO erasure_request_items (request_id, data_category, bucket, legal_basis, retention_expires_at) "
                    "VALUES (:rid, :cat, :bucket, :basis, :exp)"
                ),
                {"rid": req["request_id"], "cat": name, "bucket": bucket, "basis": legal_basis, "exp": retention_expires_at},
            )
            classified += 1

        await session.execute(
            text("UPDATE erasure_requests SET status = 'classified' WHERE request_id = :rid"),
            {"rid": req["request_id"]},
        )
    return classified


async def execute_delete_now_items(session) -> int:
    """Job 1b — delete_now items past their 30-day grace window get hard-deleted.
    Only notifications map to delete_now in DATA_CATEGORIES today."""
    cutoff = datetime.now(UTC) - ERASURE_DELETE_NOW_GRACE
    items = (
        (
            await session.execute(
                text(
                    "SELECT ei.item_id, ei.data_category, er.patient_id "
                    "FROM erasure_request_items ei JOIN erasure_requests er ON er.request_id = ei.request_id "
                    "WHERE ei.bucket = 'delete_now' AND ei.deleted_at IS NULL AND ei.created_at < :cutoff"
                ),
                {"cutoff": cutoff},
            )
        )
        .mappings()
        .all()
    )

    deleted = 0
    for item in items:
        cat = next(c for c in DATA_CATEGORIES if c[0] == item["data_category"])
        table, patient_col = cat[1], cat[2]
        await session.execute(
            text(f"DELETE FROM {table} WHERE {patient_col} = :pid"),
            {"pid": item["patient_id"]},
        )
        await session.execute(
            text("UPDATE erasure_request_items SET deleted_at = now() WHERE item_id = :iid"),
            {"iid": item["item_id"]},
        )
        deleted += 1

    # advance status to completed once every item for a request is resolved
    # (delete_now items deleted, retain_locked/compliance_evidence never need a deleted_at)
    await session.execute(
        text(
            "UPDATE erasure_requests SET status = 'completed', responded_at = now() "
            "WHERE status = 'classified' AND request_id NOT IN ("
            "  SELECT request_id FROM erasure_request_items WHERE bucket = 'delete_now' AND deleted_at IS NULL"
            ")"
        )
    )
    return deleted


async def anonymize_expired_profiles(session) -> int:
    """Job 2 — general retention sweep, independent of erasure requests.
    Recomputes the retention clock for every non-anonymised patient, then
    anonymises the profile once the clock has cleared and legal_hold is false.
    Cognito AdminDeleteUser (Section 7.2) is a follow-up AWS-SDK hook, not
    implemented here — logged so it isn't silently dropped."""
    patients = (
        (
            await session.execute(
                text(
                    "SELECT p.profile_id FROM patients p JOIN profiles pr ON pr.id = p.profile_id "
                    "WHERE pr.is_anonymized = false AND p.legal_hold = false"
                )
            )
        )
        .mappings()
        .all()
    )

    anonymized = 0
    for p in patients:
        clock = await _compute_retention_clock(session, p["profile_id"])
        cleared_at = clock["retention_basis_cleared_at"]
        if cleared_at is None or cleared_at > datetime.now(UTC):
            continue

        pseudonym = f"ANON-{p['profile_id'].hex[:12]}" if hasattr(p["profile_id"], "hex") else f"ANON-{str(p['profile_id'])[:12]}"
        await session.execute(
            text(
                "UPDATE profiles SET first_name = :anon, last_name = :anon, email = :email, "
                "phone = NULL, address = NULL, profile_photo_s3_key = NULL, "
                "is_anonymized = true, anonymized_at = now() WHERE id = :pid"
            ),
            {"anon": pseudonym, "email": f"{pseudonym.lower()}@anonymised.invalid", "pid": p["profile_id"]},
        )
        logger.info(
            "profile_anonymized_cognito_delete_pending",
            profile_id=str(p["profile_id"]),
            note="AdminDeleteUser not called from this worker — wire in app/integrations/cognito.py before relying on this in production",
        )
        anonymized += 1
    return anonymized


PARTITION_RETENTION = {
    "notifications": timedelta(days=365),
    "audit_logs": timedelta(days=365 * 10),
    "activity_logs": timedelta(days=365 * 10),
    "appointment_audit_logs": timedelta(days=365 * 7),
    "treatment_sessions": timedelta(days=365 * 7),
}


def _partition_upper_bound(partition_name: str, parent: str) -> datetime | None:
    suffix = partition_name[len(parent) + 1 :]  # strip "<parent>_"
    if suffix == "default":
        return None
    if "m" in suffix:  # yYYYYmMM
        year, month = int(suffix[1:5]), int(suffix[6:8])
        return datetime(year + (month // 12), (month % 12) + 1, 1, tzinfo=UTC)
    year = int(suffix[1:5])  # yYYYY
    return datetime(year + 1, 1, 1, tzinfo=UTC)


async def drop_expired_partitions(session) -> list[str]:
    """Job 2b — drop partitions whose entire range is past retention. A DROP,
    never a DELETE scan — the whole reason these tables were partitioned."""
    dropped = []
    now = datetime.now(UTC)
    for parent, retention in PARTITION_RETENTION.items():
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT c.relname FROM pg_inherits i "
                        "JOIN pg_class c ON i.inhrelid = c.oid "
                        "JOIN pg_class p ON i.inhparent = p.oid "
                        "WHERE p.relname = :parent"
                    ),
                    {"parent": parent},
                )
            )
            .mappings()
            .all()
        )
        for r in rows:
            upper = _partition_upper_bound(r["relname"], parent)
            if upper is None:
                continue  # never drop the default partition
            if upper + retention < now:
                await session.execute(text(f'DROP TABLE "{r["relname"]}"'))
                dropped.append(r["relname"])
    return dropped


async def run_once() -> dict:
    async with _session_factory() as session:
        async with session.begin():
            classified = await classify_erasure_requests(session)
        async with session.begin():
            deleted = await execute_delete_now_items(session)
        async with session.begin():
            anonymized = await anonymize_expired_profiles(session)
        async with session.begin():
            dropped = await drop_expired_partitions(session)

    summary = {"classified_items": classified, "deleted_items": deleted, "anonymized_profiles": anonymized, "dropped_partitions": dropped}
    logger.info("retention_purge_run_complete", **summary)
    return summary


async def run_forever() -> None:
    logger.info("retention_purge_started", interval_seconds=RUN_INTERVAL_SECONDS)
    try:
        while True:
            try:
                await run_once()
            except Exception:
                logger.exception("retention_purge_run_failed")
            await asyncio.sleep(RUN_INTERVAL_SECONDS)
    finally:
        await _engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_forever())
