"""Clinic device inventory, protocol flow completion, prescription fields, and
the tDCS reference catalogue seed.

Continues where 0031 stopped. 0031 applied SQL/v1/30 through 36; this revision
applies 37 through 40, so a database built from migrations again matches one
built from the numbered files.

Runs the SQL/v1 files verbatim, for the same reason 0031 does: those files are
the source of truth, and two copies of the same DDL is how they drift.

What each file brings:

  37  core.clinic_devices — which clinic owns which machine, and how many.
      Backs the device picker's clinic_id filter and
      trg_check_device_available_at_clinic, which stops a protocol being
      prescribed onto a device the clinic does not have.
  38  The four tables the wizard was filling in and then discarding:
      protocol_conditions, protocol_diagnoses, protocol_scales, and
      protocol_custom_montages (doctor-authored placements, deliberately kept
      out of the curated reference library).
  39  The prescribed dose as typed columns on treatment_protocols
      (prescribed_current_ma, prescribed_duration_min, ramp_seconds,
      sessions_per_week), plus authored_in_appointment_id,
      supersedes_protocol_id, reference.dosing_unspecified_notes, and the
      trigger that refuses to let an incomplete prescription go active.
  40  Seed data: conditions, ICD-10 codes, assessment scales, and — for every
      ACTIVE tDCS device — the placement/dosing catalogue.

⚠ 40 IS SEED DATA AND ITS RESULT DEPENDS ON THE TARGET DATABASE.
reference.neuromod_devices is populated by hand, not by any migration. On a
fresh database (CI, a local container) there are no devices, so 40's per-device
loop writes nothing to tdcs_placements / tdcs_dosing /
dosing_unspecified_notes; conditions, diagnoses and scales are still seeded
because they do not depend on a device. That is correct behaviour, not a
failure — see the header of 40 for the expected counts either way.

Every statement in 40 is guarded by NOT EXISTS or ON CONFLICT, so this
revision is safe to run against a database that already has the catalogue.

FOR A DATABASE WHERE 37-40 WERE ALREADY APPLIED BY HAND (which is the case for
Anava_App_v1 as of 2026-08-14): do NOT run this. `alembic stamp 0032` records
the position without executing anything. Stamping is the correct operation for
a schema created out of band. Unlike 0031, running it anyway would mostly
succeed — 37-40 use IF NOT EXISTS and DROP CONSTRAINT IF EXISTS throughout —
but stamping is still the honest record of what happened.

Revision ID: 0032
Revises: 0031
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Apply order is the numbering. 38 adds tables that reference
# treatment_protocols (created by 32, in 0031); 39 adds columns to that same
# table; 40 reads the catalogue 38 and 39 leave in place.
SQL_FILES = [
    "37_clinic_devices.sql",
    "38_protocol_flow_completion.sql",
    "39_protocol_prescription_fields.sql",
    "40_seed_tdcs_reference.sql",
]

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL" / "v1"


def _split_statements(sql_text: str) -> list[str]:
    """Splits a .sql file on top-level semicolons.

    asyncpg will not execute a script containing multiple commands in one call,
    unlike `psql -f` which sends the whole file as a single simple-query
    message. So the file has to be split before it is sent.

    Handles arbitrary dollar-quote tags, not just bare `$$` — SQL/v1 uses
    `$function$` (32, 37, 39) and `$guard$` / `$seed$` (40), and a splitter
    that did not understand them would cut PL/pgSQL bodies in half at the first
    semicolon inside them.

    Kept as a copy of 0031's rather than imported from it: a migration that
    has run is a historical record, and reaching into a sibling revision for a
    helper means editing 0031 later silently changes what 0032 did.
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


def _strip_sql_comments(statement: str) -> str:
    """The statement with line comments and blank lines removed.

    Needed because every SQL/v1 file opens with a long `--` header, and the
    splitter attaches that header to the first real statement. So the opening
    BEGIN does not arrive as the string "BEGIN;" but as
    "-- 37_clinic_devices.sql\\n-- ...\\nBEGIN;".

    0031 compared the whole statement against {"BEGIN", "COMMIT", ...}, which
    matched the trailing COMMIT (nothing follows it) but never the leading
    BEGIN. The leak is not destructive — Postgres answers a nested BEGIN with
    `WARNING: there is already a transaction in progress` and ignores it — but
    it meant every file in 0031 emitted a warning, and leaving a known-wrong
    helper in place invites someone to trust it with a statement where the
    transaction boundary does matter.
    """
    lines = []
    for line in statement.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        lines.append(stripped)
    return " ".join(lines).strip()


def _is_transaction_control(statement: str) -> bool:
    """Files 32-40 wrap themselves in BEGIN/COMMIT so they can be pasted into a
    SQL client as one atomic unit. Alembic already runs each migration in a
    transaction, so re-issuing them here would either nest (a warning) or
    COMMIT mid-migration and defeat the point of the wrapper. Stripped, and the
    outer transaction does the same job.
    """
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

    39 adds columns to core.treatment_protocols that hold the prescribed dose.
    Dropping them discards what current, duration and cadence a patient was
    actually treated with — a clinical record, not a schema detail. 38's four
    tables are the same: protocol_custom_montages carries the clinical
    reasoning a doctor wrote for departing from the validated library.

    A downgrade that "succeeded" here would be reporting success for silently
    destroying prescribing history. Restore from a pre-migration snapshot,
    which is the honest operation and the one the runbook already calls for.
    """
    raise NotImplementedError(
        "0032 is not reversible: 38 and 39 add tables and columns holding the "
        "prescribed dose and the clinical reasoning behind custom montages. "
        "Restore from a pre-migration snapshot instead."
    )
