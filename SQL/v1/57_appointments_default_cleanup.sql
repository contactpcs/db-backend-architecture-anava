-- 57_appointments_default_cleanup.sql
--
-- Drops the two orphan column DEFAULTs on core.appointments that fall
-- outside chk_appointments_appointment_type / chk_appointments_status (43).
--
-- SCOPE: two ALTER COLUMN ... DROP DEFAULT. No data changes, no new
-- constraints, no application code changes needed.
--
-- APPLY ORDER: after 56 (any of them — independent of the numbering
-- collision at 54/55/56, touches neither of those files' tables).
--
-- SAFE TO APPLY: appointment_type and status are both NOT NULL and every
-- Python INSERT into appointments (scheduling/service.py,
-- treatment_protocols/service.py — the only two writers, confirmed by
-- grep) always supplies both columns explicitly. No code path has ever hit
-- these column defaults; dropping them changes nothing observable, it just
-- stops a manual/future INSERT that omits either column from immediately
-- violating the CHECK it would otherwise silently fall back into.
--
--
-- ###########################################################################
-- WHY
-- ###########################################################################
--
-- 05_tables_core.sql created appointments with
-- appointment_type DEFAULT 'initial_assessment' and status DEFAULT
-- 'scheduled' — values from the pre-30 vocabulary. 30_appointments_spine.sql
-- deferred adding CHECK constraints on these two columns specifically
-- because the running code at the time wrote inconsistent literals; 43_
-- mock_payment_lifecycle_lock.sql later added chk_appointments_appointment_
-- type (allowing initial/follow_up/device_session/protocol_followup) and
-- chk_appointments_status (allowing planned/selected/paid/cancelled/
-- rescheduled/checked_in/in_progress/completed/no_show) once the code had
-- converged. Neither 43 nor anything since touched the column DEFAULTs
-- themselves — 'initial_assessment' and 'scheduled' are not in either
-- CHECK's allowed set, so they are now a landmine rather than a fallback:
-- any future INSERT that forgets to set one of these two columns gets a
-- constraint violation instead of the historical "scheduled"/
-- "initial_assessment" placeholder the DEFAULT clause implies is safe.
-- Dropping the DEFAULT makes that failure mode explicit (NOT NULL violation
-- naming the missing column) instead of misleading (a DEFAULT clause that
-- can never actually satisfy the table's own CHECK).
--
--
BEGIN;

ALTER TABLE core."appointments" ALTER COLUMN "appointment_type" DROP DEFAULT;
ALTER TABLE core."appointments" ALTER COLUMN "status" DROP DEFAULT;

COMMIT;


-- ###########################################################################
-- VERIFY — run after COMMIT
-- ###########################################################################
--
-- SELECT column_default FROM information_schema.columns
--  WHERE table_schema='core' AND table_name='appointments'
--    AND column_name IN ('appointment_type', 'status');
--                                                          -- both NULL
