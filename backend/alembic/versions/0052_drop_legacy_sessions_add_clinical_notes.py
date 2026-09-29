"""Drops the two dead tables core.sessions and core.doctor_session_notes
(0-row, no code path writing or reading them since commit b341817 per
58_protocol_instances_absorb_cycle.sql's own comments) and the 6 FK
constraints elsewhere in the schema that dangled off core.sessions.
Replaces doctor_session_notes with a new core.patient_clinical_notes table
matching what the doctor portal's Doctor's Notes tab actually needs — a
free-form, categorized, chronological note log per patient — instead of the
old table's SOAP-note shape tied to the retired session/cycle model.

Runs SQL/v1/99_drop_legacy_sessions_add_clinical_notes.sql.

Revision ID: 0052
Revises: 0051
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQL_FILES = [
    "99_drop_legacy_sessions_add_clinical_notes.sql",
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
    """Drops patient_clinical_notes and recreates core.sessions /
    core.doctor_session_notes with their pre-0052 shape (from
    05_tables_core.sql), including the 6 FK constraints this revision
    stripped. The two recreated tables are empty — they were 0-row before
    this revision too, so there is no data to restore."""
    op.execute('DROP TABLE IF EXISTS core."patient_clinical_notes" CASCADE')

    op.execute("""
CREATE TABLE core."sessions" (
    "session_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID,
    "session_date" TIMESTAMPTZ NOT NULL,
    "session_type" TEXT NOT NULL DEFAULT 'in_person'::text,
    "notes" TEXT,
    "status" TEXT NOT NULL DEFAULT 'scheduled'::text,
    "cycle_id" UUID,
    "clinic_id" UUID,
    "ca_id" UUID,
    "session_phase" TEXT,
    "session_number_in_cycle" INTEGER,
    "outcome" TEXT,
    "started_at" TIMESTAMPTZ,
    "completed_at" TIMESTAMPTZ,
    "payment_status" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "sessions_pkey" PRIMARY KEY ("session_id")
)
""")

    op.execute("""
CREATE TABLE core."doctor_session_notes" (
    "note_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "session_id" UUID NOT NULL,
    "cycle_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "session_number" INTEGER NOT NULL,
    "session_phase" TEXT NOT NULL,
    "chief_complaint" TEXT,
    "clinical_observations" TEXT,
    "assessment" TEXT,
    "treatment_plan_notes" TEXT,
    "follow_up_instructions" TEXT,
    "referrals" TEXT,
    "note_content" TEXT,
    "is_confidential" BOOLEAN NOT NULL DEFAULT false,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "appointment_id" UUID,
    CONSTRAINT "doctor_session_notes_pkey" PRIMARY KEY ("note_id"),
    CONSTRAINT "doctor_session_notes_session_id_doctor_id_session_phase_key" UNIQUE ("session_id", "doctor_id", "session_phase")
)
""")

    op.execute(
        'ALTER TABLE core."appointments" ADD CONSTRAINT "fk_appointments_session_id" '
        'FOREIGN KEY ("session_id") REFERENCES core."sessions" ("session_id") ON DELETE RESTRICT'
    )
    op.execute(
        'ALTER TABLE core."doctor_session_notes" ADD CONSTRAINT "fk_doctor_session_notes_session_id" '
        'FOREIGN KEY ("session_id") REFERENCES core."sessions" ("session_id") ON DELETE RESTRICT'
    )
    op.execute(
        'ALTER TABLE core."doctor_session_notes" ADD CONSTRAINT "fk_doctor_session_notes_appointment_id" '
        'FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT'
    )
    op.execute(
        'ALTER TABLE core."patient_eeg_files" ADD CONSTRAINT "fk_patient_eeg_files_session_id" '
        'FOREIGN KEY ("session_id") REFERENCES core."sessions" ("session_id") ON DELETE RESTRICT'
    )
    op.execute(
        'ALTER TABLE core."payments" ADD CONSTRAINT "fk_payments_session_id" '
        'FOREIGN KEY ("session_id") REFERENCES core."sessions" ("session_id") ON DELETE RESTRICT'
    )
    op.execute(
        'ALTER TABLE core."prs_assessment_instances" ADD CONSTRAINT "fk_prs_assessment_instances_session_id" '
        'FOREIGN KEY ("session_id") REFERENCES core."sessions" ("session_id") ON DELETE RESTRICT'
    )
    op.execute(
        'ALTER TABLE core."treatment_sessions" ADD CONSTRAINT "fk_treatment_sessions_session_id" '
        'FOREIGN KEY ("session_id") REFERENCES core."sessions" ("session_id") ON DELETE RESTRICT'
    )
