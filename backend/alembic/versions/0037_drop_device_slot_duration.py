"""Drop clinic_device_schedules.slot_duration_minutes — device_session
duration now comes from reference.billable_items.duration_minutes at
booking time, resolved per appointment, not a fixed device chunk size.

Runs SQL/v1/54_drop_device_slot_duration.sql. No other reader of this column
remains in the codebase as of this revision (confirmed via full-repo grep) —
the admin device-schedule form's "suggested capacity" helper, the only other
consumer, is removed in the same change.

NUMBERING NOTE: the live DB's alembic_version is stamped "0036", but only
0033 has a matching file in this versions/ directory — 0034-0036 were
applied by hand at some point without ever getting committed migration
files (same "hand-apply now, reconcile later" pattern 0031-0033's own
docstrings describe for 30-40). down_revision points at that stamp so a
fresh DB's chain at least tells the true story; `alembic upgrade` will
still fail to locate 0034-0036 until those are backfilled or the DB is
re-stamped — a pre-existing gap, not something this revision caused or
fixes. The actual DROP was applied directly against the live DB (via
MIGRATION_DATABASE_URL) rather than through `alembic upgrade`, matching
how this repo has been applying schema changes in practice.

Revision ID: 0037
Revises: 0036
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQL_FILES = [
    "54_drop_device_slot_duration.sql",
]

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL" / "v1"


def _split_statements(sql_text: str) -> list[str]:
    """Splits a .sql file on top-level semicolons. Copy of prior revisions',
    kept per-revision rather than imported, so a historical migration's
    behavior never shifts under it when a later file's copy is tweaked."""
    statements: list[str] = []
    buf: list[str] = []
    dollar_tag: str | None = None
    in_single_quote = False
    in_line_comment = False
    i = 0
    n = len(sql_text)

    while i < n:
        ch = sql_text[i]

        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if dollar_tag is None and not in_single_quote and sql_text.startswith("--", i):
            in_line_comment = True
            buf.append("--")
            i += 2
            continue

        if not in_single_quote and ch == "$":
            if dollar_tag is not None:
                if sql_text.startswith(dollar_tag, i):
                    buf.append(dollar_tag)
                    i += len(dollar_tag)
                    dollar_tag = None
                    continue
            else:
                close = sql_text.find("$", i + 1)
                if close != -1:
                    candidate = sql_text[i : close + 1]
                    inner = candidate[1:-1]
                    if inner == "" or inner.replace("_", "").isalnum():
                        dollar_tag = candidate
                        buf.append(candidate)
                        i += len(candidate)
                        continue

        if dollar_tag is None and ch == "'":
            in_single_quote = not in_single_quote
            buf.append(ch)
            i += 1
            continue

        if dollar_tag is None and not in_single_quote and ch == ";":
            buf.append(ch)
            statements.append("".join(buf))
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)

    def is_only_comments(chunk: str) -> bool:
        return all((not line.strip()) or line.strip().startswith("--") for line in chunk.splitlines())

    return [s for s in statements if s.strip() and not is_only_comments(s)]


def _strip_sql_comments(statement: str) -> str:
    lines = []
    for line in statement.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        lines.append(stripped)
    return " ".join(lines).strip()


def _is_transaction_control(statement: str) -> bool:
    return _strip_sql_comments(statement).rstrip(";").strip().upper() in {
        "BEGIN",
        "COMMIT",
        "START TRANSACTION",
    }


def upgrade() -> None:
    for filename in SQL_FILES:
        path = SQL_DIR / filename
        if not path.is_file():
            raise FileNotFoundError(f"{path} is missing — SQL/v1 is the source of truth for this revision")
        for statement in _split_statements(path.read_text(encoding="utf-8")):
            if _is_transaction_control(statement):
                continue
            op.execute(statement)


def downgrade() -> None:
    """Not reversible without a backfill value: re-adding the column is easy,
    but there is no way to recover what duration each existing weekly rule
    used to imply once it's gone. Restore from a pre-migration snapshot, or
    re-add the column with a chosen default if history doesn't matter."""
    raise NotImplementedError(
        "0034 is not reversible: the dropped column's historical values cannot be reconstructed. Restore from a pre-migration snapshot."
    )
