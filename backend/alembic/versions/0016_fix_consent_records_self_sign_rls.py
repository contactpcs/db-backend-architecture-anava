"""Fix doctor/CA/receptionist/patient self-sign of their own consent record.

rls_cr_update only allowed super_admin/regional_admin/clinic_admin to
UPDATE consent_records — every other role (doctor, clinical_assistant,
receptionist, patient) could never sign their own pending record under real
RLS enforcement. The UPDATE silently matched zero rows, service.sign()
misread that as a concurrent-double-submit loser and returned the
still-pending record with a 200 instead of erroring, producing an infinite
"sign and get sent right back to the consent form" loop with no visible
error. Matches SQL/36_fix_consent_records_self_sign_rls.sql — that file is
the schema source of truth per 0001's convention.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-06

"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "36_fix_consent_records_self_sign_rls.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_cr_update ON consent_records;
        CREATE POLICY rls_cr_update ON consent_records FOR UPDATE
        USING (rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin'));
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
