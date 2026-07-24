"""PRS instance SELECT RLS for NULL cycle_id.

INSERT ... RETURNING applies the SELECT policy to the new row;
rls_pai_select's staff clause matched only cycle-linked rows, so
instances with cycle_id IS NULL (all registration-stage and current
main_clinical instances) were invisible to clinic staff — breaking both
the doctor's instance-create RETURNING and staff views of patient PRS
history. Staff visibility now follows the patient's clinic membership.
Matches SQL/52_fix_prs_instance_select_rls_null_cycle.sql — that file is
the schema source of truth per 0001's convention.

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-14

"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "52_fix_prs_instance_select_rls_null_cycle.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_pai_select ON prs_assessment_instances;
        CREATE POLICY rls_pai_select ON prs_assessment_instances FOR SELECT
        USING (
            rls_user_role() = ANY (ARRAY['super_admin', 'regional_admin'])
            OR patient_id = rls_user_id()
            OR (
                rls_user_role() = ANY (ARRAY['clinic_admin', 'doctor', 'clinical_assistant', 'receptionist'])
                AND cycle_id IN (SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id())
            )
        );
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
