-- 100_restore_payments_select_policy.sql
--
-- THE PROBLEM
--
-- 99_drop_legacy_sessions_add_clinical_notes.sql ran
-- `DROP TABLE core."sessions" CASCADE`. rls_payments_select (last altered in
-- 31_appointments_payment_states.sql) had a branch
-- `session_id IN (SELECT ... FROM sessions ...)`, so CASCADE silently
-- dropped the whole policy. core.payments has FORCE ROW LEVEL SECURITY, so
-- with no SELECT policy left:
--   * every read of payments returns 0 rows for every role (55 existing
--     payment rows became invisible: lists, receipts, dashboards show none);
--   * every `INSERT INTO payments ... RETURNING *` fails with "new row
--     violates row-level security policy" — RETURNING is checked against
--     the SELECT policy — so "Pay" 500s for every role.
-- Found 2026-10-01 by the API audit (patient follow-up booking -> Pay -> 500).
-- payments was the only RLS table left with no SELECT policy.
--
-- THE FIX
--
-- Recreate rls_payments_select from 31's definition, minus the dead
-- core.sessions branch (that table no longer exists; payments.session_id is
-- an unreferenced, always-NULL column since 99 dropped its FK), and with one
-- tightening found while testing this: 31's clinic branch
-- (`a.clinic_id = rls_clinic_id()`) also matched PATIENTS, because the auth
-- middleware sets app.current_clinic_id to a patient's primary clinic — a
-- patient could read every payment at their clinic (dry run 2026-10-01: 55
-- visible, only 13 their own). Clinic-wide visibility is now limited to
-- clinic staff roles; a patient sees only payments for their own
-- appointments or store orders (same rule payment_logs already uses).
-- Admin branch unchanged.
--
-- Note: the untightened version of this file was applied to RDS by hand on
-- 2026-10-01 (alembic_version stayed 0052). Re-running is safe: DROP IF
-- EXISTS + CREATE replaces it.

DROP POLICY IF EXISTS "rls_payments_select" ON core."payments";

CREATE POLICY "rls_payments_select" ON core."payments" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text,
                                      'clinic_admin'::text, 'system'::text]))
        OR (rls_user_role() = ANY (ARRAY['doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])
            AND (order_id IN (SELECT so.order_id FROM store_orders so WHERE so.clinic_id = rls_clinic_id())
                 OR appointment_id IN (SELECT a.appointment_id FROM appointments a WHERE a.clinic_id = rls_clinic_id())))
        OR (appointment_id IN (SELECT a.appointment_id FROM appointments a WHERE a.patient_id = rls_user_id()))
        OR (order_id IN (SELECT so.order_id FROM store_orders so WHERE so.patient_id = rls_user_id()))
    );
