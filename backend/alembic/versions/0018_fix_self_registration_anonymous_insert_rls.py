"""Fix anonymous INSERT on profiles/patients/consent_records for self-registration.

Public self-registration (POST /auth/register, no auth) creates rows in all
three tables in one transaction. None of their INSERT policies had a
fallback for a caller with no RLS context at all — each required an
already-authenticated staff/patient role, impossible for the very first
insert that creates the patient. Matches
SQL/38_fix_self_registration_anonymous_insert_rls.sql — that file is the
schema source of truth per 0001's convention.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-07

"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "38_fix_self_registration_anonymous_insert_rls.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_profiles_insert ON profiles;
        CREATE POLICY rls_profiles_insert ON profiles FOR INSERT
        WITH CHECK (
            rls_user_role() = ANY (ARRAY['super_admin', 'regional_admin', 'clinic_admin', 'receptionist', 'patient'])
        );

        DROP POLICY IF EXISTS rls_patients_insert ON patients;
        CREATE POLICY rls_patients_insert ON patients FOR INSERT
        WITH CHECK (
            rls_user_role() = ANY (ARRAY['super_admin', 'clinic_admin', 'receptionist'])
        );

        DROP POLICY IF EXISTS rls_cr_insert ON consent_records;
        CREATE POLICY rls_cr_insert ON consent_records FOR INSERT
        WITH CHECK (
            rls_user_role() = ANY (ARRAY['super_admin', 'regional_admin', 'clinic_admin', 'receptionist'])
        );
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
