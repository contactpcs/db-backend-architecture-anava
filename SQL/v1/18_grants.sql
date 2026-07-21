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
