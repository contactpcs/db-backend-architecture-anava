-- 64_fee_breakdown_and_cancellation_policy.sql
--
-- APPLY ORDER: after 63. Independent of 60-63 (anamnesis work).
--
-- THE PROBLEM
--
-- core.payments.amount is a single flat number — no breakdown into
-- consultation/session fee vs. platform convenience fee, and no cancellation
-- penalty concept exists anywhere (scheduling/service.py's CANCEL_MIN_HOURS
-- is a hard binary block: refuse the cancellation outright inside 2 hours,
-- never a partial-refund tier). Mohan's 27 Aug 2026 spec:
--   - base fee (consultation_fee / session_fee) already has a home:
--     reference.billable_items.price, per clinic_id override — reused as-is,
--     not duplicated.
--   - platform/convenience fee: a single percentage of the base fee, same
--     value for every clinic (confirmed explicitly — not a per-clinic
--     override like billable_items). Split by session_type (appointment vs
--     device_session) since the two could reasonably diverge later.
--   - cancellation penalty: tiered by hours-before-appointment, refund
--     percent per tier, SEPARATE tier sets per session_type (confirmed).
--     Per-clinic override allowed (super admin manages per-clinic, same
--     override pattern as billable_items) — platform default when no
--     clinic-specific row exists.
--   - reschedule penalties: explicitly out of scope for this pass.
--
-- THE FIX
--
-- 1. reference.platform_fee_config — one row per session_type, percent of
--    base fee. Global, no clinic_id.
-- 2. reference.cancellation_policy_tiers — tier_id, clinic_id (NULL =
--    platform default), session_type, min_hours_before, refund_percent.
--    Resolution (app-side, same idea as billable_items.resolve_price): among
--    the tiers for the winning scope (clinic override if any exist, else
--    platform default), pick the highest min_hours_before that is <= the
--    actual hours-until-appointment; if the actual hours fall below every
--    tier's min_hours_before, refund is 0%. Super admin is expected to seed
--    a min_hours_before = 0 floor row explicitly — not enforced by a DB
--    constraint (would need a trigger for one row's value to depend on the
--    rest of the set); the resolver's 0%-if-unmatched fallback covers the
--    case where they don't.
-- 3. core.payments gains snapshot columns: base_fee_amount,
--    platform_fee_percent, platform_fee_amount (computed once at order
--    creation and stored — later fee-config changes never retroactively
--    alter a payment already created, confirmed explicitly), plus
--    cancellation_refund_percent / cancellation_refund_amount (filled in at
--    cancellation time). `amount` keeps its existing meaning (the total
--    charged = base_fee_amount + platform_fee_amount going forward) — every
--    existing reader of `amount` keeps working unchanged.
--
-- NOT DONE HERE (explicitly out of scope, not an oversight):
--   - No gateway refund call. cancellation_refund_amount is computed and
--     stored so finance can see what's owed; actually moving money back to
--     the patient is still a manual step, same as the existing "no refund
--     path — out of scope by decision" note in
--     Documents/Anava_Appointments_Payment_Flow_Redesign_v1.docx. Automating
--     the Razorpay refund call is a separate decision.
--   - Reschedule penalties — no schema for these at all yet, per the
--     explicit "hold that" instruction.

BEGIN;

-- ── 1. platform fee (global, percent of base fee) ──────────────────────────

