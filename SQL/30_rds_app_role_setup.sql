-- ============================================================
-- 30_rds_app_role_setup.sql
--
-- ONE-TIME infra setup for a real RDS environment — NOT part of the regular
-- schema build (00_run_all.sql does not include this file, and local Docker
-- dev doesn't need it). Run this ONCE per RDS environment, connected as the
-- RDS master user, AFTER the schema (00_run_all.sql / alembic) has been
-- applied.
--
-- Why: the local dev DB role is effectively a superuser and bypasses Row-
-- Level Security entirely (rolbypassrls=TRUE), so every policy in
-- 15_rls_policies.sql has been dead code so far — real enforcement has been
-- app-layer checks only (core/scoping.py). This creates a real non-
-- superuser role for the app to connect as, so RLS actually applies as
-- defense-in-depth like the schema always intended (see FORCE ROW LEVEL
-- SECURITY on every table — that clause only matters for non-owner roles
-- like this one; it's a no-op for the table owner/master user).
--
-- The RDS master user is a member of `rds_superuser`, which AWS explicitly
-- does NOT grant BYPASSRLS to. Still, using a distinct, narrowly-privileged
-- role for the app (rather than the master user) is correct practice
-- regardless — the master user should be reserved for migrations/admin.
--
-- Password is deliberately NOT set here — run this file first (creates the
-- role with no way to log in), then set the password yourself in a separate
-- statement/session so it's never written to a file on disk:
--   ALTER ROLE anava_app WITH PASSWORD '<your chosen password>';
-- ============================================================

CREATE ROLE anava_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

-- GRANT ... ON DATABASE needs a literal name, not an expression — this DO
-- block grants CONNECT on whichever database this script is actually run
-- against, so nothing here needs manual editing per environment.
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO anava_app', current_database());
END $$;

GRANT USAGE ON SCHEMA public TO anava_app;

-- Present + future tables. The app does no DDL (that's alembic's job, run
-- as the master user), so no CREATE/ALTER/DROP grants here — INSERT/SELECT/
-- UPDATE/DELETE is everything a request handler ever needs.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO anava_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO anava_app;

-- Sequences: mrn_seq (SQL/04_patient_tables.sql) is nextval()'d inside
-- fn_generate_mrn(), which is a plain (SECURITY INVOKER) trigger function —
-- it runs as whichever role performs the INSERT, i.e. anava_app at runtime,
-- so it needs USAGE directly, not just via the owning function.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anava_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO anava_app;

-- fn_audit_trigger() is SECURITY DEFINER (runs as its owner, the master
-- user) specifically so anava_app never needs direct INSERT on audit_logs
-- for the trigger to fire — this GRANT is only for the audit module's own
-- read-side query API (core/audit reads audit_logs directly).
