-- 101_auth_context_lookup.sql
--
-- One-call auth context lookup (design: Documents/API_Audit/design_auth_context_lookup.md).
-- Replaces the up-to-9 separate statements AuthContextMiddleware._load_profile_and_scope
-- sent on EVERY request (set_config + profile + consent re-check + scope + clinic /
-- region lockout) with one round trip. Same queries, same order, same RLS:
-- SECURITY INVOKER on purpose, so every lookup is still filtered by the caller's
-- own RLS self-lookup clauses, exactly as before (SQL/31 bootstrap fix).
--
-- Returns zero rows when no profile matches the Cognito sub (-> PROFILE_NOT_FOUND).
-- clinic_closed / region_inactive are flags; the app raises CLINIC_CLOSED /
-- REGION_INACTIVE after this transaction commits, so a consent self-heal write
-- still sticks when the request is then rejected (as on the old separate heal
-- connection).
--
-- No search_path SET clause: it would be inherited by triggers fired by the
-- self-heal UPDATE on profiles, which rely on the database-level search_path.

CREATE OR REPLACE FUNCTION ops.auth_context(p_cognito_sub text)
RETURNS TABLE (
    profile_id      uuid,
    user_role       text,
    active          boolean,
    consent         boolean,
    scope_clinic_id uuid,
    scope_region_id uuid,
    clinic_closed   boolean,
    region_inactive boolean
)
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
AS $function$
#variable_conflict use_column
DECLARE
    v_id            uuid;
    v_role          text;
    v_active        boolean;
    v_consent       boolean;
    v_signed        boolean;
    v_clinic        uuid;
    v_region        uuid;
    v_status        text;
    v_clinic_region uuid;
    v_region_active boolean;
BEGIN
    -- Must precede the profile read: rls_profiles_select's self-lookup clause
    -- matches on app.current_cognito_sub (user id/role are not known yet).
    PERFORM set_config('app.current_cognito_sub', p_cognito_sub, true);

    SELECT p.id, p.role, p.is_active, p.consent_signed
      INTO v_id, v_role, v_active, v_consent
      FROM core.profiles p
     WHERE p.cognito_sub = p_cognito_sub;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    -- Known now: every lookup below satisfies its own RLS self-lookup clause.
    PERFORM set_config('app.current_user_id', v_id::text, true);
    PERFORM set_config('app.current_user_role', v_role, true);

    -- Staff consent gate: is_active must mirror a REAL signed staff_onboarding
    -- record. Patients have their own activation gate and are never touched.
    --   inactive + unsigned but a signed record exists -> heal to active
    --   active but no signed record                    -> revoke
    --   inactive + consent TRUE = deliberate deactivation -> left alone, no query
    -- The write runs as the old separate heal connection did: role 'system'
    -- and NO user identity set (cognito_sub / user_id cleared), then restored,
    -- so policies and any audit trigger see exactly what they saw before.
    IF v_role <> 'patient' AND ((NOT v_active AND NOT v_consent) OR v_active) THEN
        SELECT EXISTS (
            SELECT 1 FROM core.consent_records c
             WHERE c.staff_id = v_id
               AND c.consent_type = 'staff_onboarding'
               AND c.status = 'signed'
        ) INTO v_signed;

        IF NOT v_active AND NOT v_consent AND v_signed THEN
            PERFORM set_config('app.current_cognito_sub', '', true);
            PERFORM set_config('app.current_user_id', '', true);
            PERFORM set_config('app.current_user_role', 'system', true);
            UPDATE core.profiles SET is_active = TRUE, consent_signed = TRUE WHERE id = v_id;
            PERFORM set_config('app.current_cognito_sub', p_cognito_sub, true);
            PERFORM set_config('app.current_user_id', v_id::text, true);
            PERFORM set_config('app.current_user_role', v_role, true);
            v_active := TRUE;
            v_consent := TRUE;
        ELSIF v_active AND NOT v_signed THEN
            PERFORM set_config('app.current_cognito_sub', '', true);
            PERFORM set_config('app.current_user_id', '', true);
            PERFORM set_config('app.current_user_role', 'system', true);
            UPDATE core.profiles SET is_active = FALSE, consent_signed = FALSE WHERE id = v_id;
            PERFORM set_config('app.current_cognito_sub', p_cognito_sub, true);
            PERFORM set_config('app.current_user_id', v_id::text, true);
            PERFORM set_config('app.current_user_role', v_role, true);
            v_active := FALSE;
            v_consent := FALSE;
        END IF;
    END IF;

    -- Tenant scope. region comes from admins only (same as before).
    IF v_role IN ('super_admin', 'regional_admin', 'clinic_admin') THEN
        SELECT a.region_id, a.clinic_id INTO v_region, v_clinic
          FROM core.admins a WHERE a.profile_id = v_id;
    ELSIF v_role IN ('doctor', 'clinical_assistant', 'receptionist') THEN
        SELECT s.clinic_id INTO v_clinic
          FROM core.clinic_staff_assignments s
         WHERE s.profile_id = v_id AND s.is_active = TRUE
         LIMIT 1;
    ELSIF v_role = 'patient' THEN
        SELECT pt.primary_clinic_id INTO v_clinic
          FROM core.patients pt WHERE pt.profile_id = v_id;
    END IF;

    clinic_closed := FALSE;
    region_inactive := FALSE;

    -- Closed clinic / inactive region lockout. super_admin and regional_admin
    -- are exempt so they can still log in to reopen / reactivate. Each GUC is
    -- set to the row being checked first: rls_clinics_select / rls_regions_select
    -- hide closed clinics / inactive regions except the caller's own.
    IF v_role NOT IN ('super_admin', 'regional_admin') AND v_clinic IS NOT NULL THEN
        PERFORM set_config('app.current_clinic_id', v_clinic::text, true);
        SELECT c.status, c.region_id INTO v_status, v_clinic_region
          FROM core.clinics c WHERE c.clinic_id = v_clinic;
        IF FOUND THEN
            -- NULL status is "not closed" (Python: None == "closed" -> False).
            clinic_closed := COALESCE(v_status = 'closed', FALSE);
            IF NOT clinic_closed AND v_clinic_region IS NOT NULL THEN
                PERFORM set_config('app.current_region_id', v_clinic_region::text, true);
                SELECT r.is_active INTO v_region_active
                  FROM core.regions r WHERE r.region_id = v_clinic_region;
                region_inactive := FOUND AND v_region_active IS NOT TRUE;
            END IF;
        END IF;
    END IF;

    profile_id := v_id;
    user_role := v_role;
    active := v_active;
    consent := v_consent;
    scope_clinic_id := v_clinic;
    scope_region_id := v_region;
    RETURN NEXT;
END;
$function$;
