-- 97_fix_recalculate_final_result_disease_scope.sql
--
-- THE PROBLEM
--
-- recalculate_final_result() (07_prs_tables.sql / MasterDB_Anava.sql) counts
-- v_total_scales as every ACTIVE patient_scale_assignments row for this
-- patient + assessment_stage — with no disease_id filter. A patient with
-- more than one disease assigned under the same stage (e.g. main_clinical
-- ATAXIA: 7 scales, plus main_clinical DEMENTIA: its own scales) gets a
-- v_total_scales that's the SUM across every disease, not just the one
-- this instance belongs to. v_completed only ever counts THIS instance's
-- own scored scales (scoped by instance_id via prs_scale_results), so once
-- a patient has 2+ diseases under one stage, v_completed can never reach
-- the inflated v_total_scales and the instance never flips to 'completed'
-- — confirmed live: a 7-scale ATAXIA instance had all 7 prs_scale_results
-- rows present and scored, but stayed status='in_progress' indefinitely
-- because this patient also had an active DEMENTIA main_clinical
-- assignment inflating the denominator.
--
-- THE FIX
--
-- Scope v_total_scales to the instance's own disease_id too, using
-- IS NOT DISTINCT FROM (not =) so a general_registration instance
-- (disease_id NULL, per 70_remove_disease_selection.sql) still matches
-- correctly against patient_scale_assignments rows with a NULL disease_id.

BEGIN;

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

    -- Count scales actually assigned to this patient for THIS INSTANCE'S
    -- disease + stage (not every disease active under the same stage) —
    -- see this file's header for the bug this closes.
    SELECT COUNT(*) INTO v_total_scales
    FROM patient_scale_assignments
    WHERE patient_id = v_instance.patient_id
      AND assessment_stage = v_instance.assessment_stage
      AND disease_id IS NOT DISTINCT FROM v_instance.disease_id
      AND is_active = TRUE;

    -- Aggregate all scale results for this instance
    FOR r IN
        SELECT sr.*, sc.scale_code, sc.scale_name
        FROM prs_scale_results sr
        JOIN prs_scales sc ON sc.scale_id = sr.scale_id
        WHERE sr.instance_id = NEW.instance_id
    LOOP
        v_total     := v_total + COALESCE(r.calculated_value, 0);
        v_max       := v_max   + COALESCE(r.max_possible, 0);
        v_completed := v_completed + 1;

        -- Track worst severity across all scales
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

        -- Build per-scale summary snapshot
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

        -- Collect risk flags from all scales
        IF r.risk_flags IS NOT NULL AND jsonb_array_length(r.risk_flags) > 0 THEN
            v_all_flags := v_all_flags || r.risk_flags;
        END IF;
    END LOOP;

    -- Upsert prs_final_results
    INSERT INTO prs_final_results (
        final_result_id,
        instance_id,
        calculated_value,
        max_possible,
        scales_completed,
        scales_total,
        overall_severity,
        overall_severity_label,
        scale_summaries,
        all_risk_flags,
        time_stamp
    ) VALUES (
        NEW.instance_id || '/' || v_instance.disease_id,
        NEW.instance_id,
        v_total,
        v_max,
        v_completed,
        v_total_scales,
        v_worst_sev,
        v_worst_label,
        v_summaries,
        v_all_flags,
        NOW()
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

    -- Mark instance completed when all scales are scored
    IF v_completed >= v_total_scales THEN
        UPDATE prs_assessment_instances
        SET
            status       = 'completed',
            completed_at = NOW(),
            final_result = (
                SELECT final_result_id
                FROM prs_final_results
                WHERE instance_id = NEW.instance_id
            )
        WHERE instance_id = NEW.instance_id
          AND status != 'completed';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMIT;
