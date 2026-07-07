"""Fix patient-triggered doctor_patient_assignments creation.

rls_dpa_insert never allowed the patient role, but the auto-allocation that
runs when a patient finishes registration (_complete_registration) creates
this row itself with patient_id = the caller's own profile id.
rls_dpa_select already had the matching ownership fallback; the INSERT
policy didn't. Matches
SQL/42_fix_doctor_patient_assignments_self_insert_rls.sql — that file is
the schema source of truth per 0001's convention.

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-07

"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "42_fix_doctor_patient_assignments_self_insert_rls.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_dpa_insert ON doctor_patient_assignments;
        CREATE POLICY rls_dpa_insert ON doctor_patient_assignments FOR INSERT
        WITH CHECK (rls_user_role() = ANY (ARRAY['super_admin', 'clinic_admin', 'receptionist']));
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
