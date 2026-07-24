"""Fix receptionist patient-approval activation.

decide_approval's paired `UPDATE profiles SET is_active = TRUE` silently
affected 0 rows under real RLS — rls_profiles_update never allowed
receptionist, only super_admin/regional_admin/clinic_admin/self. Patient
stayed is_active=false forever despite approval_status flipping to
'approved', so login kept routing them back to the awaiting-approval
screen. Matches SQL/43_fix_receptionist_patient_approval_rls.sql — that
file is the schema source of truth per 0001's convention.

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-07

"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "43_fix_receptionist_patient_approval_rls.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_profiles_update ON profiles;
        CREATE POLICY rls_profiles_update ON profiles FOR UPDATE
        USING (
            rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin')
            OR id = rls_user_id()
        );
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
