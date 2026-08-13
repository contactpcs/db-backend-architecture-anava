-- 35_ops_rls_cleanup.sql
--
-- Removes a latent trap on the two migration-bookkeeping tables.
--
-- APPLY ORDER: anywhere after 16_rls_enable.sql. Independent of 30-34 — it
-- touches nothing they touch and can be applied before or after them.
--
--
-- THE PROBLEM
--
-- 16_rls_enable.sql applies ENABLE + FORCE ROW LEVEL SECURITY to every table in
-- the database, ops included. 17_rls_policies.sql then writes zero policies for
-- the ops schema. 26_rls_lockout_fixes.sql later caught four tables stuck in
-- that state and gave them policies — but only the four the application itself
-- touches, which is how it found them. ops.alembic_version and
-- ops.schema_migrations were missed, because nothing in the application ever
-- reads them.
--
-- Under FORCE RLS with no policy, a table returns zero rows and accepts zero
-- writes — silently, not as an error. Confirmed live on Anava_App_v1
-- (2026-08-13): these two are the only tables left in that state.
--
-- Why it has not broken anything yet: Alembic connects with the `postgres`
-- credential (MIGRATION_DATABASE_URL), which empirically bypasses RLS on this
-- instance despite rolsuper=false and rolbypassrls=false — an undiagnosed
-- RDS/PostgreSQL 18 behaviour recorded separately. Migrations therefore work.
--
-- Why that is not good enough: the whole safety of migration bookkeeping now
-- rests on an undiagnosed quirk continuing to hold. If `postgres` ever stops
-- bypassing RLS, Alembic reads an EMPTY alembic_version, concludes the database
-- has never been migrated, and attempts to replay every revision from 0001
-- against a fully-built schema. That failure is loud but the damage is not
-- subtle, and it would land during a deploy.
--
--
-- THE FIX, AND WHY IT IS DISABLE RATHER THAN A POLICY
--
-- Row-level security exists to scope rows to a tenant, a clinic, or a patient.
-- ops.alembic_version holds a single version string. ops.schema_migrations holds
-- a list of migration filenames. Neither contains tenant data, patient data, or
-- anything a policy could meaningfully scope — there is no "your row" versus
-- "someone else's row" to distinguish.
--
-- Writing a permissive policy would satisfy the letter of "every table has RLS"
-- while protecting nothing, and would leave the next reader wondering what the
-- policy is for. Turning RLS off states the truth: these are infrastructure
-- tables, not tenant data, and access to them is governed by which credential
-- can reach the database at all.
--
-- Both statements are idempotent — running this file twice is a no-op.

ALTER TABLE ops."alembic_version"   DISABLE ROW LEVEL SECURITY;
ALTER TABLE ops."schema_migrations" DISABLE ROW LEVEL SECURITY;

COMMENT ON TABLE ops."alembic_version"   IS 'Alembic revision pointer. Infrastructure, not tenant data — RLS deliberately disabled (35_ops_rls_cleanup.sql); access is governed by credential, not policy.';
COMMENT ON TABLE ops."schema_migrations" IS 'Applied-migration ledger. Infrastructure, not tenant data — RLS deliberately disabled (35_ops_rls_cleanup.sql); access is governed by credential, not policy.';

-- ops.outbox_events is deliberately NOT touched. It carries event payloads that
-- can reference patient and clinic identifiers, so it is genuine tenant-adjacent
-- data and keeps the policies 26_rls_lockout_fixes.sql gave it.


-- ---------------------------------------------------------------------------
-- VERIFY (expect 0 rows)
-- ---------------------------------------------------------------------------
-- SELECT n.nspname || '.' || c.relname
-- FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
-- WHERE c.relrowsecurity AND c.relkind = 'r'
--   AND n.nspname IN ('core','reference','compliance','ops')
--   AND NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid);
