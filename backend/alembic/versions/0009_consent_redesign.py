"""Consent redesign: per-role onboarding templates + profiles.consent_signed.

Consent-record creation was duplicated across 4 call sites (staff/admin/
patients services) and profiles.is_active was overloaded to mean three
different things at once (admin on/off switch, "consent signed", and for
patients "entire registration complete"). This adds a dedicated
consent_signed column (separate from is_active) and splits the shared
staff_onboarding template into one row per staff role instead of a
[ROLE]-placeholder template. Matches
SQL/28_consent_redesign.sql — that file is the schema source of truth per
0001's convention.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-04

"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    sql_text = (SQL_DIR / "28_consent_redesign.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            op.execute(statement)


def downgrade() -> None:
    sql_text = """
        DELETE FROM consent_templates WHERE consent_type = 'staff_onboarding' AND role IS NOT NULL;
        UPDATE consent_templates SET is_active = TRUE WHERE consent_type = 'staff_onboarding' AND role IS NULL;

        ALTER TABLE consent_templates DROP CONSTRAINT IF EXISTS uq_consent_templates_type_role_version;
        ALTER TABLE consent_templates ADD CONSTRAINT consent_templates_consent_type_version_key UNIQUE (consent_type, version);
        ALTER TABLE consent_templates DROP COLUMN IF EXISTS role;

        ALTER TABLE profiles DROP COLUMN IF EXISTS consent_signed;
    """
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            op.execute(statement)
