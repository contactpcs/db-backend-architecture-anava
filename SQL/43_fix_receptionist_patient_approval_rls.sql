-- Fix: receptionist approving a self-registered patient's registration
-- (PATCH /patients/{id}/approval, decision=approved) flips approval_status
-- fine (patients table has its own policy already covering receptionist),
-- but the paired `UPDATE profiles SET is_active = TRUE` (service.py
-- decide_approval, and the analogous staff-registered auto-activate path)
-- silently affects 0 rows under real RLS — rls_profiles_update's USING
-- clause only allows super_admin/regional_admin/clinic_admin or self, never
-- receptionist. No exception is raised (a plain UPDATE with no matching
-- rows just does nothing), so the patient stays is_active=false forever
-- and keeps landing back on the "awaiting approval" screen even after
-- being approved.
--
-- Fix: add a receptionist clause scoped to patients at their own clinic —
-- same clinic-membership subquery already used by rls_profiles_select for
-- the exact same receptionist-sees-own-clinic-patients relationship.

DROP POLICY IF EXISTS rls_profiles_update ON profiles;

CREATE POLICY rls_profiles_update ON profiles FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin')
    OR id = rls_user_id()
    OR (
        rls_user_role() = 'receptionist'
        AND id IN (SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id())
    )
);
