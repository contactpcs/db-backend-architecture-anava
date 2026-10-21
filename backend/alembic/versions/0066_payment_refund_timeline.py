"""Refund progress timestamps on payments (refund_requested_at, refund_initiated_at).

Runs SQL/v1/120_payment_refund_timeline.sql (plain statements, no ';' inside
literals).

Revision ID: 0066
Revises: 0065
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "120_payment_refund_timeline.sql"


def upgrade() -> None:
    body = "\n".join(line for line in SQL_FILE.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--"))
    for statement in body.split(";"):
        if statement.strip() and statement.strip() not in ("BEGIN", "COMMIT"):
            op.execute(statement)


def downgrade() -> None:
    op.execute('ALTER TABLE core."payments" DROP COLUMN IF EXISTS "refund_initiated_at"')
    op.execute('ALTER TABLE core."payments" DROP COLUMN IF EXISTS "refund_requested_at"')
