"""Drop clinic_admin from staff_requests.position_role.

clinic_admin position_role had no fulfillment path in staff/service.py (no
ClinicAdminService there — clinic admins are only created via the dedicated
POST /clinics/{id}/assign-admin flow), so a clinic_admin requesting another
clinic_admin via this table was a dead-end anyway. Tightens both the
Pydantic schema (staff/schemas.py::StaffRequestCreate) and the DB CHECK to
match. No existing rows use this value. Matches
SQL/35_remove_clinic_admin_staff_request_role.sql — that file is the schema
source of truth per 0001's convention.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-06

"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "35_remove_clinic_admin_staff_request_role.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    sql_text = """
        ALTER TABLE staff_requests DROP CONSTRAINT IF EXISTS staff_requests_position_role_check;
        ALTER TABLE staff_requests ADD CONSTRAINT staff_requests_position_role_check
            CHECK (position_role IN ('doctor', 'clinical_assistant', 'receptionist', 'clinic_admin'));
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
