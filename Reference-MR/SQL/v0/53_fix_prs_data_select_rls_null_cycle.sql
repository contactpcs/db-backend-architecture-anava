-- Companion to 52: prs_responses / prs_scale_results / prs_final_results
-- SELECT policies scoped staff access by treatment-cycle only, so rows
-- belonging to NULL-cycle instances (all registration-stage and current
-- main_clinical ones) were invisible to clinic staff. Their repositories
-- write with RETURNING *, which applies the SELECT policy to the new row —
-- so a doctor saving a patient's answer died with an RLS violation on
-- prs_responses, and results reads/writes had the same hole. Staff clause
-- now mirrors 52: visible when the instance's patient belongs to the
-- caller's clinic (cycle clause kept).

DROP POLICY IF EXISTS rls_prs_resp_select ON prs_responses;

CREATE POLICY rls_prs_resp_select ON prs_responses FOR SELECT
USING (
    rls_user_role() = ANY (ARRAY['super_admin', 'regional_admin'])
    OR instance_id IN (SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id())
    OR (
        rls_user_role() = ANY (ARRAY['clinic_admin', 'doctor', 'clinical_assistant', 'receptionist'])
        AND instance_id IN (
            SELECT instance_id FROM prs_assessment_instances
            WHERE patient_id IN (SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id())
               OR cycle_id IN (SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id())
        )
    )
);

DROP POLICY IF EXISTS rls_psr_select ON prs_scale_results;

CREATE POLICY rls_psr_select ON prs_scale_results FOR SELECT
USING (
    rls_user_role() = ANY (ARRAY['super_admin', 'regional_admin'])
    OR instance_id IN (SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id())
    OR (
        rls_user_role() = ANY (ARRAY['clinic_admin', 'doctor', 'clinical_assistant', 'receptionist'])
        AND instance_id IN (
            SELECT instance_id FROM prs_assessment_instances
            WHERE patient_id IN (SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id())
               OR cycle_id IN (SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id())
        )
    )
);

DROP POLICY IF EXISTS rls_pfr_select ON prs_final_results;

CREATE POLICY rls_pfr_select ON prs_final_results FOR SELECT
USING (
    rls_user_role() = ANY (ARRAY['super_admin', 'regional_admin'])
    OR instance_id IN (SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id())
    OR (
        rls_user_role() = ANY (ARRAY['clinic_admin', 'doctor', 'clinical_assistant', 'receptionist'])
        AND instance_id IN (
            SELECT instance_id FROM prs_assessment_instances
            WHERE patient_id IN (SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id())
               OR cycle_id IN (SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id())
        )
    )
);
