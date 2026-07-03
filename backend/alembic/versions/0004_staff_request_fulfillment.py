"""Staff request fulfillment link.

regional_admin now creates the actual doctor/CA/receptionist profile as a
separate manual step after approving a staff_request (see 0002's staff
onboarding-lockdown follow-up) — nothing tied the created profile back to
the request it fulfilled. Adds that link: fulfilled_profile_id +
fulfilled_at, nullable, no status enum change. Matches
SQL/23_staff_request_fulfillment.sql — that file is the schema source of
truth per 0001's convention.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-03

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE staff_requests ADD COLUMN fulfilled_profile_id UUID REFERENCES profiles(id) ON DELETE RESTRICT")
    op.execute("ALTER TABLE staff_requests ADD COLUMN fulfilled_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE staff_requests DROP COLUMN fulfilled_at")
    op.execute("ALTER TABLE staff_requests DROP COLUMN fulfilled_profile_id")
