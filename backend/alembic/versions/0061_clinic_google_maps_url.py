"""core.clinics.full_address, pincode and google_maps_url — the clinic's
complete address, postal code and Google Maps link, all editable by that
clinic's clinic_admin through PATCH /clinics/{id} and read by patients
through GET /patients/{patient_id}/clinic.

Runs SQL/v1/109_clinic_google_maps_url.sql. Must be applied BEFORE deploying
the backend that writes the columns (POST /clinics inserts all three).

Revision ID: 0061
Revises: 0060
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "109_clinic_google_maps_url.sql"


def upgrade() -> None:
    # 109 is plain statements (no functions / dollar quotes), so a split on
    # ';' after dropping comment lines is safe.
    text = SQL_FILE.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.strip().startswith("--"))
    for statement in body.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute('ALTER TABLE core."clinics" DROP CONSTRAINT IF EXISTS "chk_clinics_google_maps_url"')
    op.execute(
        'ALTER TABLE core."clinics" DROP COLUMN IF EXISTS "google_maps_url", '
        'DROP COLUMN IF EXISTS "pincode", DROP COLUMN IF EXISTS "full_address"'
    )
