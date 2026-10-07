"""ops.auth_context read core.consent_records, but the table is
compliance.consent_records. The lookup runs for every active staff account,
so every staff request failed with "relation core.consent_records does not
exist". Patients never reach that branch.

Re-runs SQL/v1/101_auth_context_lookup.sql (CREATE OR REPLACE), where the
reference is corrected.

Revision ID: 0057
Revises: 0056
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "101_auth_context_lookup.sql"


def upgrade() -> None:
    # One statement (CREATE FUNCTION with a $function$ body), executed whole.
    op.execute(SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    # Nothing to restore: the previous body could not run for staff.
    pass
