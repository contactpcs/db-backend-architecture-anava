-- 74_fix_recalculate_final_result_null_disease_id.sql
--
-- APPLY ORDER: after 73. Depends on 71 (core.recalculate_final_result()).
--
-- THE PROBLEM
--
-- core.recalculate_final_result() (14_functions.sql, rewritten by 71) builds
-- prs_final_results.final_result_id (TEXT, NOT NULL PK) as:
--
--     NEW.instance_id || '/' || v_instance.disease_id
--
-- prs_assessment_instances.disease_id is nullable — 71's own header names
-- the case it exists for: an instance with assessment_stage =
-- 'general_registration' has no disease (prs/service.py's create_instance
-- allows disease_id IS NULL exactly when assessment_stage =
-- 'general_registration'). Postgres string concatenation with `||` returns
-- NULL when either side is NULL, so scoring ANY scale on a general-
-- registration instance computes final_result_id = NULL and the INSERT
-- fails with:
--
--     null value in column "final_result_id" of relation "prs_final_results"
--     violates not-null constraint
--
-- Confirmed live 2026-09-04 (prod): a scale_results write on a
-- general_registration instance crashed the request with exactly this
-- IntegrityError, surfaced as a raw 500 through the ASGI stack.
--
-- THE FIX
--
-- final_result_id only needs to be a stable, non-null string unique per
-- instance_id (the upsert already targets ON CONFLICT (instance_id), not
-- this column) — COALESCE onto instance_id alone when disease_id is NULL.
-- Everything else in the function (71's fixed scale-counting denominator,
-- the aggregation loop, the status flip) is unchanged.

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
    --
    -- IS NOT DISTINCT FROM, not = : disease_id is NULL for a
    -- general_registration instance, and NULL = NULL is NULL (not TRUE) in
    -- SQL, which would silently zero out this count instead of matching the
    -- assignment rows that are themselves disease_id IS NULL.
    SELECT COUNT(DISTINCT scale_id) INTO v_total_scales
    FROM core.patient_scale_assignments
    WHERE patient_id = v_instance.patient_id
      AND assessment_stage = v_instance.assessment_stage
      AND disease_id IS NOT DISTINCT FROM v_instance.disease_id
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
        -- COALESCE, not a bare concat: v_instance.disease_id is NULL for a
        -- general_registration instance, and `a || NULL` is NULL in
        -- Postgres, which crashed the NOT NULL PK below before this fix.
        -- final_result_id only has to be a stable string unique per
        -- instance_id — the upsert below targets instance_id, not this
        -- column — so falling back to the bare instance_id is safe.
        COALESCE(NEW.instance_id || '/' || v_instance.disease_id, NEW.instance_id),
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

COMMIT;


-- ###########################################################################
-- NO BACKFILL NEEDED
-- ###########################################################################
-- Unlike 71, this fix does not change which instances count as complete —
-- only whether a general_registration instance's scoring INSERT succeeds at
-- all. Nothing was silently left in a wrong state to reconcile; requests
-- that previously 500'd on this path will simply succeed on retry.
