"""Performance only: the SELECT policies on protocol_conditions /
protocol_diagnoses / protocol_scales called core.fn_can_read_protocol() once
per row (40 ms for 280 rows on the live database). The same EXISTS written
inline is evaluated by the planner in 0.8 ms and admits the same rows
(design: Documents/design_perf_round1.md).

Runs SQL/v1/108_protocol_child_select_rls_inline.sql. No backend change
depends on it, so it can be applied before or after a deploy.

Revision ID: 0060
Revises: 0059
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "108_protocol_child_select_rls_inline.sql"

_TABLES = ("protocol_conditions", "protocol_diagnoses", "protocol_scales")


def upgrade() -> None:
    # 108 is plain statements (no functions / dollar quotes), so a split on
    # ';' after dropping comment lines is safe.
    text = SQL_FILE.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.strip().startswith("--"))
    for statement in body.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    # Back to the function-call form SQL/v1/50 created.
    for table in _TABLES:
        op.execute(f'DROP POLICY IF EXISTS "rls_{table}_select" ON core."{table}"')
        op.execute(
            f'CREATE POLICY "rls_{table}_select" ON core."{table}" FOR SELECT TO public '
            "USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text])) "
            'OR core.fn_can_read_protocol("protocol_id"))'
        )
