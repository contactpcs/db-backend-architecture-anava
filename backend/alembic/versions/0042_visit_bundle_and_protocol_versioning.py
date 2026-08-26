"""Ties anamnesis and PRS assessment instances to the specific appointment
that produced them (appointment_id, nullable), and gives core.protocol_plan
a major.minor version number (version_major, version_minor) driven by the
supersedes_protocol_id chain 39/0034-era work added but the app never wired
up. ADDITIVE ONLY — no existing column, table, or row is touched.

Runs SQL/v1/59_visit_bundle_and_protocol_versioning.sql.

Revision ID: 0042
Revises: 0041
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQL_FILES = [
    "59_visit_bundle_and_protocol_versioning.sql",
]

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL" / "v1"


def _split_statements(sql_text: str) -> list[str]:
    """Splits a .sql file on top-level semicolons. Copy of the prior
    revision's — kept per-revision rather than imported, so a historical
    migration's behavior never shifts under it when a later file's copy is
    tweaked."""
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
    """Reversible: every change in 59 is additive (new nullable columns,
    their FKs/CHECKs/indexes) — nothing here has ever been read by app code
    before this revision, so dropping is safe."""
    op.execute('ALTER TABLE core."anamnesis_assessments" DROP CONSTRAINT IF EXISTS "fk_anamnesis_assessments_appointment_id"')
    op.execute('DROP INDEX IF EXISTS core.idx_anamnesis_assessments_appointment')
    op.execute('ALTER TABLE core."anamnesis_assessments" DROP COLUMN IF EXISTS "appointment_id"')

    op.execute('ALTER TABLE core."prs_assessment_instances" DROP CONSTRAINT IF EXISTS "fk_prs_assessment_instances_appointment_id"')
    op.execute('DROP INDEX IF EXISTS core.idx_prs_assessment_instances_appointment')
    op.execute('ALTER TABLE core."prs_assessment_instances" DROP COLUMN IF EXISTS "appointment_id"')

    op.execute('ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_protocol_plan_version_major"')
    op.execute('ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_protocol_plan_version_minor"')
    op.execute('ALTER TABLE core."protocol_plan" DROP COLUMN IF EXISTS "version_major"')
    op.execute('ALTER TABLE core."protocol_plan" DROP COLUMN IF EXISTS "version_minor"')
