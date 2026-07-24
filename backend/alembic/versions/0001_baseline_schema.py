"""Baseline schema — applies SQL/*.sql directly.

Generated FROM the hand-designed schema in D:\\PCS\\backend-v2\\SQL\\, not
hand-written from scratch (see Architecture Section 5 folder-structure note).
SQL/*.sql is the schema source of truth (Architecture Section 0.1) — if the
schema needs to change, edit the SQL/ files first, then add a new Alembic
revision, never hand-edit what this revision applies.

Revision ID: 0001
Revises:
Create Date: 2026-07-01

"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Order matches SQL/00_run_all.sql — foreign key dependencies enforced.
# 00_run_all.sql itself is excluded (psql \\i meta-commands, not raw SQL).
SQL_FILES = [
    "01_extensions.sql",
    "02_core_tables.sql",
    "03_staff_role_tables.sql",
    "04_patient_tables.sql",
    "05_request_tables.sql",
    "06_clinical_tables.sql",
    "06b_appointment_tables.sql",
    "07_prs_tables.sql",
    "08_anamnesis_tables.sql",
    "08b_patient_files.sql",
    "09_consent_tables.sql",
    "10_store_tables.sql",
    "11_payment_tables.sql",
    "12_logging_tables.sql",
    "12b_notifications.sql",
    "13_indexes.sql",
    "14_triggers.sql",
    "15_rls_policies.sql",
    "16_seed_data.sql",
    "17_outbox_events.sql",
    "18_appointment_overlap_guard.sql",
]

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def _split_statements(sql_text: str) -> list[str]:
    """Splits a .sql file into individual statements on top-level semicolons,
    respecting $$ dollar-quoted PL/pgSQL bodies and '...' string literals so a
    semicolon inside a DO $$ ... $$ block or a string doesn't split mid-block.

    Needed because asyncpg (via SQLAlchemy's prepared-statement protocol) will
    not execute a script containing multiple commands in one call — unlike
    `psql -f`, which sends the whole file as one simple-query message. Every
    file in SQL/ only uses bare `$$` tags (verified), not `$tag$` variants.
    """
    statements = []
    buf = []
    in_dollar = False
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
        if not in_single_quote and not in_dollar and sql_text.startswith("--", i):
            in_line_comment = True
            buf.append("--")
            i += 2
            continue
        if not in_single_quote and sql_text.startswith("$$", i):
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        if not in_dollar and ch == "'":
            in_single_quote = not in_single_quote
            buf.append(ch)
            i += 1
            continue
        if not in_dollar and not in_single_quote and ch == ";":
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
        return all(
            (not line.strip()) or line.strip().startswith("--")
            for line in chunk.splitlines()
        )

    return [s for s in statements if s.strip() and not is_only_comments(s)]


def upgrade() -> None:
    bind = op.get_bind()
    for filename in SQL_FILES:
        # utf-8-sig strips a leading BOM if present (several SQL/ files have one) —
        # a bare "utf-8" read leaves ﻿ as the first character, which Postgres
        # rejects as a syntax error before the first real statement.
        sql_text = (SQL_DIR / filename).read_text(encoding="utf-8-sig")
        for statement in _split_statements(sql_text):
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    # Baseline downgrade is a full reset — acceptable pre-launch (no production
    # data exists yet). Once real data exists, downgrades must become
    # additive/reversible per-change, never a schema-wide drop.
    #
    # KNOWN LIMITATION: DROP SCHEMA CASCADE also drops alembic_version itself,
    # which Alembic then tries to update immediately after this function
    # returns (DELETE FROM alembic_version WHERE ...) — that fails because the
    # table it needs is gone. For a full local reset, don't use
    # `alembic downgrade base`; instead reset the Docker volume and run
    # `alembic upgrade head` fresh: `docker compose down -v && docker compose
    # up -d && alembic upgrade head`. Not worth solving properly for a dev-only
    # reset path — fixing it for real means never CASCADE-dropping
    # alembic_version, which means this can no longer be a true full reset.
    bind = op.get_bind()
    bind.exec_driver_sql("DROP SCHEMA public CASCADE;")
    bind.exec_driver_sql("CREATE SCHEMA public;")
