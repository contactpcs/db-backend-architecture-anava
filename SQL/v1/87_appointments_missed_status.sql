-- 87_appointments_missed_status.sql
--
-- New terminal status: 'missed'. A protocol-born row (device_session /
-- protocol_followup) starts 'planned' — a doctor-set DATE with no slot and no
-- payment attempt yet (32_treatment_protocol.sql). Nothing ever swept a
-- 'planned' row whose appointment_date passed without the patient ever
-- claiming a slot: no_show_sweeper.py only watches 'paid' (slot time passed,
-- never checked in) and 'checked_in' (session never started); the hold-expiry
-- sweeper (repository.py::release_expired_holds) only watches 'selected' and,
-- for a protocol-born row, reverts it BACK to 'planned' rather than out of it.
-- The result: these rows sat in 'planned' forever, past their date, with no
-- valid transition anywhere — not reschedulable (RESCHEDULE_FROM_STATUSES
-- never included 'planned') and not re-claimable (claim_slot rejects a past
-- date), a dead end for both staff and patient.
--
-- 'missed' closes that gap: same meaning as 'no_show' (a slot that came and
-- went unattended) but for the case where payment was never even attempted.
-- Swept by no_show_sweeper.py's new third branch (app code, not this file):
-- status = 'planned' AND appointment_date < today -> 'missed'. Reschedulable
-- exactly like 'no_show' (scheduling/service.py RESCHEDULE_FROM_STATUSES).
--
-- APPLY ORDER: after 86. Depends on 43 (chk_appointments_status) and 30
-- (chk_appointments_slotted_has_time).

BEGIN;

ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "chk_appointments_status";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_status"
    CHECK ("status" IN ('planned', 'selected', 'paid', 'cancelled', 'rescheduled',
                         'checked_in', 'in_progress', 'completed', 'no_show', 'missed'));

-- A 'missed' row carries no time (it never got past 'planned' before its date
-- passed) — same as 'planned'/'cancelled' under chk_appointments_time_pair.
ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "chk_appointments_slotted_has_time";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_slotted_has_time"
    CHECK ("start_time" IS NOT NULL OR "status" IN ('planned', 'cancelled', 'missed'));

COMMENT ON COLUMN core."appointments"."status" IS 'planned -> selected -> paid -> checked_in -> in_progress -> completed, with no_show (paid/checked_in, slot time passed unattended) and missed (planned, date passed before any slot was ever claimed) as the two unattended-terminal branches, and cancelled/rescheduled as the two withdrawn branches. missed/no_show/cancelled are all reschedulable (see AppointmentService.reschedule).';

COMMIT;

-- One-time backfill, outside the transaction above only to keep that DDL lock
-- window short — matches this schema's own precedent (86's own one-time
-- backfill). Every 'planned' protocol-born row already stuck in the past
-- before this migration ships would otherwise wait for the next sweeper pass;
-- this closes it retroactively instead of leaving it dangling until then.
UPDATE core."appointments"
SET status = 'missed', updated_at = NOW()
WHERE status = 'planned' AND appointment_date < CURRENT_DATE;
