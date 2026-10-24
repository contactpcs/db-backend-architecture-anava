-- 124_protocol_sessions_per_day.sql
--
-- APPLY ORDER: after 123.
--
-- protocol_plan.sessions_per_day: how many device sessions one patient may
-- have in a single day under this protocol, set by the prescribing doctor.
-- Until now the course allowed exactly one (AppointmentService.
-- assert_device_sessions_per_day, design D6-D7); the doctor can now prescribe
-- 1..4. The schedule generator plans this many sessions on each treatment day
-- and booking/rescheduling refuses one more than it.
--
-- Existing protocols keep today's behaviour through the DEFAULT 1.
-- The ceiling must match MAX_SESSIONS_PER_DAY in
-- backend/app/modules/treatment_protocols/schemas.py.
--
-- Safe to re-run.

BEGIN;

ALTER TABLE core."protocol_plan" ADD COLUMN IF NOT EXISTS "sessions_per_day" INTEGER NOT NULL DEFAULT 1;

ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_protocol_plan_sessions_per_day";
ALTER TABLE core."protocol_plan"
    ADD CONSTRAINT "chk_protocol_plan_sessions_per_day" CHECK ("sessions_per_day" BETWEEN 1 AND 4);

COMMENT ON COLUMN core."protocol_plan"."sessions_per_day" IS
    'Doctor-prescribed maximum device sessions per day for this course (1-4). The schedule plans this many per treatment day, booking refuses more on one day. 1 = the original one-session-per-day rule.';

COMMIT;

-- Verify:
-- SELECT column_name, column_default, is_nullable FROM information_schema.columns
--  WHERE table_schema='core' AND table_name='protocol_plan' AND column_name='sessions_per_day'; -- 1 row, default 1, NO
