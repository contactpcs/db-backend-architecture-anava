"""Doctor clinic_id — denormalized primary-clinic column.

doctors previously had no clinic_id of its own (only derivable via a
correlated subquery against clinic_staff_assignments). Adds a real column,
backfilled from the doctor's current active assignment, kept in sync at
write time going forward. clinic_staff_assignments remains the source of
truth for multi-clinic doctor membership. Matches
SQL/20_doctor_clinic_id.sql — that file is the schema source of truth per
0001's convention.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-03

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE doctors ADD COLUMN clinic_id UUID REFERENCES clinics(clinic_id) ON DELETE RESTRICT")
    op.execute(
        "UPDATE doctors d SET clinic_id = ("
        "SELECT csa.clinic_id FROM clinic_staff_assignments csa "
        "WHERE csa.profile_id = d.profile_id AND csa.staff_role = 'doctor' AND csa.is_active = TRUE "
        "ORDER BY csa.joined_at DESC LIMIT 1)"
    )
    op.execute("CREATE INDEX idx_doctors_clinic_id ON doctors(clinic_id)")


def downgrade() -> None:
    op.execute("DROP INDEX idx_doctors_clinic_id")
    op.execute("ALTER TABLE doctors DROP COLUMN clinic_id")
