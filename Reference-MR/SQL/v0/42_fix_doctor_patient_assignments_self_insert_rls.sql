-- ============================================================
-- 42_fix_doctor_patient_assignments_self_insert_rls.sql
--
-- rls_dpa_insert never allowed the patient role — but
-- patients/service.py::_complete_registration (triggered by the patient's
-- own final PRS submission, Master Doc Flow M auto-allocation) creates the
-- doctor_patient_assignments row itself, with patient_id = the caller's own
-- profile id. rls_dpa_select already has a `patient_id = rls_user_id()`
-- fallback (added for a patient to see their own assignment) — the INSERT
-- policy never got the matching one. Same bootstrap-gap pattern as
-- everything upstream of this in the registration wizard (SQL/37-41).
--
-- Fix: same ownership clause, added to the INSERT check.
-- ============================================================

DROP POLICY IF EXISTS rls_dpa_insert ON doctor_patient_assignments;
CREATE POLICY rls_dpa_insert ON doctor_patient_assignments FOR INSERT
WITH CHECK (
    rls_user_role() = ANY (ARRAY['super_admin', 'clinic_admin', 'receptionist'])
    OR patient_id = rls_user_id()
);
