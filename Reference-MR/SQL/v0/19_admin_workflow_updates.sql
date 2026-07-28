-- ============================================================
-- Anava Clinic — DB Schema
-- File 19: Admin workflow updates
-- Clinic creation is now a 2-step flow: create clinic (no admin
-- yet) -> assign clinic_admin separately. No other staff/patients
-- may be attached to a clinic until it has a clinic_admin.
-- ============================================================

ALTER TABLE clinics ALTER COLUMN clinic_admin_id DROP NOT NULL;
