-- ============================================================
-- 39_fix_patient_scale_assignments_self_select_rls.sql
--
-- rls_psa_insert never included 'patient' in its allowed roles — but
-- patients/service.py::PatientService.select_disease (called by the patient
-- themselves during registration, both self- and staff-registered) calls
-- PatientScaleAssignmentService.auto_assign_for_disease right after, which
-- self-assigns scales (assigned_by = the patient's own profile id, Master
-- Doc Section 9.3). This was apparently never exercised end-to-end under
-- real RLS before — local dev's superuser-bypass role masked it, same as
-- everything else this session — so no patient, self- or staff-registered,
-- could ever get past disease selection.
--
-- Fix: allow 'patient' to INSERT here too (self-assignment only in
-- practice — assigned_by is always the caller's own id from the service
-- layer, RLS is defense-in-depth per ADR-003, not the primary gate).
-- ============================================================

DROP POLICY IF EXISTS rls_psa_insert ON patient_scale_assignments;
CREATE POLICY rls_psa_insert ON patient_scale_assignments FOR INSERT
WITH CHECK (
    rls_user_role() = ANY (ARRAY['super_admin', 'clinic_admin', 'doctor', 'clinical_assistant', 'patient'])
);
