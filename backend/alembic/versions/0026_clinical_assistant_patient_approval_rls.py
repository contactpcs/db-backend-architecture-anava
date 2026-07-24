"""Clinical assistant patient-approval RLS.

Clinical assistants now share the receptionist's approvals screen
(PATCH /patients/{id}/approval), but both RLS policies behind that flow —
rls_patients_update (approval_status flip) and rls_profiles_update
(paired is_active activation, see 0023) — listed receptionist only, so a
clinical assistant's approval silently updated 0 rows. Matches
SQL/49_clinical_assistant_patient_approval_rls.sql — that file is the
schema source of truth per 0001's convention.

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-14

"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "49_clinical_assistant_patient_approval_rls.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_patients_update ON patients;
        CREATE POLICY rls_patients_update ON patients FOR UPDATE
        USING (
            rls_user_role() IN ('super_admin', 'clinic_admin', 'receptionist')
            OR profile_id = rls_user_id()
        );
        DROP POLICY IF EXISTS rls_profiles_update ON profiles;
        CREATE POLICY rls_profiles_update ON profiles FOR UPDATE
        USING (
            rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin')
            OR id = rls_user_id()
            OR (
                rls_user_role() = 'receptionist'
                AND id IN (SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id())
            )
        );
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
