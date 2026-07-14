"""Staff PRS final-results RLS.

Doctor-on-behalf PRS flow: the scoring trigger fires with the caller's
privileges and writes prs_final_results when the last scale is finalized,
but rls_pfr_insert/rls_pfr_update allowed only super_admin or the patient
themself (see 0020's self-submit fix — same trap, staff side). Role list
now matches rls_psr_insert, which the same flow already writes. Matches
SQL/51_fix_staff_prs_final_results_rls.sql — that file is the schema
source of truth per 0001's convention.

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-14

"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "51_fix_staff_prs_final_results_rls.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_pfr_insert ON prs_final_results;
        CREATE POLICY rls_pfr_insert ON prs_final_results FOR INSERT
        WITH CHECK (
            rls_user_role() = 'super_admin'
            OR instance_id IN (SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id())
        );
        DROP POLICY IF EXISTS rls_pfr_update ON prs_final_results;
        CREATE POLICY rls_pfr_update ON prs_final_results FOR UPDATE
        USING (
            rls_user_role() = 'super_admin'
            OR instance_id IN (SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id())
        );
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
