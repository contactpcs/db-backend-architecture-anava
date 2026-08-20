"""Drop clinic_device_schedules.capacity — how many device_session sessions
can run at once is the clinic's owned unit count (clinic_devices.quantity),
not a separately admin-typed weekly number. clinic_device_schedule_overrides.
capacity is unchanged — that one is a legitimate single-day exception
(e.g. a unit out for maintenance) and still works, NULL inheriting quantity.

Runs SQL/v1/55_drop_device_schedule_capacity.sql. No other reader of this
column remains in the codebase as of this revision (confirmed via full-repo
grep) — the admin device-schedule form's capacity input is removed in the
same change.

NUMBERING NOTE: same gap 0037's docstring describes — the live DB's
alembic_version stamp doesn't have matching local files for several prior
revisions. This one chains directly after 0037 since that one at least has
a real file now.

Revision ID: 0038
Revises: 0037
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQL_FILES = [
    "55_drop_device_schedule_capacity.sql",
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
    but there is no way to recover what capacity each existing weekly rule
    used to carry once it's gone (it may have deliberately differed from
    clinic_devices.quantity for a reason no longer recorded). Restore from a
    pre-migration snapshot, or re-add the column defaulted to quantity if
    history doesn't matter."""
    raise NotImplementedError(
        "0038 is not reversible: the dropped column's historical values cannot be reconstructed. Restore from a pre-migration snapshot."
    )
