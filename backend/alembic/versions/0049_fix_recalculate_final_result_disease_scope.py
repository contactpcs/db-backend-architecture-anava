"""Fixes recalculate_final_result()'s v_total_scales count, which summed
patient_scale_assignments across EVERY disease active under an assessment
stage instead of just the instance's own disease — a patient with 2+
diseases assigned under the same stage (e.g. main_clinical ATAXIA + main_
clinical DEMENTIA) got an inflated denominator that v_completed (scoped to
one instance) could never reach, so the instance stayed 'in_progress'
forever even after every one of its own scales was scored and its
prs_scale_results rows all existed. Confirmed live against a 7-scale ATAXIA
instance with all 7 scored.

Runs SQL/v1/97_fix_recalculate_final_result_disease_scope.sql.

Revision ID: 0049
Revises: 0048
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQL_FILES = [
    "97_fix_recalculate_final_result_disease_scope.sql",
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
    """Restores the pre-0049 function body (no disease_id scoping on
    v_total_scales) — exact text from MasterDB_Anava.sql / 07_prs_tables.sql."""
    op.execute("""
CREATE OR REPLACE FUNCTION recalculate_final_result()
RETURNS TRIGGER AS $$
DECLARE
    v_instance      prs_assessment_instances%ROWTYPE;
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
    FROM prs_assessment_instances
    WHERE instance_id = NEW.instance_id;

    SELECT COUNT(*) INTO v_total_scales
    FROM patient_scale_assignments
    WHERE patient_id = v_instance.patient_id
      AND assessment_stage = v_instance.assessment_stage
      AND is_active = TRUE;

    FOR r IN
        SELECT sr.*, sc.scale_code, sc.scale_name
        FROM prs_scale_results sr
        JOIN prs_scales sc ON sc.scale_id = sr.scale_id
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

    INSERT INTO prs_final_results (
        final_result_id, instance_id, calculated_value, max_possible,
        scales_completed, scales_total, overall_severity, overall_severity_label,
        scale_summaries, all_risk_flags, time_stamp
    ) VALUES (
        NEW.instance_id || '/' || v_instance.disease_id, NEW.instance_id, v_total, v_max,
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
        UPDATE prs_assessment_instances
        SET status = 'completed', completed_at = NOW(),
            final_result = (SELECT final_result_id FROM prs_final_results WHERE instance_id = NEW.instance_id)
        WHERE instance_id = NEW.instance_id AND status != 'completed';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""")
