"""Consent v3: consent_templates.full_html (the full form behind the summary)
and the record evidence for it (fill_values, rendered_full_hash,
full_viewed_at).

Runs SQL/v1/123_consent_full_html.sql (has a plpgsql body, so it is split
the same way as 0068).

Revision ID: 0069
Revises: 0068
"""

import re
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "123_consent_full_html.sql"


def split_statements(sql: str) -> list[str]:
    # Same as 0068: split on ';' only outside dollar quotes.
    body = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    statements, current, quote = [], [], None
    for token in re.split(r"(\$\w*\$|;)", body):
        if quote is None and token == ";":
            statements.append("".join(current).strip())
            current = []
            continue
        if re.fullmatch(r"\$\w*\$", token):
            quote = None if quote == token else (quote or token)
        current.append(token)
    statements.append("".join(current).strip())
    return [s for s in statements if s and s not in ("BEGIN", "COMMIT")]


def upgrade() -> None:
    bind = op.get_bind()
    for statement in split_statements(SQL_FILE.read_text(encoding="utf-8")):
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    # Columns hold signed consent evidence; never dropped. The guard keeps
    # working without full_html in its comparison only if 122 is re-run.
    pass
