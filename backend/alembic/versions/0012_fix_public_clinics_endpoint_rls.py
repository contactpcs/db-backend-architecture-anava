"""Fix GET /auth/clinics (public self-registration clinic picker) under real RLS.

Same class of bug as 0011 — RDS cutover enforced RLS for the first time and
surfaced a gap local dev's superuser-bypass role never exercised. This
endpoint is genuinely public/unauthenticated (no RLS context exists at all
for it), and rls_clinics_select had no fallback for that — silently
returned zero rows instead of erroring, reproducing the original "clinic
dropdown is empty" bug via a different mechanism. Matches
SQL/32_fix_public_clinics_endpoint_rls.sql — that file is the schema source
of truth per 0001's convention.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-06

"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "32_fix_public_clinics_endpoint_rls.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_clinics_select ON clinics;
        CREATE POLICY rls_clinics_select ON clinics FOR SELECT
        USING (
            rls_user_role() = 'super_admin'
            OR (rls_user_role() = 'regional_admin' AND region_id = rls_region_id())
            OR clinic_id = rls_clinic_id()
        );
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
