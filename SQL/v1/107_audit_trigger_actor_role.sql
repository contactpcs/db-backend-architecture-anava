-- 107_audit_trigger_actor_role.sql
--
-- ops.fn_audit_trigger() (last replaced by 88) recorded WHO changed a row
-- (changed_by) but not in what capacity. With a clinical assistant now
-- amending protocols alongside doctors (106), "who" alone no longer answers
-- "did a doctor or a clinical assistant do this" without a join to
-- profiles, and that join reports the person's role today, not the role they
-- held when the row changed. This writes app.current_user_role, the same
-- session variable every RLS policy reads, into audit_logs.changed_by_role
-- (column added by 106).
--
-- Identical to 88 apart from v_user_role. One statement, executed whole.
--
-- APPLY ORDER: after 106.

CREATE OR REPLACE FUNCTION ops.fn_audit_trigger()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
DECLARE
    v_pk_col      TEXT := TG_ARGV[0];
    v_record_id   TEXT;
    v_old_data    JSONB;
    v_new_data    JSONB;
    v_user_id     UUID;
    v_user_role   TEXT;
    v_clinic_id   UUID;
    v_request_id  TEXT;
    v_client_ip   INET;
BEGIN
    -- Read actor + request context from session state (set by FastAPI
    -- middleware) — a bare script or worker (no HTTP request) leaves every
    -- one of these current_setting() calls unset, not just v_user_id, so
    -- each falls back to NULL rather than failing the whole write.
    -- request_id / user_role need no exception guard: they're TEXT, so a bad
    -- value can only ever come back NULL from NULLIF, never throw on cast.
    BEGIN
        v_user_id := NULLIF(current_setting('app.current_user_id', TRUE), '')::UUID;
    EXCEPTION WHEN others THEN
        v_user_id := NULL;
    END;

    v_user_role := NULLIF(current_setting('app.current_user_role', TRUE), '');

    BEGIN
        v_clinic_id := NULLIF(current_setting('app.current_clinic_id', TRUE), '')::UUID;
    EXCEPTION WHEN others THEN
        v_clinic_id := NULL;
    END;

    v_request_id := NULLIF(current_setting('app.request_id', TRUE), '');

    BEGIN
        v_client_ip := NULLIF(current_setting('app.client_ip', TRUE), '')::INET;
    EXCEPTION WHEN others THEN
        v_client_ip := NULL;
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

    INSERT INTO audit_logs (table_name, operation, record_id, old_data, new_data, changed_by, changed_by_role, clinic_id, request_id, ip_address)
    VALUES (TG_TABLE_NAME, TG_OP, v_record_id, v_old_data, v_new_data, v_user_id, v_user_role, v_clinic_id, v_request_id, v_client_ip);

    RETURN NULL;  -- AFTER trigger; return value ignored
END;
$function$;
