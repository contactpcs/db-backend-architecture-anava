"""Appointment lifecycle, treatment protocol, device pricing, legacy drop,
ops RLS cleanup, and the clinic device schedule.

Brings a database built from migrations into line with one built from the
numbered files in SQL/v1/. Before this revision the two disagreed in a way that
mattered: 0001's baseline still creates core.appointment_requests and the three
appointment columns file 34 drops, so `alembic upgrade head` produced a schema
the application was wrong about — no error, just objects the code no longer
supports and missing ones it needs.

Runs SQL/v1/30 through 36 verbatim. Those files are the source of truth and are
deliberately not duplicated here: two copies of the same DDL is how they drift.

⚠ FOR A FRESH DATABASE ONLY. Do not run this against a database that already had
30-36 applied by hand.

The chain is replayable from empty but NOT idempotent as a whole, and the reason
is specific: file 30 creates reference.billable_items with a device_type column,
constrains it (chk_billable_items_category_shape, line 241) and indexes it
(uq_billable_items_active_device_type, line 269). File 33 then DROPS that column,
having replaced it with a real device_id FK. Replaying 30 afterwards finds the
table already present (CREATE TABLE IF NOT EXISTS is a no-op, so the column is
not recreated) and then fails on the first statement that names device_type:

    ERROR: column "device_type" does not exist

Applied in order from empty this is fine — 30 needs the column, 33 removes it,
and nothing looks for it again.

For a database already built by hand (Anava_App_v1 as of 2026-08-13), apply any
missing file directly and then `alembic stamp 0031` to record the position
without executing anything. Stamping is the correct operation for a schema
created out of band; running the migration there is not.

Revision ID: 0031
Revises: 0030
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Order is the apply order and is not negotiable — each file depends on objects
# the previous one creates. 36 is independent of 33-35 but is kept last so the
# sequence matches the numbering people read.
SQL_FILES = [
    "30_appointments_spine.sql",
    "31_appointments_payment_states.sql",
    "32_treatment_protocol.sql",
    "33_billable_items_device_pricing.sql",
    "34_drop_legacy_appointment_objects.sql",
    "35_ops_rls_cleanup.sql",
    "36_clinic_device_schedules.sql",
]

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL" / "v1"


def _split_statements(sql_text: str) -> list[str]:
    """Splits a .sql file on top-level semicolons.

    asyncpg will not execute a script containing multiple commands in one call,
    unlike `psql -f` which sends the whole file as a single simple-query
    message. So the file has to be split before it is sent.

    Handles ARBITRARY dollar-quote tags, not just bare `$$`. 0001's splitter
    assumed `$$` because every file in SQL/ used only that; SQL/v1/32 uses
    `$function$`, and a splitter that did not understand it would cut three
    PL/pgSQL bodies in half at the first semicolon inside them.
    """
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
            # Either opening a dollar-quote or closing the one we are inside.
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
                    # A tag is $$ or $identifier$ — anything else is arithmetic
                    # or a stray character and must not open a quote.
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


def _is_transaction_control(statement: str) -> bool:
    """Files 32, 33 and 36 wrap themselves in BEGIN/COMMIT so they can be pasted
    into a SQL client as one atomic unit. Alembic already runs each migration in
    a transaction, so re-issuing them here would either nest (harmless but
    confusing) or COMMIT mid-migration and defeat the point of the wrapper.
    Stripped, and the outer transaction does the same job.
    """
    return statement.strip().rstrip(";").strip().upper() in {"BEGIN", "COMMIT", "START TRANSACTION"}


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

    34 drops core.appointment_requests and three appointment columns. Recreating
    empty structures would not restore the rows, so a downgrade that "succeeded"
    would be lying about what it had done. Restore from a snapshot instead —
    which is the honest operation for undoing a destructive migration, and the
    one the runbook already calls for before applying it.
    """
    raise NotImplementedError(
        "0031 is not reversible: it drops core.appointment_requests and three "
        "appointment columns. Restore from a pre-migration snapshot instead."
    )
