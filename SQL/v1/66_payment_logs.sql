-- 66_payment_logs.sql
--
-- APPLY ORDER: after 65. Independent of 60-65.
--
-- THE PROBLEM (Mohan, 27 Aug 2026)
--
-- core.payments is mutated in place — status flips pending -> paid (or
-- waived/refunded) on the same row, with no history kept. Two concrete
-- gaps this causes, both confirmed in payments/service.py before this
-- migration:
--   1. Razorpay's payment.failed webhook event is received, signature-
--      verified, and then silently ignored (handle_webhook only acts on
--      payment.captured/order.paid) — a declined card leaves the payment
--      stuck at 'pending' forever, with no record it ever failed, let alone
--      why. Same gap client-side: MockPaymentModal's checkout.on
--      ("payment.failed", ...) handler is local UI state only, never calls
--      the backend.
--   2. payments.gateway_response JSONB exists on the table for exactly
--      "store the raw gateway payload here" and is never written by any
--      code path — always '{}'.
-- Net effect: no way to answer "which payments failed and why" from the DB
-- at all, only from whatever's in the outbox_events breadcrumbs (payment_id/
-- amount/status only, no gateway detail) or structlog output.
--
-- THE FIX
--
-- core.payment_logs — one append-only row per payment *event* (order
-- created, webhook received, client-verify attempted, staff manual status
-- change), not per payment. core.payments stays the current-state row
-- (unchanged shape); this is the history sitting beside it. A "show me
-- failed payments" query now has somewhere real to look: WHERE status =
-- 'failed', with failure_code/failure_reason/gateway_response actually
-- populated, and gateway_event distinguishing "we ignored this on purpose"
-- (payment.authorized, refund.*) from "this is why the payment failed"
-- (payment.failed's error_code/error_description).
--
-- APPLICATION WIRING (payments/service.py, this migration doesn't do it):
--   - order creation (PaymentRepository.create) logs a 'pending' row.
--   - handle_webhook logs EVERY event it receives, not just the two it acts
--     on — payment.failed now also flips payments.status to 'failed' (it
--     didn't before) with failure_code/reason extracted from the payload.
--   - verify_payment logs a 'failed' row (source='client_verify') on a
--     signature mismatch or order-id mismatch, without touching payments.
--     status — a verify failure is not proof the payment failed, could be a
--     tampered client request; the log captures it as a security-relevant
--     event without claiming authority over payment state.
--   - update_status (the paid/waived/refunded/manual-failed path already
--     shared by webhook/verify/staff-PATCH) logs its outcome too.
--
-- NOTE ON RLS: core.payments' own rls_payments_insert/select/update
-- (17_rls_policies.sql:313-324) only names super_admin/clinic_admin/
-- receptionist/regional_admin and a `sessions`/`store_orders` join — no
-- patient, no doctor, no 'system' (the role handle_webhook/verify_payment
-- SET LOCAL to). That looks stale against how payments/service.py actually
-- reads/writes today (patient-initiated create_order, system-role webhook
-- writes) but is a pre-existing condition on `payments` itself, not
-- something this migration changes or depends on fixing. payment_logs below
-- uses the roles actually granted access to payments today in the app layer
-- (payments/router.py's _ALL_STAFF + patient via ownership), not a copy of
-- payments' policy, so it doesn't inherit whatever gap that has.

BEGIN;

CREATE TABLE IF NOT EXISTS core."payment_logs" (
    "log_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "payment_id" UUID NOT NULL,
    "status" TEXT NOT NULL,
    "amount" NUMERIC(10,2) NOT NULL,
    "currency" TEXT NOT NULL,
    "payment_method" TEXT,
    "razorpay_order_id" TEXT,
    "razorpay_payment_id" TEXT,
    -- Populated on a failure — Razorpay's payload.payment.entity.error_code/
    -- error_description for a webhook-sourced failure, or this app's own
    -- rejection reason (e.g. "Invalid payment signature") for a client-
    -- verify failure. NULL for a non-failure event.
    "failure_code" TEXT,
    "failure_reason" TEXT,
    -- Where this log row came from — lets a reader tell "the gateway told us
    -- this" apart from "a human clicked a button".
    "source" TEXT NOT NULL,
    -- Razorpay's own event name for a webhook-sourced row (payment.captured,
    -- payment.failed, payment.authorized, refund.processed, ...). NULL for
    -- non-webhook sources.
    "gateway_event" TEXT,
    -- The raw payload for this specific event — webhook body, or the
    -- checkout-verify fields. This is the column payments.gateway_response
    -- was supposed to be and never was.
    "gateway_response" JSONB NOT NULL DEFAULT '{}',
    "changed_by" UUID,
    "changed_by_role" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE core."payment_logs" DROP CONSTRAINT IF EXISTS "payment_logs_pkey" CASCADE;
ALTER TABLE core."payment_logs" ADD CONSTRAINT "payment_logs_pkey" PRIMARY KEY ("log_id");

ALTER TABLE core."payment_logs" DROP CONSTRAINT IF EXISTS "chk_payment_logs_status";
ALTER TABLE core."payment_logs"
    ADD CONSTRAINT "chk_payment_logs_status"
    CHECK ("status" IN ('pending', 'paid', 'failed', 'waived', 'refunded'));

ALTER TABLE core."payment_logs" DROP CONSTRAINT IF EXISTS "chk_payment_logs_source";
ALTER TABLE core."payment_logs"
    ADD CONSTRAINT "chk_payment_logs_source"
    CHECK ("source" IN ('order_created', 'razorpay_webhook', 'client_verify', 'staff_action'));

ALTER TABLE core."payment_logs" DROP CONSTRAINT IF EXISTS "fk_payment_logs_payment_id";
ALTER TABLE core."payment_logs"
    ADD CONSTRAINT "fk_payment_logs_payment_id"
    FOREIGN KEY ("payment_id") REFERENCES core."payments" ("payment_id") ON DELETE CASCADE;

ALTER TABLE core."payment_logs" DROP CONSTRAINT IF EXISTS "fk_payment_logs_changed_by";
ALTER TABLE core."payment_logs"
    ADD CONSTRAINT "fk_payment_logs_changed_by"
    FOREIGN KEY ("changed_by") REFERENCES core."profiles" ("id") ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_payment_logs_payment_id ON core."payment_logs" USING btree ("payment_id");
CREATE INDEX IF NOT EXISTS idx_payment_logs_status ON core."payment_logs" USING btree ("status");
CREATE INDEX IF NOT EXISTS idx_payment_logs_created_at ON core."payment_logs" USING btree ("created_at");

-- No updated_at / trigger — append-only, rows are never edited after insert.

-- RLS — same access shape as payments/router.py's actual app-level rules
-- (_ALL_STAFF read their clinic's payments; patient reads their own via the
-- payment they own), not a copy of payments' own stale policy (see header
-- note). Writes only ever come from this app's own service layer (system-
-- role webhook, or a real authenticated staff/patient request), so INSERT
-- is scoped the same as SELECT rather than restricted further — there's no
-- separate actor who should be able to read a payment's logs but not the
-- event that produced them.
ALTER TABLE core."payment_logs" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."payment_logs" FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_payment_logs_select" ON core."payment_logs";
CREATE POLICY "rls_payment_logs_select" ON core."payment_logs" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text, 'receptionist'::text, 'doctor'::text, 'clinical_assistant'::text, 'system'::text]))
        OR (payment_id IN (
            SELECT p.payment_id FROM payments p
            LEFT JOIN appointments a ON a.appointment_id = p.appointment_id
            LEFT JOIN store_orders so ON so.order_id = p.order_id
            WHERE COALESCE(a.patient_id, so.patient_id) = rls_user_id()
        ))
    );

-- Same shape as ops.outbox_events' own insert policy (26_rls_lockout_fixes.sql:16-17)
-- — any authenticated context (including 'system', which handle_webhook/
-- verify_payment SET LOCAL to) can log an event; there's no actor this
-- table needs to exclude, only genuinely anonymous writes.
DROP POLICY IF EXISTS "rls_payment_logs_insert" ON core."payment_logs";
CREATE POLICY "rls_payment_logs_insert" ON core."payment_logs" FOR INSERT TO public
    WITH CHECK (rls_user_role() IS NOT NULL);

GRANT SELECT ON core."payment_logs" TO anava_app;
GRANT INSERT ON core."payment_logs" TO anava_app;
GRANT SELECT ON core."payment_logs" TO anava_readonly;
GRANT SELECT ON core."payment_logs" TO anava_compliance;

COMMIT;
