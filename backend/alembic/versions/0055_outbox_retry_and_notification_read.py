"""Lets the outbox relay read back the notifications it writes (every insert
was rejected since the relay moved to RLS role 'system'), and gives
ops.outbox_events the columns the relay needs to retry a failed event instead
of dropping it (design: Documents/design_outbox_relay_reliability.md).

Runs SQL/v1/102_outbox_retry_and_notification_read.sql. Must be applied
BEFORE deploying the relay that reads the new columns.

Revision ID: 0055
Revises: 0054
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "102_outbox_retry_and_notification_read.sql"


def upgrade() -> None:
    # Plain statements (no functions / dollar quotes), so a split on ';'
    # after dropping comment lines is safe here.
    body = "\n".join(line for line in SQL_FILE.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--"))
    for statement in body.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS ops."idx_outbox_unpublished"')
    op.execute(
        'ALTER TABLE ops."outbox_events" DROP COLUMN IF EXISTS "next_attempt_at", '
        'DROP COLUMN IF EXISTS "failed_at", DROP COLUMN IF EXISTS "last_error"'
    )
    op.execute('CREATE INDEX "idx_outbox_unpublished" ON ops."outbox_events" USING btree (created_at) WHERE (published_at IS NULL)')
    op.execute('DROP POLICY IF EXISTS "rls_notif_select_system" ON core."notifications"')
