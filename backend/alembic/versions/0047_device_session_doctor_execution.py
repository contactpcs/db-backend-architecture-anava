"""Lets a Doctor run a device session end-to-end, same as a Clinical
Assistant today — role-widened RLS across device_sessions and its child
tables, plus two new durable attribution fields (appointments.executor_role,
device_sessions.performed_by_id/performed_by_role) so which role actually
ran a given session is stamped at write time, not inferred later from a
live join to profiles.role (matching the role-snapshot pattern
appointment_audit_logs.changed_by_role / device_session_events.actor_role
already use).

Also lets a patient self-log a device_session_activity from their own
portal, and adds a 'frozen' status to device_session_scales — the first
piece of the protocol-supersede PRS-freeze mechanism (the amendment-time
sweep, completion-time hook, and submission-time hard-reject that actually
use this value live in application code, not this migration).

Device-session scheduling stays deliberately independent of a doctor's own
consultation calendar — excl_doctor_overlap and excl_ca_overlap remain two
separate, untouched guards. A doctor can be recorded as running a device
session while also attending a separate consultation at an overlapping
time; this is an explicit product decision, not an oversight.

Runs SQL/v1/86_device_session_doctor_execution.sql.

Revision ID: 0047
Revises: 0046
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQL_FILES = [
    "86_device_session_doctor_execution.sql",
]

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL" / "v1"


def _split_statements(sql_text: str) -> list[str]:
    """Splits a .sql file on top-level semicolons. Copy of the prior
    revision's — kept per-revision rather than imported, so a historical
    migration's behavior never shifts under it when a later file's copy is
    tweaked."""
    statements: list[str] = []
    buf: list[str] = []
    dollar_tag: str | None = None
    in_single_quote = False
    in_line_comment = False
    i = 0
    n = len(sql_text)

    while i < n:
        ch = sql_text[i]

        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if dollar_tag is None and not in_single_quote and sql_text.startswith("--", i):
            in_line_comment = True
            buf.append("--")
            i += 2
            continue

        if not in_single_quote and ch == "$":
            if dollar_tag is not None:
                if sql_text.startswith(dollar_tag, i):
                    buf.append(dollar_tag)
                    i += len(dollar_tag)
                    dollar_tag = None
                    continue
            else:
                close = sql_text.find("$", i + 1)
                if close != -1:
                    candidate = sql_text[i : close + 1]
                    inner = candidate[1:-1]
                    if inner == "" or inner.replace("_", "").isalnum():
                        dollar_tag = candidate
                        buf.append(candidate)
                        i += len(candidate)
                        continue

        if dollar_tag is None and ch == "'":
            in_single_quote = not in_single_quote
            buf.append(ch)
            i += 1
            continue

        if dollar_tag is None and not in_single_quote and ch == ";":
            buf.append(ch)
            statements.append("".join(buf))
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)

    def is_only_comments(chunk: str) -> bool:
        return all((not line.strip()) or line.strip().startswith("--") for line in chunk.splitlines())

    return [s for s in statements if s.strip() and not is_only_comments(s)]


def _strip_sql_comments(statement: str) -> str:
    lines = []
    for line in statement.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        lines.append(stripped)
    return " ".join(lines).strip()


def _is_transaction_control(statement: str) -> bool:
    return _strip_sql_comments(statement).rstrip(";").strip().upper() in {
        "BEGIN",
        "COMMIT",
        "START TRANSACTION",
    }


def upgrade() -> None:
    for filename in SQL_FILES:
        path = SQL_DIR / filename
        if not path.is_file():
            raise FileNotFoundError(f"{path} is missing — SQL/v1 is the source of truth for this revision")
        for statement in _split_statements(path.read_text(encoding="utf-8")):
            if _is_transaction_control(statement):
                continue
            op.execute(statement)


def downgrade() -> None:
    """Reverts the new columns and restores every RLS policy to its exact
    pre-0047 (clinical_assistant-only) text from 56_device_session_records.sql.
    The 'frozen' status value is left off the CHECK on downgrade — any row
    already frozen would violate a narrower constraint, so this only makes
    sense to run before 'frozen' has ever been used in practice."""
    op.execute('ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "chk_appointments_executor_role"')
    op.execute('ALTER TABLE core."appointments" DROP COLUMN IF EXISTS "executor_role"')

    op.execute('ALTER TABLE core."device_sessions" DROP CONSTRAINT IF EXISTS "fk_device_sessions_performed_by"')
    op.execute('ALTER TABLE core."device_sessions" DROP CONSTRAINT IF EXISTS "chk_device_sessions_performed_by_role"')
    op.execute('ALTER TABLE core."device_sessions" DROP COLUMN IF EXISTS "performed_by_role"')
    op.execute('ALTER TABLE core."device_sessions" DROP COLUMN IF EXISTS "performed_by_id"')

    op.execute('ALTER TABLE core."device_session_scales" DROP CONSTRAINT IF EXISTS "chk_dss2_status"')
    op.execute(
        'ALTER TABLE core."device_session_scales" ADD CONSTRAINT "chk_dss2_status" '
        "CHECK (\"status\" IN ('pending', 'in_progress', 'completed'))"
    )

    op.execute('DROP POLICY IF EXISTS "rls_device_sessions_insert" ON core."device_sessions"')
    op.execute(
        'CREATE POLICY "rls_device_sessions_insert" ON core."device_sessions" FOR INSERT TO public '
        "WITH CHECK (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, "
        "'clinical_assistant'::text, 'system'::text]))"
    )
    op.execute('DROP POLICY IF EXISTS "rls_device_sessions_update" ON core."device_sessions"')
    op.execute(
        'CREATE POLICY "rls_device_sessions_update" ON core."device_sessions" FOR UPDATE TO public '
        "USING (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, "
        "'clinical_assistant'::text, 'system'::text]))"
    )

    for child in (
        "device_session_symptoms",
        "device_session_adverse_events",
        "device_session_notes",
        "device_session_media",
        "device_session_events",
    ):
        op.execute(f'DROP POLICY IF EXISTS "rls_{child}_insert" ON core."{child}"')
        op.execute(
            f'CREATE POLICY "rls_{child}_insert" ON core."{child}" FOR INSERT TO public '
            "WITH CHECK (rls_user_role() = ANY (ARRAY['super_admin', 'clinic_admin', 'clinical_assistant', 'system']))"
        )

    op.execute('DROP POLICY IF EXISTS "rls_device_session_activities_insert" ON core."device_session_activities"')
    op.execute(
        'CREATE POLICY "rls_device_session_activities_insert" ON core."device_session_activities" FOR INSERT TO public '
        "WITH CHECK (rls_user_role() = ANY (ARRAY['super_admin', 'clinic_admin', 'clinical_assistant', 'system']))"
    )

    op.execute('DROP POLICY IF EXISTS "rls_device_session_scales_insert" ON core."device_session_scales"')
    op.execute(
        'CREATE POLICY "rls_device_session_scales_insert" ON core."device_session_scales" FOR INSERT TO public '
        "WITH CHECK (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, "
        "'clinical_assistant'::text, 'system'::text]))"
    )
    op.execute('DROP POLICY IF EXISTS "rls_device_session_scales_update" ON core."device_session_scales"')
    op.execute(
        'CREATE POLICY "rls_device_session_scales_update" ON core."device_session_scales" FOR UPDATE TO public '
        "USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, "
        "'clinical_assistant'::text, 'system'::text])) OR (device_session_record_id IN "
        "(SELECT ds.device_session_record_id FROM device_sessions ds "
        "JOIN appointments a ON a.appointment_id = ds.appointment_id WHERE a.patient_id = rls_user_id())))"
    )

    op.execute('DROP POLICY IF EXISTS "rls_device_session_feedback_insert" ON core."device_session_feedback"')
    op.execute(
        'CREATE POLICY "rls_device_session_feedback_insert" ON core."device_session_feedback" FOR INSERT TO public '
        "WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, "
        "'clinical_assistant'::text, 'system'::text])) OR (device_session_record_id IN "
        "(SELECT ds.device_session_record_id FROM device_sessions ds "
        "JOIN appointments a ON a.appointment_id = ds.appointment_id WHERE a.patient_id = rls_user_id())))"
    )

    op.execute('DROP POLICY IF EXISTS "rls_device_session_sos_events_update" ON core."device_session_sos_events"')
    op.execute(
        'CREATE POLICY "rls_device_session_sos_events_update" ON core."device_session_sos_events" FOR UPDATE TO public '
        "USING (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, "
        "'clinical_assistant'::text, 'system'::text]))"
    )
