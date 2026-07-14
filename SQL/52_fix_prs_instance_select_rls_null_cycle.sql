-- Fix: doctor's POST /prs-assessment-instances still failed after 50 with
-- "new row violates row-level security policy" — the INSERT itself passes,
-- but the repository's RETURNING * applies the SELECT policy to the new
-- row, and rls_pai_select's staff clause only matches rows whose cycle_id
-- belongs to the caller's clinic. Instances created outside a treatment
-- cycle (cycle_id IS NULL — every registration-stage and current
-- main_clinical instance) were invisible to staff, which both killed the
-- RETURNING and hid patients' PRS history from their own clinic's staff.
--
-- Fix: scope staff visibility by the patient's clinic membership (same
-- subquery pattern as 43/49), keeping the cycle clause for cross-checks.

DROP POLICY IF EXISTS rls_pai_select ON prs_assessment_instances;

CREATE POLICY rls_pai_select ON prs_assessment_instances FOR SELECT
USING (
    rls_user_role() = ANY (ARRAY['super_admin', 'regional_admin'])
    OR patient_id = rls_user_id()
    OR (
        rls_user_role() = ANY (ARRAY['clinic_admin', 'doctor', 'clinical_assistant', 'receptionist'])
        AND (
            patient_id IN (SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id())
            OR cycle_id IN (SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id())
        )
    )
);
