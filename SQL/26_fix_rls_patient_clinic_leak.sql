-- ============================================================
-- 26_fix_rls_patient_clinic_leak.sql
--
-- Bug: several SELECT policies (15_rls_policies.sql) grant "same clinic"
-- visibility with no role guard, e.g.:
--     OR primary_clinic_id = rls_clinic_id()
--     OR patient_id IN (SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id())
-- These clauses were meant to let clinic-scoped STAFF (clinic_admin,
-- doctor, clinical_assistant, receptionist) see every patient/record at
-- their own clinic. But core/middleware.py also sets app.current_clinic_id
-- for role='patient' (to the patient's own primary_clinic_id, needed
-- elsewhere), and these clauses never checked the caller's role — so any
-- patient could see every OTHER patient (and their anamnesis, PRS
-- instances/responses, disease selection, scale assignments, consent
-- records) at the same clinic. Confirmed exploitable: GET /patients as a
-- patient-role token returned all 3 patients at the clinic, not just the
-- caller's own row.
--
-- Fix: scope each "same clinic" clause to the actual staff roles it was
-- meant for (matches _ALL_STAFF minus super_admin/regional_admin, which
-- already have their own explicit branches above these clauses).
-- ============================================================

DROP POLICY IF EXISTS rls_patients_select ON patients;
CREATE POLICY rls_patients_select ON patients FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND primary_clinic_id IN (
        SELECT clinic_id FROM clinics WHERE region_id = rls_region_id()
    ))
    OR (rls_user_role() IN ('clinic_admin', 'doctor', 'clinical_assistant', 'receptionist') AND primary_clinic_id = rls_clinic_id())
    OR profile_id = rls_user_id()
    OR (rls_user_role() = 'doctor' AND primary_doctor_id = rls_user_id())
);

DROP POLICY IF EXISTS rls_cr_select ON consent_records;
CREATE POLICY rls_cr_select ON consent_records FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND clinic_id IN (
        SELECT clinic_id FROM clinics WHERE region_id = rls_region_id()
    ))
    OR (rls_user_role() IN ('clinic_admin', 'doctor', 'clinical_assistant', 'receptionist') AND clinic_id = rls_clinic_id())
    OR patient_id = rls_user_id()
    OR staff_id = rls_user_id()
);

DROP POLICY IF EXISTS rls_pai_select ON prs_assessment_instances;
CREATE POLICY rls_pai_select ON prs_assessment_instances FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR patient_id = rls_user_id()
    OR (
        rls_user_role() IN ('clinic_admin', 'doctor', 'clinical_assistant', 'receptionist')
        AND cycle_id IN (SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id())
    )
);

DROP POLICY IF EXISTS rls_prs_resp_select ON prs_responses;
CREATE POLICY rls_prs_resp_select ON prs_responses FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR instance_id IN (
        SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id()
    )
    OR (
        rls_user_role() IN ('clinic_admin', 'doctor', 'clinical_assistant', 'receptionist')
        AND instance_id IN (
            SELECT instance_id FROM prs_assessment_instances
            WHERE cycle_id IN (SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id())
        )
    )
);

DROP POLICY IF EXISTS rls_pds_select ON patient_disease_selection;
CREATE POLICY rls_pds_select ON patient_disease_selection FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR patient_id = rls_user_id()
    OR (
        rls_user_role() IN ('clinic_admin', 'doctor', 'clinical_assistant', 'receptionist')
        AND patient_id IN (SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id())
    )
);

DROP POLICY IF EXISTS rls_anamnesis_select ON anamnesis_assessments;
CREATE POLICY rls_anamnesis_select ON anamnesis_assessments FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR patient_id = rls_user_id()
    OR (
        rls_user_role() IN ('clinic_admin', 'doctor', 'clinical_assistant', 'receptionist')
        AND patient_id IN (SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id())
    )
);

DROP POLICY IF EXISTS rls_anar_select ON anamnesis_responses;
CREATE POLICY rls_anar_select ON anamnesis_responses FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR anamnesis_id IN (
        SELECT anamnesis_id FROM anamnesis_assessments WHERE patient_id = rls_user_id()
    )
    OR (
        rls_user_role() IN ('clinic_admin', 'doctor', 'clinical_assistant', 'receptionist')
        AND anamnesis_id IN (
            SELECT anamnesis_id FROM anamnesis_assessments
            WHERE patient_id IN (SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id())
        )
    )
);

DROP POLICY IF EXISTS rls_psa_select ON patient_scale_assignments;
CREATE POLICY rls_psa_select ON patient_scale_assignments FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR patient_id = rls_user_id()
    OR assigned_by = rls_user_id()
    OR (
        rls_user_role() IN ('clinic_admin', 'doctor', 'clinical_assistant', 'receptionist')
        AND patient_id IN (SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id())
    )
);
