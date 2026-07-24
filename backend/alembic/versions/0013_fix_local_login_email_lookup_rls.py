"""Fix local_login's email-lookup path blocked by real RLS enforcement.

Same class of bug as 0011 (cognito_sub self-lookup) and 0012 (public
clinics) — auth/router.py's local_login endpoint, called with email (what
the frontend's login form actually sends), ran its "find my cognito_sub"
query with zero RLS context — the exact chicken-and-egg shape 0011 already
fixed for cognito_sub, just not yet for email. Adds a matching self-lookup-
by-email clause. Matches SQL/33_fix_local_login_email_lookup_rls.sql — that
file is the schema source of truth per 0001's convention.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-08

"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def _split_statements(sql_text: str) -> list[str]:
    """Same dollar-quote-aware splitter as 0001_baseline_schema.py / 0011 —
    this file's rls_email() function has a $$ ... $$ body with a semicolon
    inside it, which the plain split-on-';' used by 0007-0010/0012 would
    fragment mid-block."""
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
        return all((not line.strip()) or line.strip().startswith("--") for line in chunk.splitlines())

    return [s for s in statements if s.strip() and not is_only_comments(s)]


def upgrade() -> None:
    # exec_driver_sql, not op.execute() — the latter parses `:name` as a
    # bind parameter even inside a -- comment (this file's own comments
    # reference `:email` as an example), which breaks on this file the same
    # way it did on 0011's. See that migration for the full explanation.
    bind = op.get_bind()
    sql_text = (SQL_DIR / "33_fix_local_login_email_lookup_rls.sql").read_text(encoding="utf-8-sig")
    for statement in _split_statements(sql_text):
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_profiles_select ON profiles;
        CREATE POLICY rls_profiles_select ON profiles FOR SELECT
        USING (
            rls_user_role() = 'super_admin'
            OR rls_user_role() = 'regional_admin'
            OR id = rls_user_id()
            OR cognito_sub = rls_cognito_sub()
            OR (
                rls_user_role() IN ('clinic_admin', 'doctor', 'clinical_assistant', 'receptionist')
                AND id IN (
                    SELECT profile_id FROM clinic_staff_assignments
                    WHERE clinic_id = rls_clinic_id() AND is_active = TRUE
                    UNION
                    SELECT profile_id FROM patients
                    WHERE primary_clinic_id = rls_clinic_id()
                )
            )
        );
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
