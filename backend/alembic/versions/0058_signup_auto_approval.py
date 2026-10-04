"""Schema for automatic approval of self-registered patients
(design: Documents/design_signup_auto_approval.md): ops.normalize_email,
profiles.email_normalized, three patients columns, ops.signup_security_log.

Runs SQL/v1/104_normalize_email.sql then SQL/v1/105_signup_auto_approval.sql.
Must be applied BEFORE deploying the backend that uses them.

Revision ID: 0058
Revises: 0057
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL" / "v1"


def upgrade() -> None:
    # 104 is one statement (CREATE FUNCTION with a $function$ body), executed whole.
    op.execute((SQL_DIR / "104_normalize_email.sql").read_text(encoding="utf-8"))
    # 105 is plain statements (no functions / dollar quotes), so a split on
    # ';' after dropping comment lines is safe.
    text = (SQL_DIR / "105_signup_auto_approval.sql").read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.strip().startswith("--"))
    for statement in body.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS ops."signup_security_log"')
    op.execute("DROP INDEX IF EXISTS core.idx_patients_signup_ip")
    op.execute('ALTER TABLE core."patients" DROP CONSTRAINT IF EXISTS "chk_patients_approval_method"')
    op.execute(
        'ALTER TABLE core."patients" DROP COLUMN IF EXISTS "approval_method", '
        'DROP COLUMN IF EXISTS "risk_flags", DROP COLUMN IF EXISTS "signup_ip"'
    )
    op.execute('ALTER TABLE core."profiles" DROP COLUMN IF EXISTS "email_normalized"')
    op.execute("DROP FUNCTION IF EXISTS ops.normalize_email(text)")
