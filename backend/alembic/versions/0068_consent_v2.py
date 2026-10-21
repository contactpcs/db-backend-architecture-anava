"""Consent v2: template clauses/versioning/immutability, record evidence
columns, patients.guardian_reason.

Runs SQL/v1/122_consent_v2.sql. The file has plpgsql bodies ($function$,
$do$), so it is split on ';' only outside dollar quotes.

Revision ID: 0068
Revises: 0067
"""

import re
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "122_consent_v2.sql"


def split_statements(sql: str) -> list[str]:
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
    # Evidence columns hold signed consent proof; dropping them would destroy
    # it. Only the guards are removed.
    op.execute('DROP TRIGGER IF EXISTS "trg_consent_record_guard" ON compliance."consent_records"')
    op.execute('DROP TRIGGER IF EXISTS "trg_consent_template_guard" ON reference."consent_templates"')
