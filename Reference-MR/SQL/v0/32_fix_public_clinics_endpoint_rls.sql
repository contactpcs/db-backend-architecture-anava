-- ============================================================
-- 32_fix_public_clinics_endpoint_rls.sql
--
-- Same class of bug as SQL/31 (RLS enforced for real for the first time on
-- RDS surfaced a gap local dev's superuser-bypass role never exercised):
-- GET /auth/clinics (auth/router.py::list_public_clinics) is a genuinely
-- public, unauthenticated endpoint — the clinic picker on the self-
-- registration form — with no RLS context to set at all (there's no caller
-- identity yet). rls_clinics_select had no fallback for that, so the query
-- silently returned zero rows instead of erroring, reproducing the exact
-- "clinic dropdown is empty" bug from before, just via a different
-- mechanism than the original missing-endpoint one.
--
-- Fix: clinic name/city/state/address aren't sensitive per-row data (unlike
-- profiles/patients) — allow open/non-closing clinics to be read by anyone,
-- matching exactly what the query itself already filters for
-- (status NOT IN ('pending_closure', 'closed')). This doesn't relax
-- anything the endpoint wasn't already going to expose.
-- ============================================================

DROP POLICY IF EXISTS rls_clinics_select ON clinics;
CREATE POLICY rls_clinics_select ON clinics FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND region_id = rls_region_id())
    OR clinic_id = rls_clinic_id()
    OR status NOT IN ('pending_closure', 'closed')
);
