"""Appointment policy and paid-cancellation refunds.

Runs SQL/v1/118_appointment_policy_and_refunds.sql: clinics.reschedule_min_hours,
clinic_admin write access to its own cancellation tiers, default 12 h -> 100 %
tiers (only where none exist), payments refund tracking columns, and
'partially_refunded' in payment_logs.status. Plain statements only (no
functions / dollar quotes, no ';' inside string literals), so a split on ';'
after dropping comment lines is safe.

Revision ID: 0064
Revises: 0063
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "118_appointment_policy_and_refunds.sql"


def upgrade() -> None:
    body = "\n".join(line for line in SQL_FILE.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--"))
    for statement in body.split(";"):
        if statement.strip() and statement.strip() not in ("BEGIN", "COMMIT"):
            op.execute(statement)


def downgrade() -> None:
    op.execute('ALTER TABLE core."payment_logs" DROP CONSTRAINT IF EXISTS "chk_payment_logs_status"')
    op.execute(
        'ALTER TABLE core."payment_logs" ADD CONSTRAINT "chk_payment_logs_status" '
        "CHECK (\"status\" IN ('pending', 'paid', 'failed', 'waived', 'refunded'))"
    )
    op.execute('DROP INDEX IF EXISTS core."idx_payments_refund_pending"')
    op.execute('ALTER TABLE core."payments" DROP CONSTRAINT IF EXISTS "payments_razorpay_refund_id_key"')
    op.execute('ALTER TABLE core."payments" DROP CONSTRAINT IF EXISTS "chk_payments_refund_status"')
    op.execute('ALTER TABLE core."payments" DROP COLUMN IF EXISTS "refunded_at"')
    op.execute('ALTER TABLE core."payments" DROP COLUMN IF EXISTS "razorpay_refund_id"')
    op.execute('ALTER TABLE core."payments" DROP COLUMN IF EXISTS "refund_status"')
    op.execute('ALTER TABLE core."clinics" DROP CONSTRAINT IF EXISTS "chk_clinics_reschedule_min_hours"')
    op.execute('ALTER TABLE core."clinics" DROP COLUMN IF EXISTS "reschedule_min_hours"')
    for verb, clause in (("INSERT", "WITH CHECK"), ("UPDATE", "USING"), ("DELETE", "USING")):
        name = f"rls_cancellation_policy_tiers_{verb.lower()}"
        op.execute(f'DROP POLICY IF EXISTS "{name}" ON reference."cancellation_policy_tiers"')
        op.execute(
            f'CREATE POLICY "{name}" ON reference."cancellation_policy_tiers" FOR {verb} TO public '
            f"{clause} ((rls_user_role() = 'super_admin'::text))"
        )
    # The seeded default tiers are left in place: they may have been edited
    # since, and deleting a live refund policy is not a schema rollback.
