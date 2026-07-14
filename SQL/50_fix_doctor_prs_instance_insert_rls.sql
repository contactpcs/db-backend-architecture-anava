-- Fix: doctor starting a PRS assessment on a patient's behalf
-- (POST /prs-assessment-instances, initiated_by='doctor_on_behalf') dies
-- with "new row violates row-level security policy" — rls_pai_insert
-- lists super_admin/clinic_admin/clinical_assistant/receptionist/self but
-- never doctor, even though rls_pai_update (finalizing scales on the same
-- flow) already includes doctor. Endpoint role-checks (require_role) stay
-- the authorization boundary.

DROP POLICY IF EXISTS rls_pai_insert ON prs_assessment_instances;

CREATE POLICY rls_pai_insert ON prs_assessment_instances FOR INSERT
WITH CHECK (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'clinical_assistant', 'receptionist', 'doctor')
    OR patient_id = rls_user_id()
);