CREATE TABLE IF NOT EXISTS reference."platform_fee_config" (
    "session_type" TEXT NOT NULL,
    "fee_percent" NUMERIC(5,2) NOT NULL DEFAULT 0,
    "updated_by" UUID,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE reference."platform_fee_config" DROP CONSTRAINT IF EXISTS "platform_fee_config_pkey" CASCADE;
ALTER TABLE reference."platform_fee_config" ADD CONSTRAINT "platform_fee_config_pkey" PRIMARY KEY ("session_type");

ALTER TABLE reference."platform_fee_config" DROP CONSTRAINT IF EXISTS "chk_platform_fee_config_session_type";
ALTER TABLE reference."platform_fee_config"
    ADD CONSTRAINT "chk_platform_fee_config_session_type"
    CHECK ("session_type" IN ('appointment', 'device_session'));

ALTER TABLE reference."platform_fee_config" DROP CONSTRAINT IF EXISTS "chk_platform_fee_config_percent_range";
ALTER TABLE reference."platform_fee_config"
    ADD CONSTRAINT "chk_platform_fee_config_percent_range"
    CHECK ("fee_percent" >= 0 AND "fee_percent" <= 100);

ALTER TABLE reference."platform_fee_config" DROP CONSTRAINT IF EXISTS "fk_platform_fee_config_updated_by";
ALTER TABLE reference."platform_fee_config"
    ADD CONSTRAINT "fk_platform_fee_config_updated_by"
    FOREIGN KEY ("updated_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

-- Seeded at 0% for both — super admin sets the real values from the admin
-- portal. A missing row would make resolution ambiguous (0% vs "not
-- configured"); seeding both up front means "not yet configured" and "0%"
-- are the same state on purpose, and every lookup always finds a row.
INSERT INTO reference."platform_fee_config" ("session_type", "fee_percent")
VALUES ('appointment', 0), ('device_session', 0)
ON CONFLICT ("session_type") DO NOTHING;

DROP TRIGGER IF EXISTS trg_updated_at_platform_fee_config ON reference."platform_fee_config";
CREATE TRIGGER trg_updated_at_platform_fee_config
    BEFORE UPDATE ON reference."platform_fee_config"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();

ALTER TABLE reference."platform_fee_config" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."platform_fee_config" FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_platform_fee_config_select" ON reference."platform_fee_config";
CREATE POLICY "rls_platform_fee_config_select" ON reference."platform_fee_config" FOR SELECT TO public
    USING (true);

DROP POLICY IF EXISTS "rls_platform_fee_config_update" ON reference."platform_fee_config";
CREATE POLICY "rls_platform_fee_config_update" ON reference."platform_fee_config" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

GRANT SELECT ON reference."platform_fee_config" TO anava_app;
GRANT UPDATE ON reference."platform_fee_config" TO anava_app;
GRANT SELECT ON reference."platform_fee_config" TO anava_readonly;

-- ── 2. cancellation policy tiers (per clinic override, per session_type) ──

CREATE TABLE IF NOT EXISTS reference."cancellation_policy_tiers" (
    "tier_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_id" UUID,
    "session_type" TEXT NOT NULL,
    "min_hours_before" NUMERIC(6,2) NOT NULL,
    "refund_percent" NUMERIC(5,2) NOT NULL,
    "created_by" UUID,
    "updated_by" UUID,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE reference."cancellation_policy_tiers" DROP CONSTRAINT IF EXISTS "cancellation_policy_tiers_pkey" CASCADE;
ALTER TABLE reference."cancellation_policy_tiers" ADD CONSTRAINT "cancellation_policy_tiers_pkey" PRIMARY KEY ("tier_id");

ALTER TABLE reference."cancellation_policy_tiers" DROP CONSTRAINT IF EXISTS "chk_cancellation_tiers_session_type";
ALTER TABLE reference."cancellation_policy_tiers"
    ADD CONSTRAINT "chk_cancellation_tiers_session_type"
    CHECK ("session_type" IN ('appointment', 'device_session'));

ALTER TABLE reference."cancellation_policy_tiers" DROP CONSTRAINT IF EXISTS "chk_cancellation_tiers_hours_non_negative";
ALTER TABLE reference."cancellation_policy_tiers"
    ADD CONSTRAINT "chk_cancellation_tiers_hours_non_negative"
    CHECK ("min_hours_before" >= 0);

ALTER TABLE reference."cancellation_policy_tiers" DROP CONSTRAINT IF EXISTS "chk_cancellation_tiers_percent_range";
ALTER TABLE reference."cancellation_policy_tiers"
    ADD CONSTRAINT "chk_cancellation_tiers_percent_range"
    CHECK ("refund_percent" >= 0 AND "refund_percent" <= 100);

ALTER TABLE reference."cancellation_policy_tiers" DROP CONSTRAINT IF EXISTS "fk_cancellation_tiers_clinic_id";
ALTER TABLE reference."cancellation_policy_tiers"
    ADD CONSTRAINT "fk_cancellation_tiers_clinic_id"
    FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE CASCADE;

ALTER TABLE reference."cancellation_policy_tiers" DROP CONSTRAINT IF EXISTS "fk_cancellation_tiers_created_by";
ALTER TABLE reference."cancellation_policy_tiers"
    ADD CONSTRAINT "fk_cancellation_tiers_created_by"
    FOREIGN KEY ("created_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

ALTER TABLE reference."cancellation_policy_tiers" DROP CONSTRAINT IF EXISTS "fk_cancellation_tiers_updated_by";
ALTER TABLE reference."cancellation_policy_tiers"
    ADD CONSTRAINT "fk_cancellation_tiers_updated_by"
    FOREIGN KEY ("updated_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

-- Same split-partial-index pattern as billable_items (53) — a plain UNIQUE
-- on (clinic_id, session_type, min_hours_before) would let unlimited
-- duplicate platform-default (clinic_id IS NULL) rows through, since NULL
-- is never equal to NULL.
CREATE UNIQUE INDEX IF NOT EXISTS uq_cancellation_tiers_default
    ON reference."cancellation_policy_tiers" ("session_type", "min_hours_before")
    WHERE "clinic_id" IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_cancellation_tiers_clinic
    ON reference."cancellation_policy_tiers" ("clinic_id", "session_type", "min_hours_before")
    WHERE "clinic_id" IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_cancellation_tiers_clinic_id ON reference."cancellation_policy_tiers" USING btree ("clinic_id");

DROP TRIGGER IF EXISTS trg_updated_at_cancellation_policy_tiers ON reference."cancellation_policy_tiers";
CREATE TRIGGER trg_updated_at_cancellation_policy_tiers
    BEFORE UPDATE ON reference."cancellation_policy_tiers"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();

ALTER TABLE reference."cancellation_policy_tiers" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."cancellation_policy_tiers" FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_cancellation_policy_tiers_select" ON reference."cancellation_policy_tiers";
CREATE POLICY "rls_cancellation_policy_tiers_select" ON reference."cancellation_policy_tiers" FOR SELECT TO public
    USING (true);

DROP POLICY IF EXISTS "rls_cancellation_policy_tiers_insert" ON reference."cancellation_policy_tiers";
CREATE POLICY "rls_cancellation_policy_tiers_insert" ON reference."cancellation_policy_tiers" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_cancellation_policy_tiers_update" ON reference."cancellation_policy_tiers";
CREATE POLICY "rls_cancellation_policy_tiers_update" ON reference."cancellation_policy_tiers" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_cancellation_policy_tiers_delete" ON reference."cancellation_policy_tiers";
CREATE POLICY "rls_cancellation_policy_tiers_delete" ON reference."cancellation_policy_tiers" FOR DELETE TO public
    USING ((rls_user_role() = 'super_admin'::text));

GRANT SELECT ON reference."cancellation_policy_tiers" TO anava_app;
GRANT INSERT, UPDATE, DELETE ON reference."cancellation_policy_tiers" TO anava_app;
GRANT SELECT ON reference."cancellation_policy_tiers" TO anava_readonly;

-- No seed rows — same reasoning as billable_items (30 §5): a made-up
-- cancellation policy is worse than none. Super admin sets real tiers.

-- ── 3. payments snapshot columns ────────────────────────────────────────

ALTER TABLE core."payments" ADD COLUMN IF NOT EXISTS "base_fee_amount" NUMERIC(10,2);
ALTER TABLE core."payments" ADD COLUMN IF NOT EXISTS "platform_fee_percent" NUMERIC(5,2);
ALTER TABLE core."payments" ADD COLUMN IF NOT EXISTS "platform_fee_amount" NUMERIC(10,2);
ALTER TABLE core."payments" ADD COLUMN IF NOT EXISTS "cancellation_refund_percent" NUMERIC(5,2);
ALTER TABLE core."payments" ADD COLUMN IF NOT EXISTS "cancellation_refund_amount" NUMERIC(10,2);

COMMENT ON COLUMN core."payments"."base_fee_amount" IS 'Snapshot of billable_items.price at order-creation time. NULL on store-order payments (no fee breakdown for those) and on any payment created before this column existed.';
COMMENT ON COLUMN core."payments"."platform_fee_amount" IS 'Snapshot = base_fee_amount * platform_fee_percent / 100 at order-creation time. amount = base_fee_amount + platform_fee_amount going forward.';
COMMENT ON COLUMN core."payments"."cancellation_refund_amount" IS 'Computed from cancellation_policy_tiers at the moment the linked appointment is cancelled. Not a gateway refund — the actual money movement back to the patient is still a manual step.';

ALTER TABLE core."payments" DROP CONSTRAINT IF EXISTS "chk_payments_base_fee_non_negative";
ALTER TABLE core."payments"
    ADD CONSTRAINT "chk_payments_base_fee_non_negative"
    CHECK ("base_fee_amount" IS NULL OR "base_fee_amount" >= 0);

ALTER TABLE core."payments" DROP CONSTRAINT IF EXISTS "chk_payments_platform_fee_percent_range";
ALTER TABLE core."payments"
    ADD CONSTRAINT "chk_payments_platform_fee_percent_range"
    CHECK ("platform_fee_percent" IS NULL OR ("platform_fee_percent" >= 0 AND "platform_fee_percent" <= 100));

ALTER TABLE core."payments" DROP CONSTRAINT IF EXISTS "chk_payments_platform_fee_amount_non_negative";
ALTER TABLE core."payments"
    ADD CONSTRAINT "chk_payments_platform_fee_amount_non_negative"
    CHECK ("platform_fee_amount" IS NULL OR "platform_fee_amount" >= 0);

ALTER TABLE core."payments" DROP CONSTRAINT IF EXISTS "chk_payments_cancellation_refund_percent_range";
ALTER TABLE core."payments"
    ADD CONSTRAINT "chk_payments_cancellation_refund_percent_range"
    CHECK ("cancellation_refund_percent" IS NULL OR ("cancellation_refund_percent" >= 0 AND "cancellation_refund_percent" <= 100));

ALTER TABLE core."payments" DROP CONSTRAINT IF EXISTS "chk_payments_cancellation_refund_amount_non_negative";
ALTER TABLE core."payments"
    ADD CONSTRAINT "chk_payments_cancellation_refund_amount_non_negative"
    CHECK ("cancellation_refund_amount" IS NULL OR "cancellation_refund_amount" >= 0);

COMMIT;
