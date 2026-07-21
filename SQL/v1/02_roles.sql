-- Generated from live production schema introspection (2026-07-20). Do not hand-edit column/RLS/trigger/function bodies — regenerate from source instead.

-- anava_app already exists cluster-wide (used by the current production DB) — reused, not recreated.
-- anava_migrate role is not created here: the 'postgres' role already serves this purpose today
-- (it owns every table in the current schema) and continues to do so for anava_v1 — no redundant role.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anava_readonly') THEN
        CREATE ROLE anava_readonly LOGIN PASSWORD 'CHANGE_ME_BEFORE_USE';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anava_compliance') THEN
        CREATE ROLE anava_compliance LOGIN PASSWORD 'CHANGE_ME_BEFORE_USE';
    END IF;
END
$$;

COMMENT ON ROLE anava_readonly IS 'SELECT-only across all schemas. For reporting/BI. No RLS bypass.';
COMMENT ON ROLE anava_compliance IS 'SELECT/UPDATE on compliance schema only. For erasure/portability/grievance tooling.';
-- Passwords above are placeholders — rotate via ALTER ROLE ... PASSWORD before granting these roles to anyone.
