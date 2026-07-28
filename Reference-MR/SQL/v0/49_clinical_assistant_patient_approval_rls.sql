-- Clinical assistants get the same patient-approval capability as
-- receptionists (PATCH /patients/{id}/approval). Two policies gate that
-- flow and both listed receptionist but not clinical_assistant:
--
-- 1. rls_patients_update — flips patients.approval_status. Without the
--    role here the UPDATE silently matches 0 rows and approval never
--    persists.
-- 2. rls_profiles_update — decide_approval's paired
--    `UPDATE profiles SET is_active = TRUE` (see 43's receptionist fix).
--    Same clinic-membership subquery, now shared by both roles.
--
-- Endpoint role-checks (require_role) plus assert_clinic_scope remain the
-- real authorization boundary; this only stops RLS from silently no-op'ing
-- the writes.

DROP POLICY IF EXISTS rls_patients_update ON patients;

CREATE POLICY rls_patients_update ON patients FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'receptionist', 'clinical_assistant')
    OR profile_id = rls_user_id()
);

DROP POLICY IF EXISTS rls_profiles_update ON profiles;

CREATE POLICY rls_profiles_update ON profiles FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin')
    OR id = rls_user_id()
    OR (
        rls_user_role() IN ('receptionist', 'clinical_assistant')
        AND id IN (SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id())
    )
);
