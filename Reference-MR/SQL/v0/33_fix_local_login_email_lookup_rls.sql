-- ============================================================
-- 33_fix_local_login_email_lookup_rls.sql
--
-- Same class of bug as SQL/31 (auth middleware's cognito_sub self-lookup)
-- and SQL/32 (public clinics endpoint) — another RLS-enforcement gap the
-- local dev superuser-bypass role never exercised. auth/router.py's
-- local_login endpoint, when called with email instead of cognito_sub
-- (the frontend's actual login form always does this — cognito_sub is only
-- used by internal test/curl calls), runs SELECT cognito_sub FROM profiles
-- WHERE email = :email with zero RLS context (this query is what
-- determines the identity that context would come from — same chicken-
-- and-egg shape). Every real login via the frontend got "No profile found
-- for this email" even for a genuinely existing profile, once RLS was
-- actually enforced.
--
-- Fix: same self-lookup principle as rls_cognito_sub() — allow matching by
-- email too. auth/router.py sets app.current_email right before this one
-- query, on the same connection/transaction.
-- ============================================================

CREATE OR REPLACE FUNCTION rls_email() RETURNS TEXT AS $$
    SELECT NULLIF(current_setting('app.current_email', TRUE), '');
$$ LANGUAGE sql STABLE;

DROP POLICY IF EXISTS rls_profiles_select ON profiles;
CREATE POLICY rls_profiles_select ON profiles FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR rls_user_role() = 'regional_admin'
    OR id = rls_user_id()
    OR cognito_sub = rls_cognito_sub()
    OR email = rls_email()
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
