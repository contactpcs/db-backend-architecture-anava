-- 46_anamnesis_insert_doctor_role.sql
--
-- Same bug class as 43 (rls_payments_insert) and 44 (rls_apal_insert): a
-- write path that's legitimate in code was never added to the RLS policy.
--
-- rls_anamnesis_insert (17_rls_policies.sql) allows super_admin,
-- receptionist, clinical_assistant, or the patient themself -- 'doctor' was
-- never added. AnamnesisForm's mode="doctor" (taken_by='doctor_on_behalf')
-- has called POST /patients/{id}/anamnesis as the doctor role since before
-- 45, but the doctor tab was always resolving to the SAME general_registration
-- row that already existed (the bug 45's assessment_stage fix corrected), so
-- the doctor never actually hit the "no record yet, start one" INSERT path
-- until now -- surfacing this as a fresh 500 (InsufficientPrivilegeError)
-- under FORCE ROW LEVEL SECURITY, not a silent 0-row skip.
--
-- APPLY ORDER: after 45. Independent of 32-44.

BEGIN;

DROP POLICY IF EXISTS "rls_anamnesis_insert" ON core."anamnesis_assessments";
CREATE POLICY "rls_anamnesis_insert" ON core."anamnesis_assessments" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'receptionist'::text,
                                      'clinical_assistant'::text, 'doctor'::text]))
        OR (patient_id = rls_user_id())
    );

COMMIT;
