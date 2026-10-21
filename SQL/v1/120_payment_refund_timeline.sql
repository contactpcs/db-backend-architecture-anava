-- 120_payment_refund_timeline.sql
--
-- APPLY ORDER: after 119. Additive, nullable columns only.
-- Design: Documents/design_appointment_policy_and_refunds.md, D13.
--
-- Refund progress shown to patients and staff as stages:
--   1. approved   refund_requested_at  (cancellation computed a refund > 0,
--                                       or a late capture was queued for refund)
--   2. sent       refund_initiated_at  (refund created at Razorpay — online only)
--   3. refunded   refunded_at          (already exists, 118)
-- A cash refund skips stage 2: the front desk hands it back and confirms.

BEGIN;

ALTER TABLE core."payments" ADD COLUMN IF NOT EXISTS "refund_requested_at" TIMESTAMPTZ;
ALTER TABLE core."payments" ADD COLUMN IF NOT EXISTS "refund_initiated_at" TIMESTAMPTZ;

COMMENT ON COLUMN core."payments"."refund_requested_at" IS 'When a refund became owed: the cancellation that approved it, or a late capture queued for a full refund.';
COMMENT ON COLUMN core."payments"."refund_initiated_at" IS 'When the refund was created at Razorpay (online payments only). NULL for cash refunds.';

-- Backfill rows that already carry a refund (from 118 onward). Best available
-- time is the row's last change. Exact enough for display, never used in rules.
UPDATE core."payments"
   SET "refund_requested_at" = COALESCE("refund_requested_at", "updated_at")
 WHERE "refund_status" IS NOT NULL;

UPDATE core."payments"
   SET "refund_initiated_at" = COALESCE("refund_initiated_at", "refunded_at", "updated_at")
 WHERE "razorpay_refund_id" IS NOT NULL;

COMMIT;
