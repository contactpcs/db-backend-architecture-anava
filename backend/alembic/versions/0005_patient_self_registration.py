"""Patient self-registration + receptionist approval gate.

Two legitimate patient onboarding paths: staff-registered (existing,
unaffected — witness present, consent activates immediately) and
self-registered (new — patient completes the whole 6-step registration
machine themselves while inactive, then a receptionist approves/rejects
once registration_status='registration_complete'). Matches
SQL/24_patient_self_registration.sql — that file is the schema source of
truth per 0001's convention.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-03

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE patients ADD COLUMN self_registered BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute(
        "ALTER TABLE patients ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'not_required' "
        "CHECK (approval_status IN ('not_required', 'pending', 'approved', 'rejected'))"
    )
    op.execute("ALTER TABLE patients ADD COLUMN approved_by UUID REFERENCES profiles(id) ON DELETE RESTRICT")
    op.execute("ALTER TABLE patients ADD COLUMN approved_at TIMESTAMPTZ")
    op.execute("ALTER TABLE patients ADD COLUMN rejection_reason TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE patients DROP COLUMN rejection_reason")
    op.execute("ALTER TABLE patients DROP COLUMN approved_at")
    op.execute("ALTER TABLE patients DROP COLUMN approved_by")
    op.execute("ALTER TABLE patients DROP COLUMN approval_status")
    op.execute("ALTER TABLE patients DROP COLUMN self_registered")
