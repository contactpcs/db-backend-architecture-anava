"""Fixes core.fn_check_protocol_device_consistency (32) so it stops
rejecting every custom-montage protocol.

Runs SQL/v1/55_custom_montage_device_trigger_fix.sql, which redefines the
BEFORE INSERT OR UPDATE trigger function on protocol_plan to short-circuit
when custom_montage_id IS NOT NULL — 54 added that column but never touched
this trigger, so every custom-montage protocol insert failed with "Protocol
device_id ... does not match the placement's device <NULL>" (the six
catalogue placement/dosing columns the trigger COALESCEs over are all NULL
by construction on a custom-montage row). See that file's header for the
full "why".

Revision ID: 0035
Revises: 0034
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQL_FILES = [
    "55_custom_montage_device_trigger_fix.sql",
]

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL" / "v1"


def _split_statements(sql_text: str) -> list[str]:
    """Splits a .sql file on top-level semicolons. Copy of 0034's — kept
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
    """Reverts the function body to 0032's version — no custom_montage_id
    short-circuit, so custom-montage protocol creation breaks again exactly
    as it did before 0035. Safe as a straight CREATE OR REPLACE, since
    nothing about the trigger's attachment (table, timing, event) changes
    in either direction — only the function body."""
    for statement in [
        """
CREATE OR REPLACE FUNCTION core.fn_check_protocol_device_consistency()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_placement_device UUID;
    v_dosing_device    UUID;
BEGIN
    SELECT COALESCE(
        (SELECT device_id FROM reference.tdcs_placements    WHERE tdcs_placement_id    = NEW.tdcs_placement_id),
        (SELECT device_id FROM reference.hd_tdcs_placements WHERE hd_tdcs_placement_id = NEW.hd_tdcs_placement_id),
        (SELECT device_id FROM reference.tavns_placements   WHERE tavns_placement_id   = NEW.tavns_placement_id),
        (SELECT device_id FROM reference.tps_placements     WHERE tps_placement_id     = NEW.tps_placement_id),
        (SELECT device_id FROM reference.rtms_placements    WHERE rtms_placement_id    = NEW.rtms_placement_id),
        (SELECT device_id FROM reference.other_placements   WHERE other_placement_id   = NEW.other_placement_id)
    ) INTO v_placement_device;

    SELECT COALESCE(
        (SELECT device_id FROM reference.tdcs_dosing    WHERE tdcs_dosing_id    = NEW.tdcs_dosing_id),
        (SELECT device_id FROM reference.hd_tdcs_dosing WHERE hd_tdcs_dosing_id = NEW.hd_tdcs_dosing_id),
        (SELECT device_id FROM reference.tavns_dosing   WHERE tavns_dosing_id   = NEW.tavns_dosing_id),
        (SELECT device_id FROM reference.tps_dosing     WHERE tps_dosing_id     = NEW.tps_dosing_id),
        (SELECT device_id FROM reference.rtms_dosing    WHERE rtms_dosing_id    = NEW.rtms_dosing_id),
        (SELECT device_id FROM reference.other_dosing   WHERE other_dosing_id   = NEW.other_dosing_id)
    ) INTO v_dosing_device;

    IF v_placement_device IS DISTINCT FROM NEW.device_id THEN
        RAISE EXCEPTION 'Protocol device_id % does not match the placement''s device %',
            NEW.device_id, v_placement_device;
    END IF;
    IF v_dosing_device IS DISTINCT FROM NEW.device_id THEN
        RAISE EXCEPTION 'Protocol device_id % does not match the dosing''s device %',
            NEW.device_id, v_dosing_device;
    END IF;

    RETURN NEW;
END;
$function$;
""".strip()
    ]:
        op.execute(statement)
