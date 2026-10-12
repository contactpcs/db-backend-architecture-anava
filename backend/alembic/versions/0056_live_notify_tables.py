"""The two tables that replace Redis for logged-out access tokens and
one-time live-stream tickets (design: Documents/design_postgres_notify_live.md).

Runs SQL/v1/103_live_notify_tables.sql. Must be applied BEFORE deploying the
backend that uses them.

Revision ID: 0056
Revises: 0055
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "103_live_notify_tables.sql"


def upgrade() -> None:
    # Plain statements (no functions / dollar quotes), so a split on ';'
    # after dropping comment lines is safe here.
    body = "\n".join(line for line in SQL_FILE.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--"))
    for statement in body.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS ops."sse_tickets"')
    op.execute('DROP TABLE IF EXISTS ops."revoked_access_tokens"')
