"""Final Report: core.final_reports (doctor-signed, immutable snapshot).

Runs SQL/v1/117_final_reports.sql. The file is plain statements up to the
`-- ==FUNCTION==` marker (split on ';'), then one trigger function whose
$function$ body is executed whole, followed by its DROP/CREATE TRIGGER.

Revision ID: 0063
Revises: 0062
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0063"
down_revision: str | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "117_final_reports.sql"
MARKER = "-- ==FUNCTION=="
FN_END = "$function$;"


def _statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [s for s in body.split(";") if s.strip() and s.strip() not in ("BEGIN", "COMMIT")]


def upgrade() -> None:
    head, func = SQL_FILE.read_text(encoding="utf-8").split(MARKER)
    for statement in _statements(head):
        op.execute(statement)
    fn_end = func.index(FN_END) + len(FN_END)
    op.execute(func[:fn_end])
    for statement in _statements(func[fn_end:]):
        op.execute(statement)


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS core."final_reports"')
    op.execute("DROP FUNCTION IF EXISTS ops.fn_block_final_report_change()")
