"""Recreates rls_payments_select, silently dropped by 0052's
`DROP TABLE core.sessions CASCADE` (the policy had a sessions branch). With
FORCE RLS and no SELECT policy, payments were invisible to every role and
every INSERT ... RETURNING into payments failed. Same policy as
SQL/v1/31_appointments_payment_states.sql minus the dead sessions branch.

Runs SQL/v1/100_restore_payments_select_policy.sql.

Revision ID: 0053
Revises: 0052
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "100_restore_payments_select_policy.sql"


def upgrade() -> None:
    # Two plain statements (no functions / dollar quotes), so a split on ';'
    # after dropping comment lines is safe here.
    body = "\n".join(line for line in SQL_FILE.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--"))
    for statement in body.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS "rls_payments_select" ON core."payments"')
