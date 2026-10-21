"""Platform default refund rule: 24 h -> 100 %, 12 h -> 50 %.

Runs SQL/v1/119_default_refund_tiers_24_12.sql (data only, plain statements,
no ';' inside literals).

Revision ID: 0065
Revises: 0064
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_FILE = Path(__file__).resolve().parents[3] / "SQL" / "v1" / "119_default_refund_tiers_24_12.sql"


def upgrade() -> None:
    body = "\n".join(line for line in SQL_FILE.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--"))
    for statement in body.split(";"):
        if statement.strip() and statement.strip() not in ("BEGIN", "COMMIT"):
            op.execute(statement)


def downgrade() -> None:
    # A refund policy is live business data, not schema: rolling the code back
    # does not justify silently changing what patients are refunded.
    pass
