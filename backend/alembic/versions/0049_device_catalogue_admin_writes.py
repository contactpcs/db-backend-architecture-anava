"""Grants anava_app INSERT/UPDATE on reference.device_companies and
reference.neuromod_devices so super_admin can maintain the device catalogue
through the application (POST/PATCH /neuromod/device-companies and
/neuromod/devices). The RLS policies restricting those writes to
super_admin already existed since 32; only the table grant was missing.
Dosing/placement catalogue tables stay read-only to the app.

Runs SQL/v1/97_device_catalogue_admin_writes.sql.

Revision ID: 0049
Revises: 0048
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "97_device_catalogue_admin_writes.sql"


def upgrade() -> None:
    # ponytail: file is a single GRANT statement, so no statement splitter needed.
    op.get_bind().exec_driver_sql(SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute('REVOKE INSERT, UPDATE ON reference."device_companies", reference."neuromod_devices" FROM anava_app')
