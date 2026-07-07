"""Patient signup redesign — email_verified/phone_verified columns.

Backs the new OTP-based patient signup wizard (email-or-mobile, OTP
verification of the chosen channel, required follow-up verification of the
other one). Matches SQL/44_patient_otp_channel_verification.sql — that file
is the schema source of truth per 0001's convention.

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-07

"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    bind = op.get_bind()
    sql_text = (SQL_DIR / "44_patient_otp_channel_verification.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("ALTER TABLE profiles DROP COLUMN IF EXISTS email_verified, DROP COLUMN IF EXISTS phone_verified;")
