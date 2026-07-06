"""Fix regional_admin consent creation being silently skipped.

consent_records.clinic_id was NOT NULL, but a regional_admin isn't
clinic-scoped. RegionService.assign_admin (admin/service.py) worked around
this by looking up any clinic in the region to fill clinic_id — when the
region had zero clinics yet (the normal case, a clinic needs a regional_
admin before it can go 'active'), the consent record was never created,
permanently bricking the new regional_admin's account behind the
consent-sign screen with nothing to sign. Matches
SQL/27_fix_regional_admin_consent_scope.sql — that file is the schema
source of truth per 0001's convention.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-04

"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    sql_text = (SQL_DIR / "27_fix_regional_admin_consent_scope.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            op.execute(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_cr_select ON consent_records;
        CREATE POLICY rls_cr_select ON consent_records FOR SELECT
        USING (
            rls_user_role() = 'super_admin'
            OR (rls_user_role() = 'regional_admin' AND clinic_id IN (
                SELECT clinic_id FROM clinics WHERE region_id = rls_region_id()
            ))
            OR (rls_user_role() IN ('clinic_admin', 'doctor', 'clinical_assistant', 'receptionist') AND clinic_id = rls_clinic_id())
            OR patient_id = rls_user_id()
            OR staff_id = rls_user_id()
        );

        ALTER TABLE consent_records DROP CONSTRAINT IF EXISTS chk_consent_scope;
        ALTER TABLE consent_records DROP COLUMN IF EXISTS region_id;
        ALTER TABLE consent_records ALTER COLUMN clinic_id SET NOT NULL;
    """
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            op.execute(statement)
