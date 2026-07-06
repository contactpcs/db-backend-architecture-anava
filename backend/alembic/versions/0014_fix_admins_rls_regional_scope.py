"""Fix regional_admin's clinic_admin visibility under real RLS.

rls_admins_select (SQL/15) only matched a regional_admin against
admins.region_id — a column only ever set on regional_admin rows
themselves. clinic_admin rows always have region_id = NULL (clinic-scoped,
not region-scoped — see ClinicService.assign_admin), so RLS silently
dropped every clinic_admin row before the app-layer query filter in
admin/repository.py::AdminsRepository.list even ran. Surfaced by the new
regional-admin "Clinic Admins" section, which reported an empty list under
real RLS enforcement (RDS) despite clinic_admin rows existing. Matches
SQL/34_fix_admins_rls_regional_scope.sql — that file is the schema source
of truth per 0001's convention.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-06

"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "34_fix_admins_rls_regional_scope.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_admins_select ON admins;
        CREATE POLICY rls_admins_select ON admins FOR SELECT
        USING (
            rls_user_role() = 'super_admin'
            OR (rls_user_role() = 'regional_admin' AND region_id = rls_region_id())
            OR profile_id = rls_user_id()
        );
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
