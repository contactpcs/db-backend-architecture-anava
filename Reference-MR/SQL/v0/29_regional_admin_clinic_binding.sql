-- ============================================================
-- 29_regional_admin_clinic_binding.sql
--
-- Corrects the region/clinic/admin creation order. The real business flow:
-- region created -> its first clinic created (auto is_main_branch=TRUE) ->
-- regional_admin assigned FROM that main-branch clinic (must carry its
-- clinic_id, not just region_id) -> that same clinic's own separate
-- clinic_admin created afterward. Reverses a rule from
-- SQL/27_fix_regional_admin_scope.sql that deliberately let a regional_admin
-- exist with no clinic at all — that was correct for the bug it fixed
-- (a bricked account), but the actual intended order requires a clinic to
-- come first, so it's superseded here.
--
-- One-time data reset: tightening admins.chk_admins_scope to require
-- clinic_id for regional_admin will fail immediately against existing rows
-- (a real regional_admin was created with clinic_id NULL under the old
-- rule). Per explicit instruction, this migration also wipes every
-- profile/admin/region/clinic except the super_admin bootstrap account —
-- this is what makes the stricter constraint applicable to a clean slate
-- rather than a targeted backfill of unrecoverable old data.
-- ============================================================

-- Everything that transitively depends on clinics/regions/profiles (patient
-- and staff clinical data, requests, logs, store/payments, scheduling).
-- CASCADE is safe here — none of admins/clinics/regions/profiles are
-- downstream of any of these, so it cannot reach further than intended.
TRUNCATE TABLE
    activity_logs, appointment_requests, appointments, assessment_protocol_requests,
    ca_doctor_assignments, clinic_requests, clinic_staff_assignments, clinical_assistants,
    consent_records, device_assignments, doctor_patient_assignments, doctor_schedule_overrides,
    doctor_weekly_schedules, doctors, inventory, notifications, patient_clinic_transfers,
    patient_eeg_files, patient_medical_history_files, patients, receptionists, sessions,
    staff_requests, stock_transfers, store_orders, treatment_cycles, anamnesis_assessments,
    appointment_audit_logs, doctor_session_notes, patient_disease_selection,
    patient_scale_assignments, payments, prs_assessment_instances, treatment_plans,
    treatment_sessions, outbox_events, audit_logs
CASCADE;

-- admins.region_id/clinic_id FK-reference clinics/regions — Postgres blocks
-- TRUNCATE on a table with ANY existing FK constraint from another table
-- regardless of whether that other table currently has rows, so CASCADE is
-- required here even though everything it would touch is already empty
-- from the truncate above. The super_admin's admins row is restored right after.
TRUNCATE TABLE admins, clinics, regions CASCADE;

INSERT INTO admins (profile_id, admin_type)
SELECT id, 'super_admin' FROM profiles WHERE role = 'super_admin'
ON CONFLICT (profile_id) DO NOTHING;

DELETE FROM profiles WHERE role != 'super_admin';

-- Regional admin now requires a home clinic (the region's main-branch one,
-- enforced in app code, not here — CHECK constraints can't reach across
-- tables to verify is_main_branch).
ALTER TABLE admins DROP CONSTRAINT chk_admins_scope;
ALTER TABLE admins ADD CONSTRAINT chk_admins_scope CHECK (
    (admin_type = 'super_admin'       AND region_id IS NULL     AND clinic_id IS NULL)
    OR (admin_type = 'regional_admin' AND region_id IS NOT NULL AND clinic_id IS NOT NULL)
    OR (admin_type = 'clinic_admin'   AND clinic_id IS NOT NULL)
);

-- regional_admin now gets a normal clinic_staff_assignments row at their
-- home clinic too, same as every other staff role.
ALTER TABLE clinic_staff_assignments DROP CONSTRAINT clinic_staff_assignments_staff_role_check;
ALTER TABLE clinic_staff_assignments ADD CONSTRAINT clinic_staff_assignments_staff_role_check CHECK (staff_role IN (
    'regional_admin', 'clinic_admin', 'doctor', 'clinical_assistant', 'receptionist'
));
