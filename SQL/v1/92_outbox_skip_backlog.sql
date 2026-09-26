-- 92_outbox_skip_backlog.sql
--
-- Marks every undelivered outbox event as published WITHOUT notifying
-- anyone, so switching the event relay on (app/main.py lifespan) starts from
-- a clean queue instead of sending a burst of stale notifications for
-- months-old bookings and cancellations.
--
-- SCOPE: one UPDATE on ops.outbox_events. No schema change.
--
-- APPLY ORDER: after 91, and BEFORE deploying the backend that starts the
-- relay — applied after, the relay would already be sending the backlog.
--
-- Why: the relay (app/workers/event_relay.py) was built but never started,
-- so nothing ever marked these rows published (see
-- Documents/DATA_CAPTURE_AUDIT_2026-09-21.md, DC-01). Decision 2026-09-26:
-- skip the backlog, notify only for events from now on.

UPDATE ops."outbox_events"
SET "published_at" = now()
WHERE "published_at" IS NULL;


-- VERIFY
--  SELECT count(*) FROM ops.outbox_events WHERE published_at IS NULL;   -- 0 right after apply
