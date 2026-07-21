-- Generated from live production schema introspection (2026-07-20). Do not hand-edit column/RLS/trigger/function bodies — regenerate from source instead.

CREATE OR REPLACE FUNCTION ops.fn_audit_trigger()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
DECLARE
    v_pk_col    TEXT := TG_ARGV[0];
    v_record_id TEXT;
    v_old_data  JSONB;
    v_new_data  JSONB;
    v_user_id   UUID;
BEGIN
    -- Read actor from session context (set by FastAPI middleware)
    BEGIN
        v_user_id := NULLIF(current_setting('app.current_user_id', TRUE), '')::UUID;
    EXCEPTION WHEN others THEN
        v_user_id := NULL;
    END;

    IF TG_OP = 'DELETE' THEN
        v_old_data  := to_jsonb(OLD);
        v_new_data  := NULL;
        v_record_id := v_old_data ->> v_pk_col;   -- TEXT, no ::UUID cast
    ELSIF TG_OP = 'INSERT' THEN
        v_old_data  := NULL;
        v_new_data  := to_jsonb(NEW);
        v_record_id := v_new_data ->> v_pk_col;   -- TEXT, no ::UUID cast
    ELSE  -- UPDATE
        v_old_data  := to_jsonb(OLD);
        v_new_data  := to_jsonb(NEW);
        v_record_id := v_new_data ->> v_pk_col;   -- TEXT, no ::UUID cast
    END IF;

    INSERT INTO audit_logs (table_name, operation, record_id, old_data, new_data, changed_by)
    VALUES (TG_TABLE_NAME, TG_OP, v_record_id, v_old_data, v_new_data, v_user_id);

    RETURN NULL;  -- AFTER trigger; return value ignored
END;
$function$;


CREATE OR REPLACE FUNCTION core.fn_generate_mrn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.mrn IS NULL THEN
        NEW.mrn := 'ANV-' || LPAD(nextval('core.mrn_seq')::TEXT, 8, '0');
    END IF;
    RETURN NEW;
END;
$function$;


CREATE OR REPLACE FUNCTION ops.fn_notify_outbox_event()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    PERFORM pg_notify('outbox_new_event', NEW.outbox_id::TEXT);
    RETURN NEW;
END;
$function$;


CREATE OR REPLACE FUNCTION ops.fn_set_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$function$;


CREATE OR REPLACE FUNCTION core.recalculate_final_result()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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

    -- Count only scales that apply to THIS instance's assessment_stage.
    SELECT COUNT(*) INTO v_total_scales
    FROM prs_disease_scale_map m
    JOIN prs_scales sc ON sc.scale_id = m.scale_id
    WHERE m.disease_id = v_instance.disease_id
      AND sc.applicable_for IN (v_instance.assessment_stage, 'all');

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
$function$;


CREATE OR REPLACE FUNCTION ops.rls_user_role()
 RETURNS text
 LANGUAGE sql
 STABLE
AS $function$
    SELECT NULLIF(current_setting('app.current_user_role', TRUE), '');
$function$;


CREATE OR REPLACE FUNCTION ops.rls_user_id()
 RETURNS uuid
 LANGUAGE sql
 STABLE
AS $function$
    SELECT NULLIF(current_setting('app.current_user_id', TRUE), '')::UUID;
$function$;


CREATE OR REPLACE FUNCTION ops.rls_region_id()
 RETURNS uuid
 LANGUAGE sql
 STABLE
AS $function$
    SELECT NULLIF(current_setting('app.current_region_id', TRUE), '')::UUID;
$function$;


CREATE OR REPLACE FUNCTION ops.rls_email()
 RETURNS text
 LANGUAGE sql
 STABLE
AS $function$
    SELECT NULLIF(current_setting('app.current_email', TRUE), '');
$function$;


CREATE OR REPLACE FUNCTION ops.rls_cognito_sub()
 RETURNS text
 LANGUAGE sql
 STABLE
AS $function$
    SELECT NULLIF(current_setting('app.current_cognito_sub', TRUE), '');
$function$;


CREATE OR REPLACE FUNCTION ops.rls_clinic_id()
 RETURNS uuid
 LANGUAGE sql
 STABLE
AS $function$
    SELECT NULLIF(current_setting('app.current_clinic_id', TRUE), '')::UUID;
$function$;

