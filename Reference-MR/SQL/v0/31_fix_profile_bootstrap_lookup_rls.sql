-- ============================================================
-- 31_fix_profile_bootstrap_lookup_rls.sql
--
-- Bug found migrating to RDS and turning on real RLS enforcement for the
-- first time (the local dev DB role bypasses RLS entirely, so this was
-- never actually exercised before): core/middleware.py's own "who am I"
-- lookup — SELECT ... FROM profiles WHERE cognito_sub = :sub, run right
-- after JWT verification, BEFORE any app.current_user_id/role context can
-- be set (this query is what DETERMINES that context) — was rejected by
-- rls_profiles_select, which only allows matching by an already-known role
-- or id. Every authenticated request hit PROFILE_NOT_FOUND, even for a
-- profile that genuinely exists, once RLS was actually enforced.
--
-- Fix: allow self-lookup by cognito_sub too, the same "you can always see
-- your own row" principle already granted via `id = rls_user_id()` — just
-- keyed on the pre-authentication identifier instead of the post-lookup
-- UUID. core/middleware.py sets app.current_cognito_sub (derived from the
-- already-verified JWT, so this isn't trusting unverified input) right
-- before this specific query.
--
-- Same function also runs 3 follow-up scope queries (admins/
-- clinic_staff_assignments/patients, filtered WHERE profile_id = :pid) to
-- resolve clinic_id/region_id — those hit the identical problem.
-- admins/patients already have a `profile_id = rls_user_id()` self-clause,
-- which now works once core/middleware.py sets app.current_user_id/role
-- immediately after resolving them (see middleware.py — must happen before
-- these follow-up queries, on the same connection). clinic_staff_assignments
-- had NO self-clause at all — added below, a real gap (a CA/doctor/
-- receptionist couldn't see their own assignment row via RLS even outside
-- the bootstrap case), not just a bootstrap-specific patch.
-- ============================================================

CREATE OR REPLACE FUNCTION rls_cognito_sub() RETURNS TEXT AS $$
    SELECT NULLIF(current_setting('app.current_cognito_sub', TRUE), '');
$$ LANGUAGE sql STABLE;

DROP POLICY IF EXISTS rls_profiles_select ON profiles;
CREATE POLICY rls_profiles_select ON profiles FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR rls_user_role() = 'regional_admin'
    OR id = rls_user_id()
    OR cognito_sub = rls_cognito_sub()
    OR (
        rls_user_role() IN ('clinic_admin', 'doctor', 'clinical_assistant', 'receptionist')
        AND id IN (
            SELECT profile_id FROM clinic_staff_assignments
            WHERE clinic_id = rls_clinic_id() AND is_active = TRUE
            UNION
            SELECT profile_id FROM patients
            WHERE primary_clinic_id = rls_clinic_id()
        )
    )
);

DROP POLICY IF EXISTS rls_csa_select ON clinic_staff_assignments;
CREATE POLICY rls_csa_select ON clinic_staff_assignments FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND clinic_id IN (
        SELECT clinic_id FROM clinics WHERE region_id = rls_region_id()
    ))
    OR clinic_id = rls_clinic_id()
    OR profile_id = rls_user_id()
);
