"""Regional admin must be assigned from the region's main-branch clinic.

Reverses the region_id-only regional_admin scoping added in
0007/0008 — the real business flow requires a clinic to exist BEFORE its
regional_admin is assigned (they're a person based at that region's first/
main-branch clinic), not the other way around. Tightens
admins.chk_admins_scope to require clinic_id for regional_admin, adds
'regional_admin' to clinic_staff_assignments.staff_role, and performs a
one-time wipe of all non-super_admin profiles/admins/regions/clinics (and
everything downstream of them) since the stricter constraint can't apply to
existing data created under the old rule. Matches
SQL/29_regional_admin_clinic_binding.sql — that file is the schema source of
truth per 0001's convention.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-04

"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    sql_text = (SQL_DIR / "29_regional_admin_clinic_binding.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            op.execute(statement)


def downgrade() -> None:
    sql_text = """
        ALTER TABLE clinic_staff_assignments DROP CONSTRAINT IF EXISTS clinic_staff_assignments_staff_role_check;
        ALTER TABLE clinic_staff_assignments ADD CONSTRAINT clinic_staff_assignments_staff_role_check CHECK (staff_role IN (
            'clinic_admin', 'doctor', 'clinical_assistant', 'receptionist'
        ));

        ALTER TABLE admins DROP CONSTRAINT IF EXISTS chk_admins_scope;
        ALTER TABLE admins ADD CONSTRAINT chk_admins_scope CHECK (
            (admin_type = 'super_admin'       AND region_id IS NULL     AND clinic_id IS NULL)
            OR (admin_type = 'regional_admin' AND region_id IS NOT NULL AND clinic_id IS NULL)
            OR (admin_type = 'clinic_admin'   AND clinic_id IS NOT NULL)
        );
    """
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            op.execute(statement)
