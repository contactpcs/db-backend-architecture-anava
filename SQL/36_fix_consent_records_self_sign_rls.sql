-- ============================================================
-- 36_fix_consent_records_self_sign_rls.sql
--
-- rls_cr_update (SQL/15) only let super_admin/regional_admin/clinic_admin
-- UPDATE consent_records — doctor/clinical_assistant/receptionist/patient
-- were never in that list, so any of those roles signing their OWN pending
-- consent record matched zero rows under real RLS enforcement. repo.sign()
-- then returned no RETURNING row, service.sign() treated that as a
-- concurrent-double-submit loser and just re-fetched the still-pending
-- record instead of erroring — so PATCH /consent-records/{id}/status
-- returned 200 with status still 'pending', no visible error. The frontend,
-- seeing consent still unsigned, kept showing the consent form — the
-- "click Sign & Continue, land right back on the same form" loop. Local
-- dev's superuser-bypass role never exercised this; regional_admin/
-- clinic_admin signing worked fine because those two roles are the ones
-- already hardcoded into the policy, masking the gap until the first
-- non-admin staff role (doctor) tried it.
--
-- Fix: same self-ownership clause rls_cr_select already has, extended to
-- UPDATE — a caller can always update a consent_records row that's theirs
-- (patient_id or staff_id = their own profile id), on top of the existing
-- admin-role allowance.
-- ============================================================

DROP POLICY IF EXISTS rls_cr_update ON consent_records;
CREATE POLICY rls_cr_update ON consent_records FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin')
    OR patient_id = rls_user_id()
    OR staff_id = rls_user_id()
);
