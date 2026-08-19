-- 42_payments_status_vocab_fix.sql
--
-- Fixes B9: 31_appointments_payment_states.sql shipped a double-charge guard
-- watching payments.status = 'captured'. Nothing in the app has ever written
-- that value -- payments/service.py, repository.py and router.py all read and
-- write 'paid' (matches ERD_Anava.sql's own payments.status CHECK design,
-- never applied, which also lists 'paid' and never 'captured'). The guard has
-- been watching an empty set since 31 was applied: two 'paid' rows can land
-- against the same appointment right now with no constraint violation.
--
-- Decision: fix the index to the vocabulary the running code actually uses
-- ('paid'), not the other way around. 'captured' does not appear anywhere
-- else in the applied schema or in any module that touches core.payments.
--
-- 31 is already APPLIED (2026-08-13, Anava_App_v1) -- editing it in place
-- would not touch the live index (CREATE ... IF NOT EXISTS is a no-op against
-- an index that already exists under that name). Same convention 33 used to
-- alter what 30 created, and 41 used to alter what 36/37 created: a new file
-- that DROPs and recreates, not an edit to the applied file.
--
-- APPLY ORDER: after 31. Independent of 32-41.

BEGIN;

DROP INDEX IF EXISTS core."uq_payments_one_captured_per_appointment";

CREATE UNIQUE INDEX IF NOT EXISTS "uq_payments_one_paid_per_appointment"
    ON core."payments" ("appointment_id")
    WHERE "status" = 'paid' AND "appointment_id" IS NOT NULL;

COMMIT;
