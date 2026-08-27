-- 68_profiles_system_role_rls.sql
--
-- APPLY ORDER: after 67. Independent of 60-67.
--
-- THE PROBLEM (found live, 27 Aug 2026)
--
-- 31_appointments_payment_states.sql gave core.appointments' rls_appt_select/
-- update/delete a 'system' branch specifically so an unattended webhook/
-- hold-sweeper write (SET LOCAL app.current_user_role = 'system', no
-- clinic_id/user_id set) can still see and touch the row. core.profiles
-- never got the same fix.
--
-- app/modules/scheduling/repository.py's _APPT_SELECT does
-- "JOIN profiles pp ON pp.id = a.patient_id" — an INNER JOIN, required.
-- Under role='system', rls_profiles_select matches nothing (no role branch,
-- no id/cognito_sub/email match, no clinic_staff_assignments membership —
-- 'system' isn't a real logged-in person), so the join silently drops every
-- appointment row from the result even though core.appointments itself is
-- fully visible to 'system'. AppointmentService.get() then reports 404
-- APPOINTMENT_NOT_FOUND for an appointment that verifiably exists.
--
-- This breaks payments/service.py's mark_paid() call chain for BOTH real
-- entry points that run as 'system' — handle_webhook (Razorpay's own
-- payment.captured) and verify_payment (the client-callback confirm) —
-- meaning no gateway-driven 'paid' transition could ever complete. Confirmed
-- live: reproduced directly against the anava_app connection role (not the
-- RLS-bypassing migration credential, which masked this in earlier ad-hoc
-- checks) — the exact join returns zero rows under role='system', full rows
-- under any other tested context.
--
-- (The 11 payments that DID reach 'paid' before this fix all show
-- payment_method='razorpay_checkout', i.e. verify_payment — they predate
-- whatever earlier change tightened rls_profiles_select to its current
-- clinic-scoped form without a 'system' branch; this migration doesn't need
-- to know exactly when that happened, only that the current live policy is
-- missing it, confirmed above.)
--
-- THE FIX
--
-- Same shape as 31's fix to rls_appt_select: add 'system' to the role-array
-- branch, unconditional like super_admin/regional_admin — 'system' is an
-- unattended backend process (webhook, hold sweeper), not scoped to one
-- clinic's staff/patient roster the way clinic_admin/doctor/receptionist are.

BEGIN;

DROP POLICY IF EXISTS "rls_profiles_select" ON core."profiles";
CREATE POLICY "rls_profiles_select" ON core."profiles" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR (id = rls_user_id())
        OR (cognito_sub = rls_cognito_sub())
        OR (email = rls_email())
        OR (
            (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text]))
            AND (id IN (
                SELECT clinic_staff_assignments.profile_id FROM clinic_staff_assignments
                WHERE clinic_staff_assignments.clinic_id = rls_clinic_id() AND clinic_staff_assignments.is_active = true
                UNION
                SELECT patients.profile_id FROM patients WHERE patients.primary_clinic_id = rls_clinic_id()
            ))
        )
        OR (
            (rls_user_role() = 'patient'::text)
            AND (id IN (
                SELECT clinic_staff_assignments.profile_id FROM clinic_staff_assignments
                WHERE clinic_staff_assignments.clinic_id = rls_clinic_id() AND clinic_staff_assignments.is_active = true
            ))
        )
    );

COMMIT;
