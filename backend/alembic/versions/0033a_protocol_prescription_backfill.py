"""Backfills the alembic gap between 0033 (SQL/v1 41) and 0034 (SQL/v1 54) —
SQL/v1 files 42 through 53 had no migration wrapper at all until now, despite
0036's own docstring stating flatly that files "41 through 55" were assumed
already hand-applied to the live database. Anyone running `alembic upgrade
head` on a fresh/empty database before this revision existed would silently
skip core.protocol_instances, the core.protocol_plan rename (was
treatment_protocols), core.protocol_device_sessions, core.protocol_followup,
and everything 47-53 build on top of them — the application code has
depended on all of this existing since routing to /treatment-protocols and
/device-sessions was added.

Bundles, in dependency order (not raw file-number order — 45 was used twice
by two unrelated features developed in parallel, see below):

    42  payments_status_vocab_fix
    43  mock_payment_lifecycle_lock
    44  audit_log_system_role_rls
    45  anamnesis_assessment_stage        (anamnesis thread)
    46  anamnesis_insert_doctor_role      (anamnesis thread, needs 45 above)
    45  protocol_instances                (protocol thread — SAME number as
                                            the anamnesis 45 above; two
                                            unrelated features collided on
                                            it, resolved by disambiguating on
                                            filename, not number, here)
    47  protocol_prescription_tables      (needs protocol_instances above)
    48  protocol_plan_drop_plan_id
    49  protocol_plan_fix_dropped_dependents
    50  protocol_child_select_rls
    51  protocol_scales_from_prs
    52  appointments_booked_by_semantics
    53  billable_items_clinic_pricing

The .run.sql companions next to 47/48/49 (stripped, copy-paste-for-psql
versions per their own file headers) are intentionally NOT wrapped here —
same DDL as the .sql originals, just a manual-execution convenience copy.

⚠ FOR A FRESH DATABASE ONLY, same caveat as 0031's own docstring and for the
identical reason: several of these files are not idempotent when replayed
against a database where they already ran by hand (48 DROPs protocol_plan.
plan_id outright — replaying finds it already gone and errors). For a
database already built by hand (Anava_App_v1, or any environment where 42-53
were already applied out of band), the correct operation is:

    alembic stamp 0033a

to record this position without executing anything — never `alembic upgrade`
through this revision on such a database.

Revision ID: 0033a
Revises: 0033
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0033a"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQL_FILES = [
    "42_payments_status_vocab_fix.sql",
    "43_mock_payment_lifecycle_lock.sql",
    "44_audit_log_system_role_rls.sql",
    "45_anamnesis_assessment_stage.sql",
    "46_anamnesis_insert_doctor_role.sql",
    "45_protocol_instances.sql",
    "47_protocol_prescription_tables.sql",
    "48_protocol_plan_drop_plan_id.sql",
    "49_protocol_plan_fix_dropped_dependents.sql",
    "50_protocol_child_select_rls.sql",
    "51_protocol_scales_from_prs.sql",
    "52_appointments_booked_by_semantics.sql",
    "53_billable_items_clinic_pricing.sql",
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
    """Not reversible, deliberately — same stance as 0031's downgrade, for
    the same class of reason. 48 DROPs protocol_plan.plan_id outright;
    recreating an empty column would not restore what it pointed to, so a
    downgrade that "succeeded" would be lying about what it had done.
    Restore from a snapshot instead."""
    raise NotImplementedError(
        "0033a is not downgradable — 48_protocol_plan_drop_plan_id.sql and others in this "
        "bundle perform irreversible drops. Restore from a pre-migration snapshot instead."
    )
