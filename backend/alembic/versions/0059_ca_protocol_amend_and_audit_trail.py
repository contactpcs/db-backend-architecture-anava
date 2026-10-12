"""Lets a clinical assistant amend (never create, cancel or complete) a
treatment protocol, and closes the audit-trail gaps that go with it:
audit_logs.changed_by_role, the disabled anamnesis audit trigger, missing
audit triggers on protocol_instances / protocol_conditions / protocol_scales /
anamnesis_responses / prs_responses, and a doctor-readable slice of
activity_logs.

Runs SQL/v1/106_ca_protocol_amend_and_audit_trail.sql then
SQL/v1/107_audit_trigger_actor_role.sql. Must be applied BEFORE deploying the
backend that uses them: without 106 a clinical assistant's amendment is
refused by RLS.

Revision ID: 0059
Revises: 0058
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL" / "v1"

_PRESCRIBERS = "ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text]"
_PRESCRIBERS_AND_SYSTEM = "ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'system'::text]"


def upgrade() -> None:
    # 106 is plain statements (no functions / dollar quotes), so a split on
    # ';' after dropping comment lines is safe.
    text = (SQL_DIR / "106_ca_protocol_amend_and_audit_trail.sql").read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.strip().startswith("--"))
    for statement in body.split(";"):
        if statement.strip():
            op.execute(statement)
    # 107 is one statement (CREATE FUNCTION with a $function$ body), executed whole.
    op.execute((SQL_DIR / "107_audit_trigger_actor_role.sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    # The function goes back first: its 107 body writes changed_by_role, so
    # dropping the column underneath it would fail every audited write.
    op.execute((SQL_DIR / "88_audit_trigger_request_context.sql").read_text(encoding="utf-8"))
    op.execute('ALTER TABLE compliance."audit_logs" DROP COLUMN IF EXISTS "changed_by_role"')

    op.execute('DROP POLICY IF EXISTS "rls_actlog_select_protocol_events" ON compliance."activity_logs"')
    for table in ("protocol_instances", "protocol_conditions", "protocol_scales", "anamnesis_responses", "prs_responses"):
        op.execute(f'DROP TRIGGER IF EXISTS trg_audit_{table} ON core."{table}"')
    # trg_audit_anamnesis_assessments stays enabled: it was only ever off by
    # a manual change on the live DB, never by a migration.

    # Policies back to what the live DB held before 0059.
    for table, roles in (
        ("protocol_plan", _PRESCRIBERS),
        ("protocol_conditions", _PRESCRIBERS),
        ("protocol_diagnoses", _PRESCRIBERS),
        ("protocol_scales", _PRESCRIBERS),
        ("protocol_device_sessions", _PRESCRIBERS_AND_SYSTEM),
        ("protocol_followup", _PRESCRIBERS_AND_SYSTEM),
    ):
        op.execute(f'DROP POLICY IF EXISTS "rls_{table}_insert" ON core."{table}"')
        op.execute(
            f'CREATE POLICY "rls_{table}_insert" ON core."{table}" FOR INSERT TO public WITH CHECK (rls_user_role() = ANY ({roles}))'
        )
    for table, roles in (
        ("protocol_plan", _PRESCRIBERS),
        ("protocol_device_sessions", _PRESCRIBERS_AND_SYSTEM),
        ("protocol_followup", _PRESCRIBERS_AND_SYSTEM),
    ):
        op.execute(f'DROP POLICY IF EXISTS "rls_{table}_update" ON core."{table}"')
        op.execute(f'CREATE POLICY "rls_{table}_update" ON core."{table}" FOR UPDATE TO public USING (rls_user_role() = ANY ({roles}))')
