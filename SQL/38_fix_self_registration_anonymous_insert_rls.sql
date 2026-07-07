-- ============================================================
-- 38_fix_self_registration_anonymous_insert_rls.sql
--
-- Public self-registration (POST /auth/register, no auth — the only
-- anonymous-write path in this schema) creates rows in profiles, patients,
-- and consent_records in one transaction. None of their INSERT policies
-- had a fallback for a caller with NO RLS context at all (anonymous — no
-- profile exists yet, so nothing to derive a role from):
--   - rls_profiles_insert required rls_user_role() to already be one of
--     super_admin/regional_admin/clinic_admin/receptionist/patient —
--     impossible for the very first insert that CREATES the patient.
--   - rls_patients_insert required a staff role — same problem for the
--     patients row that immediately follows.
--   - rls_cr_insert required a staff role — same problem for the
--     onboarding consent_records row created right after.
-- Each blocked silently with InsufficientPrivilegeError, surfaced as a
-- generic 500 once the RLS anonymous-read gap (SQL/37) was fixed and the
-- registration flow could get this far for the first time under real RLS.
--
-- Fix: an anonymous caller may only ever reach these three inserts through
-- this one hardcoded backend code path (PatientService.register, self_
-- registered=True) — no other anonymous-reachable endpoint touches these
-- tables (RLS is defense-in-depth here per ADR-003, not the primary gate;
-- the app layer is). Add a narrow anonymous-allow: profiles only when the
-- new row's own role='patient' (can't self-register as anything else),
-- patients/consent_records unconditionally for anonymous (they carry no
-- role column to check against).
-- ============================================================

DROP POLICY IF EXISTS rls_profiles_insert ON profiles;
CREATE POLICY rls_profiles_insert ON profiles FOR INSERT
WITH CHECK (
    rls_user_role() = ANY (ARRAY['super_admin', 'regional_admin', 'clinic_admin', 'receptionist', 'patient'])
    OR (rls_user_role() IS NULL AND role = 'patient')
);

DROP POLICY IF EXISTS rls_patients_insert ON patients;
CREATE POLICY rls_patients_insert ON patients FOR INSERT
WITH CHECK (
    rls_user_role() = ANY (ARRAY['super_admin', 'clinic_admin', 'receptionist'])
    OR rls_user_role() IS NULL
);

DROP POLICY IF EXISTS rls_cr_insert ON consent_records;
CREATE POLICY rls_cr_insert ON consent_records FOR INSERT
WITH CHECK (
    rls_user_role() = ANY (ARRAY['super_admin', 'regional_admin', 'clinic_admin', 'receptionist'])
    OR rls_user_role() IS NULL
);
