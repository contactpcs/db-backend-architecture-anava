-- Fix: Razorpay webhook has no logged-in user, so app.current_user_role was
-- never set on its DB session — rls_user_role() returned NULL, and neither
-- payments nor treatment_sessions had a policy matching NULL, so the
-- webhook's own UPDATE silently affected 0 rows (FORCE RLS + no matching
-- policy = 0 rows, not an error). Fix: a dedicated 'system' role value,
-- distinct from 'super_admin' so the audit trail honestly shows an
-- unattended system write, not a human admin action. Paired with a
-- SET LOCAL app.current_user_role = 'system' in payments/service.py's
-- handle_webhook() — this policy change alone does nothing without that.

-- SELECT too — the webhook looks the payment up by razorpay_order_id
-- (get_by_razorpay_order_id) before it can update it. 'system' needs both.
ALTER POLICY "rls_payments_select" ON core."payments"
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text, 'system'::text]))
        OR (session_id IN (SELECT sessions.session_id FROM sessions WHERE sessions.clinic_id = rls_clinic_id()))
        OR (order_id IN (SELECT store_orders.order_id FROM store_orders WHERE store_orders.clinic_id = rls_clinic_id()))
    );

ALTER POLICY "rls_payments_update" ON core."payments"
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'system'::text])));

ALTER POLICY "rls_ts_update" ON core."treatment_sessions"
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'system'::text]))
        OR ((rls_user_role() = 'clinical_assistant'::text) AND (ca_id = rls_user_id()))
        OR ((rls_user_role() = 'clinic_admin'::text) AND (plan_id IN (
            SELECT tp.plan_id FROM treatment_plans tp
            JOIN treatment_cycles tc ON tc.cycle_id = tp.cycle_id
            WHERE tc.clinic_id = rls_clinic_id()
        )))
    );
