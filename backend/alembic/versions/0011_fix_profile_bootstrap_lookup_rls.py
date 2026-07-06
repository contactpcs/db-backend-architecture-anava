"""Fix profiles SELECT RLS policy blocking the auth middleware's own lookup.

core/middleware.py's "who am I" query (by cognito_sub, right after JWT
verification, before app.current_user_id/role can be set — this query is
what determines that context) was rejected by rls_profiles_select once RLS
was actually enforced for the first time (RDS cutover — local dev's role
bypasses RLS, so this never surfaced there). Adds a self-lookup-by-
cognito_sub clause, same principle as the existing id = rls_user_id() one.
Matches SQL/31_fix_profile_bootstrap_lookup_rls.sql — that file is the
schema source of truth per 0001's convention.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-06

"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def _split_statements(sql_text: str) -> list[str]:
    """Same as 0001_baseline_schema.py's splitter — this file's rls_cognito_sub()
    function has a $$ ... $$ body with a semicolon inside it, unlike 0007-0010's
    files, so the plain split-on-';' those use would fragment it mid-block."""
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
    # exec_driver_sql, not op.execute() — the latter goes through
    # SQLAlchemy's text() construct, which parses `:name` as a bind
    # parameter placeholder even inside a -- comment (this file's own
    # comments reference `:sub` as an example). exec_driver_sql sends the
    # raw string straight to the DBAPI driver, same as 0001_baseline_schema.py.
    bind = op.get_bind()
    sql_text = (SQL_DIR / "31_fix_profile_bootstrap_lookup_rls.sql").read_text(encoding="utf-8-sig")
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

        DROP POLICY IF EXISTS rls_csa_select ON clinic_staff_assignments;
        CREATE POLICY rls_csa_select ON clinic_staff_assignments FOR SELECT
        USING (
            rls_user_role() = 'super_admin'
            OR (rls_user_role() = 'regional_admin' AND clinic_id IN (
                SELECT clinic_id FROM clinics WHERE region_id = rls_region_id()
            ))
            OR clinic_id = rls_clinic_id()
        );
    """
    bind = op.get_bind()
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            bind.exec_driver_sql(statement)
