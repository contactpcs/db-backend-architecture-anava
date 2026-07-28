-- ============================================================
-- 41_fix_profiles_patient_sees_own_clinic_staff_rls.sql
--
-- rls_profiles_select let staff (clinic_admin/doctor/clinical_assistant/
-- receptionist) see profiles of patients and coworkers at their own
-- clinic, but had no reverse clause letting a patient see the PROFILE rows
-- of staff at their own clinic. patients/service.py::_complete_registration
-- -> DoctorService.pick_least_loaded -> DoctorRepository.list() joins
-- doctors to profiles (needs first_name/last_name/email/is_active) — under
-- a patient's own RLS context, that join's profiles side was invisible, so
-- every doctor row silently dropped out of the join. "No available doctor
-- at this clinic to auto-allocate" for a clinic that genuinely has one.
-- Same bootstrap-gap pattern as the rest of this session, just the first
-- time a patient-triggered query needed to read a STAFF profile rather than
-- write their own record.
--
-- Fix: symmetric clause — a patient can see profiles of staff assigned
-- (active) at their own clinic.
-- ============================================================

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
    OR (
        rls_user_role() = 'patient'
        AND id IN (
            SELECT profile_id FROM clinic_staff_assignments
            WHERE clinic_id = rls_clinic_id() AND is_active = TRUE
        )
    )
);
