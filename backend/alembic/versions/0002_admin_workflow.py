"""Admin workflow updates — clinic_admin_id becomes nullable.

Clinic creation is now a 2-step flow: create the clinic first (no admin
picker needed), then assign a clinic_admin via a dedicated endpoint.
Matches SQL/19_admin_workflow_updates.sql — that file is the schema source
of truth per 0001's convention.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-03

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE clinics ALTER COLUMN clinic_admin_id DROP NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE clinics ALTER COLUMN clinic_admin_id SET NOT NULL")
