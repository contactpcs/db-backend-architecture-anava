-- Fix: doctor (or CA/clinic_admin) administering a PRS on a patient's
-- behalf crashes when the LAST scale is finalized — the scoring trigger
-- runs with the caller's privileges (same mechanism 40 fixed for patient
-- self-submit) and inserts/updates prs_final_results, whose policies allow
-- only super_admin or the patient themself. Align the role list with
-- rls_psr_insert (prs_scale_results), which the same flow already writes.

DROP POLICY IF EXISTS rls_pfr_insert ON prs_final_results;

CREATE POLICY rls_pfr_insert ON prs_final_results FOR INSERT
WITH CHECK (
    rls_user_role() = ANY (ARRAY['super_admin', 'clinic_admin', 'clinical_assistant', 'doctor'])
    OR instance_id IN (SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id())
);

DROP POLICY IF EXISTS rls_pfr_update ON prs_final_results;

CREATE POLICY rls_pfr_update ON prs_final_results FOR UPDATE
USING (
    rls_user_role() = ANY (ARRAY['super_admin', 'clinic_admin', 'clinical_assistant', 'doctor'])
    OR instance_id IN (SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id())
);
