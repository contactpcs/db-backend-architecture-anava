"""Fix patient self-assignment of PRS scales during disease selection.

rls_psa_insert never included 'patient' — but PatientService.select_disease
(called by the patient themselves) triggers auto_assign_for_disease right
after, self-assigning scales. No patient could ever get past disease
selection under real RLS. Matches
SQL/39_fix_patient_scale_assignments_self_select_rls.sql — that file is the
schema source of truth per 0001's convention.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-07

"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "39_fix_patient_scale_assignments_self_select_rls.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_psa_insert ON patient_scale_assignments;
        CREATE POLICY rls_psa_insert ON patient_scale_assignments FOR INSERT
        WITH CHECK (
            rls_user_role() = ANY (ARRAY['super_admin', 'clinic_admin', 'doctor', 'clinical_assistant'])
        );
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
