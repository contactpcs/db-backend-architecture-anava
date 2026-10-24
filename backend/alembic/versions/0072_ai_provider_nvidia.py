"""Ask ANAVA assistant moves from Gemini to NVIDIA: allow provider 'nvidia' in
ops.ai_usage_log and reword the privacy FAQ answer.

Runs SQL/v1/127_ai_provider_nvidia.sql (plain statements, no ';' inside
literals or comments-on).

Revision ID: 0072
Revises: 0071
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "127_ai_provider_nvidia.sql"


def upgrade() -> None:
    body = "\n".join(line for line in SQL_FILE.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--"))
    for statement in body.split(";"):
        if statement.strip() and statement.strip() not in ("BEGIN", "COMMIT"):
            op.execute(statement)


def downgrade() -> None:
    # Fails if 'nvidia' rows exist; delete or relabel them first.
    op.execute('ALTER TABLE ops."ai_usage_log" DROP CONSTRAINT IF EXISTS "chk_ai_usage_log_provider"')
    op.execute('ALTER TABLE ops."ai_usage_log" ADD CONSTRAINT "chk_ai_usage_log_provider" CHECK ("provider" IN (\'gemini\', \'claude\'))')
