"""Doctor PRS-instance insert RLS.

Doctor starting a PRS assessment on behalf of a patient
(POST /prs-assessment-instances) failed with an RLS insert violation —
rls_pai_insert never listed doctor although rls_pai_update did. Matches
SQL/50_fix_doctor_prs_instance_insert_rls.sql — that file is the schema
source of truth per 0001's convention.

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-14

"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "50_fix_doctor_prs_instance_insert_rls.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_pai_insert ON prs_assessment_instances;
        CREATE POLICY rls_pai_insert ON prs_assessment_instances FOR INSERT
        WITH CHECK (
            rls_user_role() IN ('super_admin', 'clinic_admin', 'clinical_assistant', 'receptionist')
            OR patient_id = rls_user_id()
        );
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
