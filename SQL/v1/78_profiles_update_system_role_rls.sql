-- 78_profiles_update_system_role_rls.sql
--
-- THE PROBLEM
--
-- middleware.py's AuthContextMiddleware has two "self-heal" write-backs on
-- core.profiles.is_active/consent_signed — one healing a bricked account
-- whose flags are stuck FALSE despite a real signed consent existing, and
-- one (added alongside this migration) catching the opposite: flags stuck
-- TRUE with no real consent_records behind them at all (found live — 6
-- staff/admin accounts had been seeded directly with both flags set to
-- TRUE, bypassing the sign flow entirely; the super-admin consent view
-- correctly reported "not signed" for every one of them).
--
-- Both write-backs run on `engine` (the app's own anava_app connection,
-- fully RLS-subject) via a fresh engine.begin() with no app.current_user_role
-- GUC set on it at all. rls_profiles_update has no 'system' branch, so
-- rls_user_role() reads NULL, no USING clause matches, and the UPDATE
-- silently affects zero rows — not an error, just a no-op. The in-memory
-- RequestContext returned for that one request was still correct (plain
-- Python assignment, unrelated to the DB write), so gating never broke, but
-- the DB flag itself never actually got corrected — meaning the exact same
-- self-heal query re-runs on every single subsequent request, forever,
-- for an account this was supposed to have already fixed once.
--
-- THE FIX
--
-- Same remedy this codebase already applies elsewhere for an unattended
-- system write against a forced-RLS table (25_webhook_system_role_rls.sql,
-- 31 §6 for appointments, 44 for appointment_audit_logs): admit a 'system'
-- role explicitly. middleware.py sets app.current_user_role='system' on
-- these connections now, matching every other worker/webhook that does the
-- same unattended write.
--
-- APPLY ORDER: after 77. Independent of everything else.

BEGIN;

DROP POLICY IF EXISTS "rls_profiles_update" ON core."profiles";
CREATE POLICY "rls_profiles_update" ON core."profiles" FOR UPDATE TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text, 'system'::text]))
        OR (id = rls_user_id())
        OR ((rls_user_role() = ANY (ARRAY['receptionist'::text, 'clinical_assistant'::text]))
            AND (id IN (SELECT patients.profile_id FROM core.patients WHERE (patients.primary_clinic_id = rls_clinic_id()))))
    );

COMMIT;
