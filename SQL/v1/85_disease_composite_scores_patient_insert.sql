-- 85_disease_composite_scores_patient_insert.sql
--
-- Fixes a gap in 84_versioned_disease_composite.sql's rls_dcs_insert policy:
-- its own comment says it's "Same shape as core.prs_scale_results' RLS"
-- (rls_psr_insert, 17_rls_policies.sql), but it only carried over the STAFF
-- branch (super_admin/clinic_admin/clinical_assistant/doctor) and dropped
-- the patient-self-write branch entirely. rls_psr_insert allows a patient to
-- insert into prs_scale_results for their OWN instance
-- (instance_id IN (SELECT ... WHERE patient_id = rls_user_id())); disease_
-- composite_scores has no instance_id to key that check on, so this uses
-- the same patient_id = rls_user_id() form the table's own SELECT policy
-- (rls_dcs_select) already uses for the identical purpose.
--
-- Live-caught: a patient finalizing their own PRS scale from the patient
-- portal 500s with `InsufficientPrivilegeError: new row violates row-level
-- security policy for table "disease_composite_scores"` the moment
-- _compute_asof_disease_composite() tries to insert the recomputed
-- composite — every patient-initiated PRS submission for a scored disease
-- was broken, not just doctor/staff-initiated ones.
--
-- APPLY ORDER: after 84.

-- Same gap, same reason, in rls_dcs_update: ensure_baseline() runs an
-- UPDATE unconditionally after every insert (a no-op once a baseline
-- already exists, per its own WHERE NOT EXISTS guard) — but for a
-- patient's very FIRST-ever completed composite for a disease, that UPDATE
-- really does need to flip is_baseline, under whatever role initiated the
-- request. rls_dcs_update's original USING (super_admin/doctor only) would
-- block that the moment the insert fix above lets it get that far.

BEGIN;

DROP POLICY IF EXISTS "rls_dcs_insert" ON core."disease_composite_scores";

CREATE POLICY "rls_dcs_insert" ON core."disease_composite_scores" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = ANY (ARRAY['super_admin', 'clinic_admin', 'clinical_assistant', 'doctor']))
        OR ("patient_id" = rls_user_id())
    );

DROP POLICY IF EXISTS "rls_dcs_update" ON core."disease_composite_scores";

CREATE POLICY "rls_dcs_update" ON core."disease_composite_scores" FOR UPDATE TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin', 'doctor']))
        OR ("patient_id" = rls_user_id())
    );

COMMIT;

-- Verification:
--   SELECT polname, pg_get_expr(polwithcheck, polrelid) AS with_check, pg_get_expr(polqual, polrelid) AS using_expr
--   FROM pg_policy WHERE polrelid = 'core.disease_composite_scores'::regclass AND polname IN ('rls_dcs_insert', 'rls_dcs_update');
-- should show both the staff-role array check and patient_id = rls_user_id() on each.
