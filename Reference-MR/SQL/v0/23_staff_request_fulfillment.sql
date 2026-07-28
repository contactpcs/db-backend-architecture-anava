-- ============================================================
-- Anava Clinic — DB Schema
-- File 23: Staff request fulfillment link
--
-- Regional_admin now creates the actual doctor/CA/receptionist
-- profile as a separate manual step after approving a staff_request
-- (see 22_staff_onboarding_lockdown.sql) — nothing tied that created
-- profile back to the request it fulfilled. This adds that link.
--
-- staff_requests.status stays 'approved' forever once decided — no
-- new status value added, no CHECK constraint touched.
-- fulfilled_profile_id IS NOT NULL is the fulfillment signal instead.
-- A request can be fulfilled at most once; a second POST
-- /doctors|/clinical-assistants|/receptionists referencing an
-- already-fulfilled staff_request_id is rejected in the service
-- layer (STAFF_REQUEST_ALREADY_FULFILLED), not by a DB constraint.
--
-- Applied via alembic migration 0004_staff_request_fulfillment.py
-- (this file is the schema source-of-truth doc, same convention as
-- 19_admin_workflow_updates.sql / 0002_admin_workflow.py).
-- ============================================================

ALTER TABLE staff_requests ADD COLUMN fulfilled_profile_id UUID REFERENCES profiles(id) ON DELETE RESTRICT;
ALTER TABLE staff_requests ADD COLUMN fulfilled_at TIMESTAMPTZ;
