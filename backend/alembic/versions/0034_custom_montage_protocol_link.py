"""Gives core.protocol_plan a seventh placement slot — a doctor-authored
custom montage (core.protocol_custom_montages, 38) — and makes dosing
optional when it's used.

Runs SQL/v1/54_custom_montage_protocol_link.sql, which ADD COLUMNs a nullable
custom_montage_id onto protocol_plan and widens/adds the placement/dosing
CHECK constraints. See that file's header for the full "why" (short version:
protocol_custom_montages existed since 38 with zero wiring into protocol_plan
at all — a doctor could save a validated custom montage and then be stuck,
because create-protocol only ever accepted a catalogue placement_id).

NOTE — chained off this checkout's actual head (0033). A separate, not-yet-
merged unit of work also claims revision id "0034" for
SQL/v1/53_device_session_records.sql, built in a sibling worktree
(.claude/worktrees/agent-a9c11e99bda24f444) per an explicit "deal with that
backend later" instruction. Whichever of the two lands first keeps "0034";
the other gets renumbered at merge time — normal alembic-history-merge
housekeeping, not something to guess a number for pre-emptively here.

Revision ID: 0034
Revises: 0033
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQL_FILES = [
    "54_custom_montage_protocol_link.sql",
]

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL" / "v1"


def _split_statements(sql_text: str) -> list[str]:
    """Splits a .sql file on top-level semicolons. Copy of 0033's — kept
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

    Dropping custom_montage_id from protocol_plan would discard which
    protocols were prescribed against a doctor-authored montage rather than
    a catalogue placement — clinical provenance, not scaffolding. Restore
    from a pre-migration snapshot instead.
    """
    raise NotImplementedError(
        "0034 (custom_montage_protocol_link) is not reversible: it would discard "
        "which protocols were prescribed against a doctor-authored custom montage. "
        "Restore from a pre-migration snapshot instead."
    )
