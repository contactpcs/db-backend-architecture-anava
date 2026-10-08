-- anava_app is the application's login role. It may already exist (CI creates it before
-- applying these files; so does scripts/apply_sql_v1.py), so this only creates it when
-- missing. No password is set here: a password in this file would live in git. Set it
-- once with:  ALTER ROLE anava_app PASSWORD '...';  (apply_sql_v1.py does that from
-- DATABASE_URL in backend/.env).
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anava_app') THEN
        CREATE ROLE anava_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END
$$;



-- Generated from live production schema introspection (2026-07-20). Do not hand-edit column/RLS/trigger/function bodies — regenerate from source instead.

-- anava_app: full DML on core/compliance/ops, read on reference/analytics (RLS-scoped throughout)
GRANT USAGE ON SCHEMA core, reference, compliance, analytics, ops TO anava_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO anava_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA compliance TO anava_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ops TO anava_app;
GRANT SELECT ON ALL TABLES IN SCHEMA reference TO anava_app;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO anava_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core, ops TO anava_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA core, ops TO anava_app;

-- anava_readonly: SELECT-only everywhere. No RLS bypass (not superuser, not BYPASSRLS).
GRANT USAGE ON SCHEMA core, reference, compliance, analytics, ops TO anava_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA core, reference, compliance, analytics, ops TO anava_readonly;

-- anava_compliance: SELECT/UPDATE on compliance schema only.
GRANT USAGE ON SCHEMA compliance TO anava_compliance;
GRANT SELECT, UPDATE ON ALL TABLES IN SCHEMA compliance TO anava_compliance;

-- Default privileges so future tables in each schema inherit the same grants automatically.
ALTER DEFAULT PRIVILEGES IN SCHEMA core, compliance, ops GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO anava_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA reference, analytics GRANT SELECT ON TABLES TO anava_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA core, reference, compliance, analytics, ops GRANT SELECT ON TABLES TO anava_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA compliance GRANT SELECT, UPDATE ON TABLES TO anava_compliance;
