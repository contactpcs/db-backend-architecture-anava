"""payments.status CHECK; payments / payment_logs SELECT policies scoped.

Runs SQL/v1/121_payment_integrity_and_rls.sql (plain statements, no ';'
inside literals; the trailing VALIDATE runs after COMMIT on purpose).

Revision ID: 0067
Revises: 0066
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "121_payment_integrity_and_rls.sql"


def upgrade() -> None:
    body = "\n".join(line for line in SQL_FILE.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--"))
    for statement in body.split(";"):
        if statement.strip() and statement.strip() not in ("BEGIN", "COMMIT"):
            op.execute(statement)


def downgrade() -> None:
    op.execute('ALTER TABLE core."payments" DROP CONSTRAINT IF EXISTS "chk_payments_status"')
    # Policies are left as upgraded: widening data access is not a safe rollback.
