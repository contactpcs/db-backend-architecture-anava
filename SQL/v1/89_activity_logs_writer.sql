-- 89_activity_logs_writer.sql
--
-- compliance.activity_logs has been fully provisioned since the original
-- schema dump (partitions through 2027, FKs, indexes, RLS) but had zero
-- writers anywhere in app/ — confirmed by a repo-wide grep. Architecture doc
-- (Anava_Backend_Architecture_v1.md section 6) always intended core/events.py
-- ::emit_event() to be the single write-side helper for both outbox_events
-- (relay/notifications) and activity_logs (compliance/reporting projection);
-- only the first half was ever implemented. This migration's companion app
-- change (core/events.py) adds the second INSERT.
--
-- actor_id was NOT NULL + FK'd to profiles, which a worker-originated event
-- (hold_sweeper, no_show_sweeper — no logged-in user, no profile to
-- reference) can never satisfy. appointment_audit_logs.changed_by is already
-- nullable for the exact same reason (system-authored rows carry no actor).
-- Same fix here rather than inventing a synthetic "system" profile row.
--
-- APPLY ORDER: after 88.

ALTER TABLE compliance."activity_logs" ALTER COLUMN "actor_id" DROP NOT NULL;

COMMENT ON COLUMN compliance."activity_logs"."actor_id" IS 'NULL for a worker/sweeper-originated event (no logged-in user) — actor_role carries ''system'' in that case. Set from RequestContext.user_id for every real HTTP request (core/events.py::emit_event).';
