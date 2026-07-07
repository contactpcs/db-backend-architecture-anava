-- ============================================================
-- 40_fix_prs_scale_and_final_results_self_submit_rls.sql
--
-- prs_scale_results and prs_final_results never had an ownership-based
-- fallback for the patient submitting their own general PRS assessment —
-- unlike prs_responses (rls_prs_resp_insert), which already correctly
-- allows "instance_id IN (SELECT ... WHERE patient_id = rls_user_id())".
-- prs/service.py::PrsAssessmentService.submit_responses (called by the
-- patient themselves) writes to both: _finalize_scale upserts
-- prs_scale_results on every scale completion, and the recalculate_final_
-- result trigger (SQL/07_prs_tables.sql, NOT SECURITY DEFINER — runs as the
-- calling anava_app role, same RLS context as the patient's own session)
-- upserts prs_final_results once the last scale for an instance completes.
-- Neither could ever succeed for a patient under real RLS — "Failed to
-- submit" on general PRS, same bootstrap-gap pattern as disease selection
-- (SQL/39) and self-registration (SQL/37-38).
--
-- Fix: same ownership clause prs_responses already has, added to both
-- tables' INSERT and UPDATE policies (the upsert can take either path).
-- ============================================================

DROP POLICY IF EXISTS rls_psr_insert ON prs_scale_results;
CREATE POLICY rls_psr_insert ON prs_scale_results FOR INSERT
WITH CHECK (
    rls_user_role() = ANY (ARRAY['super_admin', 'clinic_admin', 'clinical_assistant', 'doctor'])
    OR instance_id IN (SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id())
);

DROP POLICY IF EXISTS rls_psr_update ON prs_scale_results;
CREATE POLICY rls_psr_update ON prs_scale_results FOR UPDATE
USING (
    rls_user_role() = ANY (ARRAY['super_admin', 'doctor'])
    OR instance_id IN (SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id())
);

DROP POLICY IF EXISTS rls_pfr_insert ON prs_final_results;
CREATE POLICY rls_pfr_insert ON prs_final_results FOR INSERT
WITH CHECK (
    rls_user_role() = 'super_admin'
    OR instance_id IN (SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id())
);

DROP POLICY IF EXISTS rls_pfr_update ON prs_final_results;
CREATE POLICY rls_pfr_update ON prs_final_results FOR UPDATE
USING (
    rls_user_role() = 'super_admin'
    OR instance_id IN (SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id())
);
