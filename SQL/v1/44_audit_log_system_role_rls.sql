-- 44_audit_log_system_role_rls.sql
--
-- Same bug class 25/26 already fixed elsewhere, found live: confirm_mock_payment
-- (payments/service.py) runs the appointments.status 'selected' -> 'paid'
-- transition as 'system' role (SET LOCAL app.current_user_role='system',
-- matching the Razorpay webhook's own pattern, since the caller may be an
-- authenticated 'patient' and rls_payments_update grants only staff/system).
--
-- That transition writes an audit row via AppointmentService._write_audit ->
-- appointment_audit_logs, inside the SAME transaction, so it inherits the same
-- 'system' role SET LOCAL put in place. rls_apal_insert (17_rls_policies.sql)
-- was never patched with a 'system' branch -- unlike rls_appt_update, which
-- already has one. Under FORCE ROW LEVEL SECURITY an INSERT whose WITH CHECK
-- fails raises InsufficientPrivilegeError, not a silent 0-row skip, so this
-- surfaced immediately as a 500 on the very first real mock payment.
--
-- APPLY ORDER: after 43. Independent of 32-42.

BEGIN;

DROP POLICY IF EXISTS "rls_apal_insert" ON core."appointment_audit_logs";
CREATE POLICY "rls_apal_insert" ON core."appointment_audit_logs" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text,
                                      'clinical_assistant'::text, 'receptionist'::text, 'system'::text]))
    );

COMMIT;
