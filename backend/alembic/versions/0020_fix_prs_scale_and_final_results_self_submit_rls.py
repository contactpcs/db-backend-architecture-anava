"""Fix patient self-submission of general PRS scale/final results.

prs_scale_results and prs_final_results never had an ownership-based
fallback for the patient submitting their own general PRS assessment,
unlike prs_responses which already had one. submit_responses (called by the
patient) writes both on every scale finalize / on the last scale via
trigger — neither could ever succeed for a patient under real RLS. Matches
SQL/40_fix_prs_scale_and_final_results_self_submit_rls.sql — that file is
the schema source of truth per 0001's convention.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-07

"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "40_fix_prs_scale_and_final_results_self_submit_rls.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_psr_insert ON prs_scale_results;
        CREATE POLICY rls_psr_insert ON prs_scale_results FOR INSERT
        WITH CHECK (rls_user_role() = ANY (ARRAY['super_admin', 'clinic_admin', 'clinical_assistant', 'doctor']));

        DROP POLICY IF EXISTS rls_psr_update ON prs_scale_results;
        CREATE POLICY rls_psr_update ON prs_scale_results FOR UPDATE
        USING (rls_user_role() = ANY (ARRAY['super_admin', 'doctor']));

        DROP POLICY IF EXISTS rls_pfr_insert ON prs_final_results;
        CREATE POLICY rls_pfr_insert ON prs_final_results FOR INSERT
        WITH CHECK (rls_user_role() = 'super_admin');

        DROP POLICY IF EXISTS rls_pfr_update ON prs_final_results;
        CREATE POLICY rls_pfr_update ON prs_final_results FOR UPDATE
        USING (rls_user_role() = 'super_admin');
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
