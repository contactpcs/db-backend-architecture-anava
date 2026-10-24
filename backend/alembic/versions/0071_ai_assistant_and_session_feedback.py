"""Ask ANAVA assistant + post-session feedback: assistant_faq, assistant_consents,
assistant_messages, session_feedback, ai_usage_log.

Runs SQL/v1/125_ai_assistant_and_session_feedback.sql (plain statements, no ';'
inside literals or comments-on).

Revision ID: 0071
Revises: 0070
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "125_ai_assistant_and_session_feedback.sql"


def upgrade() -> None:
    body = "\n".join(line for line in SQL_FILE.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--"))
    for statement in body.split(";"):
        if statement.strip() and statement.strip() not in ("BEGIN", "COMMIT"):
            op.execute(statement)


def downgrade() -> None:
    # Order matters: assistant_messages references assistant_faq.
    op.execute('DROP TABLE IF EXISTS ops."ai_usage_log"')
    op.execute('DROP TABLE IF EXISTS core."session_feedback"')
    op.execute('DROP TABLE IF EXISTS core."assistant_messages"')
    op.execute('DROP TABLE IF EXISTS core."assistant_consents"')
    op.execute('DROP TABLE IF EXISTS reference."assistant_faq"')
