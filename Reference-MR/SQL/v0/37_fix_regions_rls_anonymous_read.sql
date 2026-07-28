-- ============================================================
-- 37_fix_regions_rls_anonymous_read.sql
--
-- rls_regions_select had no fallback for an anonymous (no RLS context)
-- caller — only super_admin or an authenticated caller's own region could
-- see any regions row. patients/service.py::PatientService.register (the
-- public self-registration endpoint, no auth) validates the target clinic
-- with "SELECT ... FROM clinics c JOIN regions r ON r.region_id = c.region_id
-- WHERE c.clinic_id = :id" — rls_clinics_select already has an anonymous
-- fallback (status not closed/pending_closure, added in an earlier fix for
-- the same reason), but regions didn't, so the JOIN's regions side was
-- invisible to this anonymous request and silently dropped the row —
-- "Clinic not found" for a clinic that genuinely exists and is open.
-- Local dev's superuser-bypass role never exercised this; only surfaced
-- once RLS was actually enforced (RDS) and someone tried self-registering
-- for real.
--
-- Fix: same shape as rls_clinics_select's fallback — any caller (including
-- anonymous) can see an active region.
-- ============================================================

DROP POLICY IF EXISTS rls_regions_select ON regions;
CREATE POLICY rls_regions_select ON regions FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR region_id = rls_region_id()
    OR is_active = TRUE
);
