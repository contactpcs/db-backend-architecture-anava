-- 102_outbox_retry_and_notification_read.sql
--
-- Makes the outbox relay (app/workers/event_relay.py) write notifications
-- again, and retry a failed event instead of silently dropping it
-- (design: Documents/design_outbox_relay_reliability.md).
--
-- SCOPE: one new policy on core.notifications, three new columns and one
-- rebuilt index on ops.outbox_events. No data change.
--
-- APPLY ORDER: after 101, and BEFORE deploying the relay that reads the new
-- columns. Safe under the old relay: the columns have defaults and the policy
-- only adds access.
--
--
-- WHY
-- 1. SQL/v1/93 let RLS role 'system' INSERT into core.notifications but not
--    SELECT from it. The relay inserts with RETURNING, which needs both, so
--    every notification it wrote was rejected ("new row violates row-level
--    security policy"). No notification has been saved since the relay moved
--    to the app login on 2026-09-28.
-- 2. The relay marked a failed event published anyway, so that failure was
--    silent and permanent. An event now stays unpublished and is retried with
--    a growing delay. After the last attempt failed_at is set and it is left
--    alone, so one bad event never blocks the events behind it.


-- A separate PERMISSIVE policy, same pattern as SQL/v1/93: every existing
-- role's access is unchanged and 'system' gains read access.
DROP POLICY IF EXISTS "rls_notif_select_system" ON core."notifications";
CREATE POLICY "rls_notif_select_system" ON core."notifications" FOR SELECT TO public
    USING (rls_user_role() = 'system');


-- next_attempt_at  earliest time the relay may process the event
-- failed_at        set when the relay gives up on the event
-- last_error       most recent failure, for whoever investigates it
ALTER TABLE ops."outbox_events"
    ADD COLUMN IF NOT EXISTS "next_attempt_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS "failed_at" TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS "last_error" TEXT;


-- The relay's queue: only events still waiting, so the index stays small
-- however many events the table holds.
DROP INDEX IF EXISTS ops."idx_outbox_unpublished";
CREATE INDEX "idx_outbox_unpublished" ON ops."outbox_events" USING btree (created_at)
    WHERE (published_at IS NULL AND failed_at IS NULL);


-- VERIFY
--  SELECT policyname FROM pg_policies WHERE tablename = 'notifications';   -- includes rls_notif_select_system
--  SELECT count(*) FROM ops.outbox_events WHERE failed_at IS NOT NULL;      -- 0 right after apply
--
-- INSPECT events the relay gave up on
--  SELECT outbox_id, event_type, publish_attempts, failed_at, last_error
--  FROM ops.outbox_events WHERE failed_at IS NOT NULL ORDER BY failed_at DESC;
--
-- REPLAY one of them once the cause is fixed
--  UPDATE ops.outbox_events SET failed_at = NULL, publish_attempts = 0, next_attempt_at = now()
--  WHERE outbox_id = '<id>';
