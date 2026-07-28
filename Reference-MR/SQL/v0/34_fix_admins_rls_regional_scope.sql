-- ============================================================
-- 34_fix_admins_rls_regional_scope.sql
--
-- rls_admins_select (SQL/15) only let a regional_admin see admins rows
-- where admins.region_id = their own region. That column is only ever set
-- on regional_admin rows themselves (RegionService.assign_admin) —
-- clinic_admin rows always have admins.region_id = NULL and only
-- admins.clinic_id set (ClinicService.assign_admin), since a clinic_admin
-- is clinic-scoped, not region-scoped. So RLS silently dropped every
-- clinic_admin row for a regional_admin caller, regardless of the app-layer
-- query filter — the new "Clinic Admins" section under the regional-admin
-- portal (admin/repository.py's list() already reaches clinic_admin rows
-- via a c.region_id join, but that never mattered — RLS ran first and
-- already excluded the rows before that WHERE clause was even evaluated).
--
-- Fix: also let a regional_admin see any admins row whose clinic belongs
-- to their region.
-- ============================================================

DROP POLICY IF EXISTS rls_admins_select ON admins;
CREATE POLICY rls_admins_select ON admins FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND region_id = rls_region_id())
    OR (
        rls_user_role() = 'regional_admin'
        AND clinic_id IN (SELECT clinic_id FROM clinics WHERE region_id = rls_region_id())
    )
    OR profile_id = rls_user_id()
);
