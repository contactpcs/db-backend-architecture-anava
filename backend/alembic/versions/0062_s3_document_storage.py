"""S3 document storage: scan status, clinician confirmation and S3 VersionId
on core.patient_medical_history_files, raw_s3_version_id on
core.patient_eeg_files, and RLS policies letting the 'system' role (the
upload promotion job) read and update medical history file rows.

Runs SQL/v1/110_s3_document_storage.sql. Must be applied BEFORE deploying the
backend that writes the columns (every file upload inserts them).

Revision ID: 0062
Revises: 0061
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "110_s3_document_storage.sql"


def upgrade() -> None:
    # 110 is plain statements (no functions / dollar quotes, no ';' inside a
    # string), so a split on ';' after dropping comment lines is safe.
    text = SQL_FILE.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.strip().startswith("--"))
    for statement in body.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS "rls_mhf_update_system" ON core."patient_medical_history_files"')
    op.execute('DROP POLICY IF EXISTS "rls_mhf_select_system" ON core."patient_medical_history_files"')
    op.execute('ALTER TABLE core."patient_eeg_files" DROP COLUMN IF EXISTS "raw_s3_version_id"')
    op.execute('ALTER TABLE core."patient_medical_history_files" DROP CONSTRAINT IF EXISTS "fk_patient_medical_history_files_verified_by"')
    op.execute('ALTER TABLE core."patient_medical_history_files" DROP CONSTRAINT IF EXISTS "chk_mhf_status"')
    op.execute(
        'ALTER TABLE core."patient_medical_history_files" DROP COLUMN IF EXISTS "verified_at", '
        'DROP COLUMN IF EXISTS "verified_by", DROP COLUMN IF EXISTS "scan_result", '
        'DROP COLUMN IF EXISTS "status", DROP COLUMN IF EXISTS "s3_version_id"'
    )
