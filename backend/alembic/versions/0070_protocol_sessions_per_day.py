"""protocol_plan.sessions_per_day: doctor-set daily device-session limit (1-4).

Runs SQL/v1/124_protocol_sessions_per_day.sql (plain statements, no ';'
inside literals).

Revision ID: 0070
Revises: 0069
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "124_protocol_sessions_per_day.sql"


def upgrade() -> None:
    body = "\n".join(line for line in SQL_FILE.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--"))
    for statement in body.split(";"):
        if statement.strip() and statement.strip() not in ("BEGIN", "COMMIT"):
            op.execute(statement)


def downgrade() -> None:
    op.execute('ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_protocol_plan_sessions_per_day"')
    op.execute('ALTER TABLE core."protocol_plan" DROP COLUMN IF EXISTS "sessions_per_day"')
