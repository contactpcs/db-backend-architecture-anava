"""Add core.device_sessions and its nine child tables — live tDCS
device-treatment session tracking (pre-session checklist, live session
state, symptoms, adverse events, notes, activities, scale delivery,
feedback, media, the audit trail, patient-raised SOS events).

Runs SQL/v1/56_device_session_records.sql, which creates all ten tables
from scratch (IF NOT EXISTS) — it does not touch anything 30-55 already
created.

RENUMBERED: this migration and its SQL file were originally built as "53"
in a separate development worktree, in parallel with unrelated work that
also landed a genuine 53_billable_items_clinic_pricing.sql on the target
database first. Renumbered to 56/0036 at merge time to avoid the collision
— no content changed besides the file's own self-referencing header comment.

ALEMBIC GAP — READ BEFORE APPLYING: alembic's head before this revision is
0035, mirroring SQL/v1 file 55. Files 42-52 — including 47's
protocol_device_sessions, which this migration's RLS policies join against
— have no alembic wrapper yet. This is a pre-existing gap earlier revisions
inherited and did not fix; this revision does not fix it either. Applying
0036 assumes SQL/v1 files 41 through 55 are ALREADY HAND-APPLIED to the
target database.

FOR A DATABASE WHERE 56 WAS ALREADY APPLIED BY HAND: do not run this —
`alembic stamp 0036` records the position without executing anything.

Revision ID: 0036
Revises: 0035
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQL_FILES = [
    "56_device_session_records.sql",
]

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL" / "v1"


def _split_statements(sql_text: str) -> list[str]:
    """Splits a .sql file on top-level semicolons. Copy of 0035's — kept
    per-revision rather than imported, so a historical migration's behavior
    never shifts under it when a later file's copy is tweaked."""
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
    """Not reversible, deliberately.

    Dropping core.device_sessions and its nine child tables would discard
    every pre-session checklist, symptom, adverse event, note, activity,
    scale-delivery record, feedback response, media attachment, audit-trail
    entry, and SOS event ever logged during a live device session — the
    entire clinical record this revision exists to capture. Restore from a
    pre-migration snapshot instead.
    """
    raise NotImplementedError(
        "0036 is not reversible: it would discard every device-session "
        "checklist, symptom, adverse event, note, activity, scale-delivery "
        "record, feedback response, media attachment, audit-trail entry, "
        "and SOS event ever logged. Restore from a pre-migration snapshot "
        "instead."
    )
