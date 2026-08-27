-- 67_payment_logs_survive_deletion.sql
--
-- APPLY ORDER: after 66.
--
-- THE PROBLEM (found live, 27 Aug 2026, same day as 66)
--
-- app/workers/hold_sweeper.py releases an expired 'selected' appointment
-- hold by deleting the appointment row — but core.payments.appointment_id
-- is FK ON DELETE RESTRICT (30 §5), and every abandoned checkout has a
-- payments row pointing at it (create_order writes it before Checkout even
-- opens). That RESTRICT blocked the sweeper's DELETE on every real pass,
-- rolling back the whole transaction and silently re-failing forever —
-- confirmed live: two holds sat expired 25+ minutes, untouched.
--
-- Fixed in application code (hold_sweeper.py) by having the sweeper delete
-- the dangling payments row itself before deleting the appointment — safe,
-- since only 'pending'/'failed' payments can ever be attached to a still-
-- 'selected' expired appointment (a 'paid' one already moved the
-- appointment out of 'selected' via mark_paid()).
--
-- BUT payment_logs.payment_id was FK ON DELETE CASCADE (66) — so deleting
-- the payments row also destroyed every payment_logs row describing it,
-- including the very "this payment was abandoned/failed" history 66 exists
-- to keep. Confirmed live: a payment_logs row proving a payment.failed
-- webhook had been received and handled correctly was wiped out by the very
-- next hold-sweep pass. Directly contradicts 66's own purpose (a durable
-- history "sitting beside" the mutable payments row) the same day it shipped.
--
-- THE FIX
--
-- payment_id becomes nullable, and the FK becomes ON DELETE SET NULL — a
-- payment_logs row survives its parent payments row being deleted, orphaned
-- but intact (amount/currency/status/failure_reason/gateway_response were
-- already snapshotted onto the log row at insert time, so an orphaned row
-- still says everything it always said). 'system' is added to the source
-- CHECK so hold_sweeper can log its own "hold expired before payment
-- completed" event before it deletes anything.

BEGIN;

ALTER TABLE core."payment_logs" ALTER COLUMN "payment_id" DROP NOT NULL;

ALTER TABLE core."payment_logs" DROP CONSTRAINT IF EXISTS "fk_payment_logs_payment_id";
ALTER TABLE core."payment_logs"
    ADD CONSTRAINT "fk_payment_logs_payment_id"
    FOREIGN KEY ("payment_id") REFERENCES core."payments" ("payment_id") ON DELETE SET NULL;

ALTER TABLE core."payment_logs" DROP CONSTRAINT IF EXISTS "chk_payment_logs_source";
ALTER TABLE core."payment_logs"
    ADD CONSTRAINT "chk_payment_logs_source"
    CHECK ("source" IN ('order_created', 'razorpay_webhook', 'client_verify', 'staff_action', 'system'));

COMMENT ON COLUMN core."payment_logs"."payment_id" IS 'Nullable — ON DELETE SET NULL. NULL means the parent payments row was later deleted (e.g. app/workers/hold_sweeper.py cleaning up an abandoned/expired checkout); the log row survives as an orphaned but complete historical record (amount/currency/status/failure_reason/gateway_response were already snapshotted at insert time).';

COMMIT;
