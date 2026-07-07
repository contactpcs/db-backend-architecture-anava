"""Fix patient-triggered doctor auto-allocation ("No available doctor").

rls_profiles_select had no clause letting a patient see staff profile rows
at their own clinic. patients/service.py::_complete_registration ->
DoctorService.pick_least_loaded -> DoctorRepository.list() joins doctors to
profiles — under a patient's own RLS context, that join's profiles side was
invisible, so every doctor row silently dropped out, producing "No
available doctor at this clinic to auto-allocate" for a clinic that
genuinely has one. Matches
SQL/41_fix_profiles_patient_sees_own_clinic_staff_rls.sql — that file is the
schema source of truth per 0001's convention.

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-07

"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "41_fix_profiles_patient_sees_own_clinic_staff_rls.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_profiles_select ON profiles;
        CREATE POLICY rls_profiles_select ON profiles FOR SELECT
        USING (
            rls_user_role() = 'super_admin'
            OR rls_user_role() = 'regional_admin'
            OR id = rls_user_id()
            OR cognito_sub = rls_cognito_sub()
            OR email = rls_email()
            OR (
                rls_user_role() IN ('clinic_admin', 'doctor', 'clinical_assistant', 'receptionist')
                AND id IN (
                    SELECT profile_id FROM clinic_staff_assignments
                    WHERE clinic_id = rls_clinic_id() AND is_active = TRUE
                    UNION
                    SELECT profile_id FROM patients
                    WHERE primary_clinic_id = rls_clinic_id()
                )
            )
        );
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
