"""patient_scale_assignments.disease_id — fixes the doctor-assigns-PRS flow.

Without this, the permissions list had no way to reconstruct which disease
an assignment was for (a scale can serve multiple diseases), so starting an
assessment from a granted permission could never resolve its scales.
Matches SQL/48_patient_scale_assignments_disease_id.sql.

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-14

"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "48_patient_scale_assignments_disease_id.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("ALTER TABLE patient_scale_assignments DROP COLUMN IF EXISTS disease_id;")
