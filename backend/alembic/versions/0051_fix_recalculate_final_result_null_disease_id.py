"""Fixes recalculate_final_result()'s final_result_id computation, which
built the PK as `NEW.instance_id || '/' || v_instance.disease_id`. Postgres
string concat with `||` returns NULL when either operand is NULL, so scoring
any scale on a general_registration instance (disease_id IS NULL) crashed the
INSERT into prs_final_results with:

    null value in column "final_result_id" of relation "prs_final_results"
    violates not-null constraint

Confirmed live 2026-09-28 (staging): every scale-submit on a
general_registration instance raised IntegrityError → 500.

Fix: wrap in COALESCE so the PK falls back to the bare instance_id when
disease_id is NULL (it only needs to be a stable non-null string unique per
instance_id — the upsert targets ON CONFLICT (instance_id), not this column).

Runs SQL/v1/74_fix_recalculate_final_result_null_disease_id.sql.

Revision ID: 0051
Revises: 0050
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL" / "v1"

SQL_FILES = [
    "74_fix_recalculate_final_result_null_disease_id.sql",
]


def _split_statements(sql_text: str) -> list[str]:
    """Splits a .sql file on top-level semicolons. Handles dollar-quoted
    blocks and single-quoted strings correctly so that semicolons inside
    function bodies are not treated as statement terminators."""
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
    """Restores the pre-0051 function body — the 97_fix version from 0050,
    which had disease-scoped v_total_scales but still used the bare concat
    (without COALESCE) for final_result_id. general_registration instances
    will crash again on scale submit after downgrade."""
    op.execute("""
CREATE OR REPLACE FUNCTION core.recalculate_final_result()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_instance      core.prs_assessment_instances%ROWTYPE;
    v_total         NUMERIC := 0;
    v_max           NUMERIC := 0;
    v_completed     INTEGER := 0;
    v_total_scales  INTEGER := 0;
    v_worst_sev     TEXT    := NULL;
    v_worst_label   TEXT    := NULL;
    v_summaries     JSONB   := '[]'::JSONB;
    v_all_flags     JSONB   := '[]'::JSONB;
    sev_order       INTEGER;
    worst_order     INTEGER := -1;
    r               RECORD;
BEGIN
    SELECT * INTO v_instance
    FROM core.prs_assessment_instances
    WHERE instance_id = NEW.instance_id;

    SELECT COUNT(DISTINCT scale_id) INTO v_total_scales
    FROM core.patient_scale_assignments
    WHERE patient_id = v_instance.patient_id
      AND assessment_stage = v_instance.assessment_stage
      AND disease_id IS NOT DISTINCT FROM v_instance.disease_id
      AND is_active = TRUE;

    FOR r IN
        SELECT sr.*, sc.scale_code, sc.scale_name
        FROM core.prs_scale_results sr
        JOIN reference.prs_scales sc ON sc.scale_id = sr.scale_id
        WHERE sr.instance_id = NEW.instance_id
    LOOP
        v_total     := v_total + COALESCE(r.calculated_value, 0);
        v_max       := v_max   + COALESCE(r.max_possible, 0);
        v_completed := v_completed + 1;

        sev_order := CASE r.severity_level
            WHEN 'severe'            THEN 4
            WHEN 'moderately-severe' THEN 3
            WHEN 'moderate'          THEN 2
            WHEN 'mild'              THEN 1
            ELSE 0
        END;
        IF sev_order > worst_order THEN
            worst_order   := sev_order;
            v_worst_sev   := r.severity_level;
            v_worst_label := r.severity_label;
        END IF;

        v_summaries := v_summaries || jsonb_build_object(
            'scale_code',     r.scale_code,
            'scale_name',     r.scale_name,
            'score',          r.calculated_value,
            'max_possible',   r.max_possible,
            'percentage',     CASE WHEN r.max_possible > 0
                                   THEN ROUND((r.calculated_value / r.max_possible) * 100, 2)
                                   ELSE NULL END,
            'severity_level', r.severity_level,
            'severity_label', r.severity_label
        );

        IF r.risk_flags IS NOT NULL AND jsonb_array_length(r.risk_flags) > 0 THEN
            v_all_flags := v_all_flags || r.risk_flags;
        END IF;
    END LOOP;

    INSERT INTO core.prs_final_results (
        final_result_id, instance_id, calculated_value, max_possible,
        scales_completed, scales_total, overall_severity, overall_severity_label,
        scale_summaries, all_risk_flags, time_stamp
    ) VALUES (
        NEW.instance_id || '/' || v_instance.disease_id,
        NEW.instance_id, v_total, v_max,
        v_completed, v_total_scales, v_worst_sev, v_worst_label,
        v_summaries, v_all_flags, NOW()
    )
    ON CONFLICT (instance_id) DO UPDATE SET
        calculated_value        = EXCLUDED.calculated_value,
        max_possible            = EXCLUDED.max_possible,
        scales_completed        = EXCLUDED.scales_completed,
        scales_total            = EXCLUDED.scales_total,
        overall_severity        = EXCLUDED.overall_severity,
        overall_severity_label  = EXCLUDED.overall_severity_label,
        scale_summaries         = EXCLUDED.scale_summaries,
        all_risk_flags          = EXCLUDED.all_risk_flags,
        time_stamp              = EXCLUDED.time_stamp;

    IF v_completed >= v_total_scales THEN
        UPDATE core.prs_assessment_instances
        SET
            status       = 'completed',
            completed_at = NOW(),
            final_result = (
                SELECT final_result_id
                FROM core.prs_final_results
                WHERE instance_id = NEW.instance_id
            )
        WHERE instance_id = NEW.instance_id
          AND status != 'completed';
    END IF;

    RETURN NEW;
END;
$function$;
""")
