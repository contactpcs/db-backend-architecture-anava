-- 43_mock_payment_lifecycle_lock.sql
--
-- Two independent, previously-deferred fixes, landing together because both
-- are prerequisites for the mock/dummy payment-confirmation flow (Anava
-- Payments Implementation Plan v3.1's §11 Phase 2 "code-first, then schema"
-- sequencing, applied here now that the code-side blockers are actually
-- fixed):
--
-- A. The two status-vocabulary CHECK constraints deferred since 30/31/33 --
--    all three files independently note the exact same two blockers:
--      1. appointment_type CHECK -- blocked on the staff booking path
--         writing legacy values ('initial_assessment', 'doctor_consultation',
--         'ca_session', 'treatment_session', 'demo_visit', 'teleconsult').
--         scheduling/schemas.py's APPOINTMENT_TYPES pattern has restricted
--         this to the 4-type model for a while now -- confirmed by reading
--         the file directly, not assumed.
--      2. status CHECK -- blocked on 'confirmed' being reachable via
--         PATCH /appointments/{id}/status. scheduling/schemas.py's
--         AppointmentStatusUpdate pattern already excludes 'confirmed' and
--         'scheduled'; this migration's sibling code change additionally
--         removes 'paid' from that same pattern (payment, not a staff PATCH,
--         is now the only route to 'paid' -- see payments/service.py's new
--         create_mock_order/confirm_mock_payment).
--    Both tables this touches hold 0 rows (same empty-table state 30/31/33
--    applied against), so this is the cheapest possible moment to do it.
--
-- B. rls_payments_insert (17_rls_policies.sql) has never been patched since
--    it was first written -- it allows only super_admin/clinic_admin/
--    receptionist. The mock payment flow inserts a core.payments row as
--    whichever role is paying (a patient paying for their own appointment,
--    or any clinic staff role recording one on a patient's behalf) -- under
--    FORCE ROW LEVEL SECURITY, every one of those would silently insert 0
--    rows today rather than error, the exact bug class 25/26 already fixed
--    elsewhere for other tables. Widened to the same _ALL_STAFF roster
--    scheduling/payments routers already use, plus 'system' (matches
--    rls_payments_update's existing 'system' branch), plus a patient
--    branch scoped to appointments they own -- mirrors rls_payments_select's
--    existing appointment_id ownership subquery (31_appointments_payment_states.sql).
--
-- APPLY ORDER: after 42. Independent of 32-41.

BEGIN;

-- A1. appointment_type
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_appointment_type"
    CHECK ("appointment_type" IN ('initial', 'follow_up', 'device_session', 'protocol_followup'));

-- A2. status
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_status"
    CHECK ("status" IN ('planned', 'selected', 'paid', 'cancelled', 'rescheduled',
                         'checked_in', 'in_progress', 'completed', 'no_show'));

-- B. rls_payments_insert
DROP POLICY IF EXISTS "rls_payments_insert" ON core."payments";
CREATE POLICY "rls_payments_insert" ON core."payments" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text,
                                      'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text,
                                      'system'::text]))
        OR (appointment_id IN (SELECT a.appointment_id FROM core."appointments" a WHERE a.patient_id = rls_user_id()))
    );

COMMIT;
