"""Fix PRS completion trigger to be assessment_stage-aware.

recalculate_final_result() (SQL/07_prs_tables.sql) counted ALL scales
mapped to a disease as "total scales for this instance", regardless of
assessment_stage. Scale assignment only assigns scales whose applicable_for
matches the instance's own stage (or 'all'), so any disease with scales
split across stages could never reach v_completed >= v_total_scales for a
narrower stage (e.g. general_registration) — the instance stayed
'in_progress' forever, and registration_status could never reach
'general_prs_complete'. Matches SQL/25_fix_prs_stage_completion_trigger.sql
— that file is the schema source of truth per 0001's convention.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-03

"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    sql_text = (SQL_DIR / "25_fix_prs_stage_completion_trigger.sql").read_text(encoding="utf-8-sig")
    op.execute(sql_text)


def downgrade() -> None:
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
            FROM prs_disease_scale_map
            WHERE disease_id = v_instance.disease_id;

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
