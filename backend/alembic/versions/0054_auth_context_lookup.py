"""One-call auth context lookup: ops.auth_context(cognito_sub) replaces the
up-to-9 statements AuthContextMiddleware sent per request (design:
Documents/API_Audit/design_auth_context_lookup.md).

Runs SQL/v1/101_auth_context_lookup.sql. Must be applied BEFORE deploying
the middleware that calls it (the API refuses to start without it).

Revision ID: 0054
Revises: 0053
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "101_auth_context_lookup.sql"


def upgrade() -> None:
    # One statement (CREATE FUNCTION with a $function$ body), so the file is
    # executed whole — splitting on ';' would cut the function body apart.
    op.execute(SQL_FILE.read_text(encoding="utf-8"))
    op.execute("GRANT EXECUTE ON FUNCTION ops.auth_context(text) TO anava_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS ops.auth_context(text)")
