-- 56_audit_log_patient_role_rls.sql
--
-- Same bug class 25/26/44 already fixed elsewhere, found live: claim_slot
-- (scheduling/service.py) writes an appointment_audit_logs row with
-- changed_by_role='patient' whenever a patient claims a slot on their own
-- planned protocol_followup/device_session appointment — cancel_own does
-- the identical thing for a patient cancelling their own appointment. Both
-- are genuine patient self-service actions (assert_owns_profile already
-- gates them before the write), not unattended system writes, so 'system'
-- alone doesn't cover this — 'patient' itself was simply never added to
-- rls_apal_insert (17_rls_policies.sql only ever listed staff roles + the
-- 44 fix added 'system' for the webhook path, never patient).
--
-- Under FORCE ROW LEVEL SECURITY an INSERT whose WITH CHECK fails raises
-- InsufficientPrivilegeError, not a silent 0-row skip — this surfaced
-- immediately as a 500-after-success on the very first real patient claim
-- (the appointment UPDATE itself succeeded; the audit-log INSERT that
-- follows in the same transaction did not, rolling the whole claim back).
--
-- APPLY ORDER: after 55. Independent of 32-54.

BEGIN;

DROP POLICY IF EXISTS "rls_apal_insert" ON core."appointment_audit_logs";
CREATE POLICY "rls_apal_insert" ON core."appointment_audit_logs" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text,
                                      'clinical_assistant'::text, 'receptionist'::text, 'system'::text,
                                      'patient'::text]))
    );

COMMIT;
