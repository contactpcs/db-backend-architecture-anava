-- 121_payment_integrity_and_rls.sql
--
-- APPLY ORDER: after 120. Design: Documents/design_payment_audit_fixes.md (M4, L6).
--
-- 1. payments.status gets the CHECK it never had.
-- 2. payments SELECT: clinic_admin and regional_admin were unscoped at the
--    database level (the app scoped them). Now clinic_admin sees its clinic,
--    regional_admin its region; super_admin and system see everything.
-- 3. payment_logs SELECT: any staff role could read every clinic's payment
--    events, including the raw gateway payload with payer contact details.
--    Now a log row is visible exactly when its payment is (the subquery runs
--    under the caller's own payments policy). Orphaned logs (payment deleted)
--    stay visible to super_admin and system only.
--
-- Safe to apply: no code path depends on the wider visibility (all reads are
-- already scoped by role in payments/service.py); verified 2026-10-16 that
-- every existing payments.status value is in the new CHECK set.

BEGIN;

ALTER TABLE core."payments" DROP CONSTRAINT IF EXISTS "chk_payments_status";
ALTER TABLE core."payments"
    ADD CONSTRAINT "chk_payments_status"
    CHECK ("status" IN ('pending', 'paid', 'failed', 'waived', 'refunded', 'partially_refunded'))
    NOT VALID;

DROP POLICY IF EXISTS "rls_payments_select" ON core."payments";
CREATE POLICY "rls_payments_select" ON core."payments" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'system'::text]))
        OR (rls_user_role() = 'regional_admin'::text
            AND (appointment_id IN (SELECT a.appointment_id FROM appointments a JOIN clinics c ON c.clinic_id = a.clinic_id
                                     WHERE c.region_id = rls_region_id())
                 OR order_id IN (SELECT so.order_id FROM store_orders so JOIN clinics c ON c.clinic_id = so.clinic_id
                                  WHERE c.region_id = rls_region_id())))
        OR (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])
            AND (order_id IN (SELECT so.order_id FROM store_orders so WHERE so.clinic_id = rls_clinic_id())
                 OR appointment_id IN (SELECT a.appointment_id FROM appointments a WHERE a.clinic_id = rls_clinic_id())))
        OR (appointment_id IN (SELECT a.appointment_id FROM appointments a WHERE a.patient_id = rls_user_id()))
        OR (order_id IN (SELECT so.order_id FROM store_orders so WHERE so.patient_id = rls_user_id()))
    );

DROP POLICY IF EXISTS "rls_payment_logs_select" ON core."payment_logs";
CREATE POLICY "rls_payment_logs_select" ON core."payment_logs" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'system'::text]))
        OR (payment_id IN (SELECT p.payment_id FROM payments p))
    );

COMMIT;

ALTER TABLE core."payments" VALIDATE CONSTRAINT "chk_payments_status";
