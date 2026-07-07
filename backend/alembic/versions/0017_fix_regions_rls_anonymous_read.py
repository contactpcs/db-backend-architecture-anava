"""Fix anonymous read of regions for public self-registration.

rls_regions_select had no fallback for an anonymous (no RLS context) caller.
patients/service.py::PatientService.register validates the target clinic via
a clinics JOIN regions query — rls_clinics_select already had an anonymous
fallback (status not closed), regions didn't, so the JOIN's regions side was
invisible to the public registration endpoint and the row silently dropped,
producing "Clinic not found" for a clinic that genuinely exists and is open.
Matches SQL/37_fix_regions_rls_anonymous_read.sql — that file is the schema
source of truth per 0001's convention.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-07

"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "37_fix_regions_rls_anonymous_read.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_regions_select ON regions;
        CREATE POLICY rls_regions_select ON regions FOR SELECT
        USING (
            rls_user_role() = 'super_admin'
            OR region_id = rls_region_id()
        );
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
