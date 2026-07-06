-- ============================================================
-- 27_fix_regional_admin_consent_scope.sql
--
-- Bug: consent_records.clinic_id is NOT NULL, but a regional_admin is not
-- clinic-scoped (admins.clinic_id is correctly nullable for that tier, per
-- chk_admins_scope in 02_core_tables.sql). RegionService.assign_admin
-- (admin/service.py) worked around this by looking up "any clinic in the
-- region" to satisfy the NOT NULL constraint when creating the regional_
-- admin's staff_onboarding consent record. When the region has zero clinics
-- yet — the normal case, since a clinic needs a regional_admin assigned
-- before it can go 'active' — that lookup returns nothing and the consent
-- record creation was silently skipped entirely. The profile is still
-- created with is_active=FALSE, so the regional_admin is forced to the
-- consent-sign screen on login, but no consent_records row exists for them
-- to sign — permanently bricking the account ("No pending consent record
-- found for your account").
--
-- Fix: consent isn't inherently clinic-scoped — it's staff/patient-scoped,
-- with clinic_id as an incidental tenant tag used for staff hired at a
-- specific clinic. A regional_admin's tenant tag is their region, not any
-- one clinic. Make clinic_id nullable, add region_id for this case, and
-- require at least one of them.
-- ============================================================

ALTER TABLE consent_records
    ALTER COLUMN clinic_id DROP NOT NULL;

ALTER TABLE consent_records
    ADD COLUMN region_id UUID REFERENCES regions(region_id) ON DELETE RESTRICT;

ALTER TABLE consent_records
    ADD CONSTRAINT chk_consent_scope CHECK (clinic_id IS NOT NULL OR region_id IS NOT NULL);

-- rls_cr_select's regional_admin branch already matched on clinic_id IN
-- (clinics in their region) — extend it to also match a region_id-scoped
-- record directly (a regional_admin's own staff_onboarding row already
-- matches via `staff_id = rls_user_id()` regardless, this branch is for
-- another admin/staff member reviewing region-scoped records).
DROP POLICY IF EXISTS rls_cr_select ON consent_records;
CREATE POLICY rls_cr_select ON consent_records FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND (
        region_id = rls_region_id()
        OR clinic_id IN (SELECT clinic_id FROM clinics WHERE region_id = rls_region_id())
    ))
    OR (rls_user_role() IN ('clinic_admin', 'doctor', 'clinical_assistant', 'receptionist') AND clinic_id = rls_clinic_id())
    OR patient_id = rls_user_id()
    OR staff_id = rls_user_id()
);
