"""PRS data-table SELECT RLS for NULL cycle_id.

Companion to 0029: prs_responses / prs_scale_results / prs_final_results
SELECT policies were cycle-scoped for staff, so rows under NULL-cycle
instances were invisible — and since those repositories write with
RETURNING *, a doctor saving a patient's answer failed with an RLS
violation on prs_responses. Staff clause now follows the instance
patient's clinic membership, like 0029. Matches
SQL/53_fix_prs_data_select_rls_null_cycle.sql — that file is the schema
source of truth per 0001's convention.

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-14

"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"

_TABLES = {
    "rls_prs_resp_select": "prs_responses",
    "rls_psr_select": "prs_scale_results",
    "rls_pfr_select": "prs_final_results",
}


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "53_fix_prs_data_select_rls_null_cycle.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    for polname, table in _TABLES.items():
        bind.exec_driver_sql(f"DROP POLICY IF EXISTS {polname} ON {table}")
        bind.exec_driver_sql(f"""
            CREATE POLICY {polname} ON {table} FOR SELECT
            USING (
                rls_user_role() = ANY (ARRAY['super_admin', 'regional_admin'])
                OR instance_id IN (SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id())
                OR (
                    rls_user_role() = ANY (ARRAY['clinic_admin', 'doctor', 'clinical_assistant', 'receptionist'])
                    AND instance_id IN (
                        SELECT instance_id FROM prs_assessment_instances
                        WHERE cycle_id IN (SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id())
                    )
                )
            )
        """)
