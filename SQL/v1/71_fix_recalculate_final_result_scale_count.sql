-- 71_fix_recalculate_final_result_scale_count.sql
--
-- APPLY ORDER: after 70. Independent of 51-70.
--
-- THE PROBLEM
--
-- core.recalculate_final_result() (14_functions.sql, trg_recalculate_final_
-- result on prs_scale_results) decides an assessment instance is complete by
-- comparing v_completed (DISTINCT scales actually scored for THIS instance)
-- against v_total_scales:
--
--     SELECT COUNT(*) INTO v_total_scales
--     FROM patient_scale_assignments
--     WHERE patient_id = v_instance.patient_id
--       AND assessment_stage = v_instance.assessment_stage
--       AND is_active = TRUE;
--
-- Two bugs in that one query:
--
--   1. Not scoped to the instance's disease_id — a patient with assignments
--      for more than one disease at the same assessment_stage (main_clinical,
--      say) gets ALL of them counted into v_total_scales for an instance that
--      only ever renders/scores ONE disease's scales (_compose_scales in
--      prs/service.py IS disease-scoped). v_completed can never reach a
--      denominator inflated by scales this instance was never asked.
--
--   2. Plain COUNT(*), not COUNT(DISTINCT scale_id) — patient_scale_
--      assignments has no uniqueness constraint on (patient, scale, disease,
--      stage), so re-assigning the same scale (a doctor's re-send, or the
--      device-session "Send to patient app" bug fixed client-side in
--      patients.service.ts/prsAssessment.service.ts, prs-neurowellness repo)
--      creates a second row rather than upserting one. Each duplicate row
--      inflates v_total_scales again even though _compose_scales already
--      dedupes them down to one rendered question block per scale_id.
--
-- Together: a patient assigned 3 real scales under one disease, with even
-- one duplicate assignment row anywhere in that (patient, stage) — same
-- disease or not — ends up needing 4+ scored scales to ever satisfy
-- v_completed >= v_total_scales. Since _compose_scales only ever renders
-- the 3 real ones, the instance can score every question it was actually
-- asked and still sit at status='in_progress' forever: confirmed against a
-- live instance (ae759fc4-eb8471a3, ADHD/2026, main_clinical) with 3/3
-- scale_results rows but v_total_scales evaluating to 6 from duplicate
-- EQ-5D-5L/DASS-21/COMPASS-31 assignment rows.
--
-- THE FIX
--
-- Scope the denominator to this instance's own disease_id (matching what
-- _compose_scales actually renders) and count DISTINCT scale_id so repeat
-- assignments of the same scale stop double-counting. Everything else in
-- the function (aggregation loop, prs_final_results upsert, status flip) is
-- unchanged.
--
-- NOTE: this does not delete the duplicate patient_scale_assignments rows
-- already in the table — those are harmless once this denominator ignores
-- them, and _compose_scales was already deduping them for rendering. If a
-- cleanup of the rows themselves is wanted later, that's a separate,
-- non-urgent migration.


BEGIN;

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

    -- Count DISTINCT scales actually assigned to this patient for THIS
    -- instance's disease + stage (not every disease that happens to share
    -- the stage, and not once per duplicate assignment row).
    SELECT COUNT(DISTINCT scale_id) INTO v_total_scales
    FROM core.patient_scale_assignments
    WHERE patient_id = v_instance.patient_id
      AND assessment_stage = v_instance.assessment_stage
      AND disease_id = v_instance.disease_id
      AND is_active = TRUE;

    -- Aggregate all scale results for this instance
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


-- ###########################################################################
-- BACKFILL — recheck every instance stuck at in_progress that actually
-- finished everything it was asked, now that the denominator is fixed.
-- ###########################################################################
--
-- Re-fires the trigger for every existing prs_scale_results row by touching
-- time_stamp — cheapest no-op UPDATE that still satisfies the trigger's
-- "AFTER INSERT OR UPDATE" condition without changing any real column.

DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN SELECT scale_result_id FROM core.prs_scale_results LOOP
        UPDATE core.prs_scale_results
        SET time_stamp = time_stamp
        WHERE scale_result_id = r.scale_result_id;
    END LOOP;
END $$;

COMMIT;
