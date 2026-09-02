-- 72_device_session_early_completion.sql
--
-- APPLY ORDER: after 71. Depends on 56 (core.device_sessions).
--
-- THE PROBLEM
--
-- device_sessions.service.py's complete() only checks session_status ==
-- 'in_progress' — no server-side duration/percentage check exists at all.
-- The 100% timer gate lives purely client-side (live/page.tsx's canComplete),
-- so a CA workaround (devtools, a slow network, a stale build) can call
-- POST /device-sessions/{id}/complete at any elapsed time with no record of
-- why. Clinical requirement: a CA may mark a session complete once at least
-- 75% of the prescribed duration has elapsed AND the patient is stable, but
-- that judgment call must be captured, not silently allowed.
--
-- THE FIX
--
-- One nullable override column, same pattern as payment_override_reason
-- (56_device_session_records.sql:152/196) — required by the API only when
-- completion happens before the full prescribed duration, not enforced as a
-- CHECK because "patient is stable, stopping a bit early" is a judgment call
-- the checklist records, not a rule the schema should block.

BEGIN;

ALTER TABLE core."device_sessions"
    ADD COLUMN IF NOT EXISTS "early_completion_override_reason" TEXT;

COMMENT ON COLUMN core."device_sessions"."early_completion_override_reason" IS
    'Set only when a CA completes the session before the prescribed duration has fully elapsed. Required by the API whenever elapsed time is under the 75% threshold (see device_sessions/service.py complete()); NULL for a normal full-duration completion.';

COMMIT;
