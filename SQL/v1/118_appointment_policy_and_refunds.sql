-- 118_appointment_policy_and_refunds.sql
--
-- APPLY ORDER: after 117. Depends on 64 (cancellation_policy_tiers, payment
-- refund columns), 65 (clinics), 66/67 (payment_logs).
--
-- Design: Documents/design_appointment_policy_and_refunds.md (D1-D9).
--
-- 1. clinics.reschedule_min_hours   per-clinic patient reschedule notice.
--                                   NULL = platform default (app config
--                                   appointment_reschedule_min_hours, 24).
-- 2. cancellation_policy_tiers      clinic_admin may write its own clinic's
--                                   tier set (super_admin still writes
--                                   defaults and any clinic).
-- 3. default tiers                  12 h -> 100 % for both session types,
--                                   only where no default tier exists yet,
--                                   so a super admin's own defaults are
--                                   never changed by this file.
-- 4. payments refund tracking       refund_status / razorpay_refund_id /
--                                   refunded_at, for the automatic Razorpay
--                                   refund and the cash "refund due" path.
-- 5. payment_logs.status            gains 'partially_refunded'.

BEGIN;

-- ── 1. per-clinic reschedule notice ────────────────────────────────────────
ALTER TABLE core."clinics" ADD COLUMN IF NOT EXISTS "reschedule_min_hours" NUMERIC(6,2);

ALTER TABLE core."clinics" DROP CONSTRAINT IF EXISTS "chk_clinics_reschedule_min_hours";
ALTER TABLE core."clinics"
    ADD CONSTRAINT "chk_clinics_reschedule_min_hours"
    CHECK ("reschedule_min_hours" IS NULL OR "reschedule_min_hours" >= 0);

COMMENT ON COLUMN core."clinics"."reschedule_min_hours" IS 'Minimum hours before a booked slot that a patient may still reschedule it. NULL = platform default (app config appointment_reschedule_min_hours). Clinic_admin editable for their own clinic (rls_clinics_update).';

-- ── 2. clinic_admin writes its own clinic's cancellation tiers ─────────────
DROP POLICY IF EXISTS "rls_cancellation_policy_tiers_insert" ON reference."cancellation_policy_tiers";
CREATE POLICY "rls_cancellation_policy_tiers_insert" ON reference."cancellation_policy_tiers" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = 'super_admin'::text)
        OR (rls_user_role() = 'clinic_admin'::text AND clinic_id = rls_clinic_id())
    );

DROP POLICY IF EXISTS "rls_cancellation_policy_tiers_update" ON reference."cancellation_policy_tiers";
CREATE POLICY "rls_cancellation_policy_tiers_update" ON reference."cancellation_policy_tiers" FOR UPDATE TO public
    USING (
        (rls_user_role() = 'super_admin'::text)
        OR (rls_user_role() = 'clinic_admin'::text AND clinic_id = rls_clinic_id())
    );

DROP POLICY IF EXISTS "rls_cancellation_policy_tiers_delete" ON reference."cancellation_policy_tiers";
CREATE POLICY "rls_cancellation_policy_tiers_delete" ON reference."cancellation_policy_tiers" FOR DELETE TO public
    USING (
        (rls_user_role() = 'super_admin'::text)
        OR (rls_user_role() = 'clinic_admin'::text AND clinic_id = rls_clinic_id())
    );

-- ── 3. default refund rule: cancel >= 12 h before -> 100 % ─────────────────
INSERT INTO reference."cancellation_policy_tiers" ("clinic_id", "session_type", "min_hours_before", "refund_percent")
SELECT NULL, st, 12, 100
FROM (VALUES ('appointment'), ('device_session')) AS v(st)
WHERE NOT EXISTS (
    SELECT 1 FROM reference."cancellation_policy_tiers" t
    WHERE t."clinic_id" IS NULL AND t."session_type" = v.st
);

-- ── 4. payment refund tracking ─────────────────────────────────────────────
ALTER TABLE core."payments" ADD COLUMN IF NOT EXISTS "refund_status" TEXT;
ALTER TABLE core."payments" ADD COLUMN IF NOT EXISTS "razorpay_refund_id" TEXT;
ALTER TABLE core."payments" ADD COLUMN IF NOT EXISTS "refunded_at" TIMESTAMPTZ;

ALTER TABLE core."payments" DROP CONSTRAINT IF EXISTS "chk_payments_refund_status";
ALTER TABLE core."payments"
    ADD CONSTRAINT "chk_payments_refund_status"
    CHECK ("refund_status" IS NULL OR "refund_status" IN ('pending', 'processed', 'failed'));

ALTER TABLE core."payments" DROP CONSTRAINT IF EXISTS "payments_razorpay_refund_id_key";
ALTER TABLE core."payments" ADD CONSTRAINT "payments_razorpay_refund_id_key" UNIQUE ("razorpay_refund_id");

CREATE INDEX IF NOT EXISTS idx_payments_refund_pending
    ON core."payments" ("payment_id")
    WHERE "refund_status" = 'pending';

COMMENT ON COLUMN core."payments"."refund_status" IS 'NULL = no refund owed. pending = owed (online: refund worker / webhook completes it, cash: front desk confirms). processed = returned, status is then refunded or partially_refunded. failed = gateway refused, see payment_logs.';
COMMENT ON COLUMN core."payments"."razorpay_refund_id" IS 'Razorpay refund id (rfnd_...) once the refund has been created at the gateway.';

-- ── 5. payment_logs status vocabulary ─────────────────────────────────────
ALTER TABLE core."payment_logs" DROP CONSTRAINT IF EXISTS "chk_payment_logs_status";
ALTER TABLE core."payment_logs"
    ADD CONSTRAINT "chk_payment_logs_status"
    CHECK ("status" IN ('pending', 'paid', 'failed', 'waived', 'refunded', 'partially_refunded'));

COMMIT;

-- VERIFY
-- SELECT column_name FROM information_schema.columns
--  WHERE table_schema='core' AND table_name='payments'
--    AND column_name IN ('refund_status','razorpay_refund_id','refunded_at');      -- 3 rows
-- SELECT session_type, min_hours_before, refund_percent
--   FROM reference.cancellation_policy_tiers WHERE clinic_id IS NULL;               -- >= 1 row per session_type
