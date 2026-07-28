-- ============================================================
-- 35_remove_clinic_admin_staff_request_role.sql
--
-- A clinic_admin's own hiring flow (staff_requests) let them request
-- another clinic_admin for their own clinic — that's not a role staff
-- requests should ever create anyway (clinic_admin position_role had no
-- corresponding fulfillment path in staff/service.py — no ClinicAdminService
-- exists there; clinic admins are only ever created via the dedicated
-- POST /clinics/{id}/assign-admin flow, admin/service.py::ClinicService.
-- assign_admin). Tightening the CHECK to match — no existing rows use
-- position_role = 'clinic_admin' as of this writing, so this is safe to
-- apply directly.
-- ============================================================

ALTER TABLE staff_requests DROP CONSTRAINT IF EXISTS staff_requests_position_role_check;
ALTER TABLE staff_requests ADD CONSTRAINT staff_requests_position_role_check
    CHECK (position_role IN ('doctor', 'clinical_assistant', 'receptionist'));
