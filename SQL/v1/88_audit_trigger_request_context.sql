-- 88_audit_trigger_request_context.sql
--
-- ops.fn_audit_trigger() (14_functions.sql) has always written only
-- (table_name, operation, record_id, old_data, new_data, changed_by) —
-- clinic_id/ip_address/request_id are real columns on compliance.audit_logs
-- (07_tables_compliance.sql) that the function simply never touched. Found
-- live: 100% NULL on all 3,534 existing rows (Data Capture Audit,
-- 2026-09-21, DC-04).
--
-- clinic_id had a value to read all along — app.current_clinic_id is set on
-- every request by core/db.py::_apply_rls_context, this function just never
-- read it. request_id/ip_address needed the app side built first (this
-- migration's own companion change): AuthContextMiddleware now resolves
-- both once per request and RequestContext carries them through to two new
-- session vars, app.request_id / app.client_ip, set the same
-- SET LOCAL way as app.current_clinic_id already is.
--
-- APPLY ORDER: after 87. No new columns — audit_logs already had all three.

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
    v_clinic_id   UUID;
    v_request_id  TEXT;
    v_client_ip   INET;
BEGIN
    -- Read actor + request context from session state (set by FastAPI
    -- middleware) — a bare script or worker (no HTTP request) leaves every
    -- one of these current_setting() calls unset, not just v_user_id, so
    -- each falls back to NULL rather than failing the whole write.
    -- request_id needs no exception guard: it's TEXT, so a bad value can
    -- only ever come back NULL from NULLIF, never throw on cast.
    BEGIN
        v_user_id := NULLIF(current_setting('app.current_user_id', TRUE), '')::UUID;
    EXCEPTION WHEN others THEN
        v_user_id := NULL;
    END;

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

    INSERT INTO audit_logs (table_name, operation, record_id, old_data, new_data, changed_by, clinic_id, request_id, ip_address)
    VALUES (TG_TABLE_NAME, TG_OP, v_record_id, v_old_data, v_new_data, v_user_id, v_clinic_id, v_request_id, v_client_ip);

    RETURN NULL;  -- AFTER trigger; return value ignored
END;
$function$;
