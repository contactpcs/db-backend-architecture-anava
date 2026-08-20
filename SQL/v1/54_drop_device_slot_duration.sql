-- 54_drop_device_slot_duration.sql
--
-- THE PROBLEM
--
-- clinic_device_schedules.slot_duration_minutes (36 §1, re-keyed per-device
-- by 41) chunked each device's day into fixed-size slots — an admin-set
-- device property, same for every patient on that device that day. Actual
-- device_session length varies per patient/protocol (30 min vs 2 hr), and
-- that variation lives in reference.billable_items.duration_minutes (what's
-- actually being paid for, 30 §5) — which booking never read at all. The
-- slot picker's numbers never matched what the patient was actually booking.
--
-- THE FIX
--
-- Booking logic (app/modules/scheduling/service.py, DeviceCapacityService)
-- now resolves session length from billable_items.duration_minutes and
-- checks device capacity with a real time-range overlap query instead of a
-- fixed-chunk exact-match. slot_duration_minutes has no reader left anywhere
-- in the codebase (confirmed: only the admin device-schedule CRUD screen
-- displayed it, and only as a "suggested capacity" planning aid — dropped
-- too, per the same decision, so admin capacity is a direct entered number).
--
-- NOT the doctor calendar's own slot_duration_minutes (doctor_weekly_schedules)
-- — same column name, different table, untouched by this file.
--
-- SAFE TO APPLY: no code path reads this column for booking or capacity
-- planning as of this revision. The admin device-schedule form and its
-- "N-minute slots" display text are removed in the same change.

BEGIN;

ALTER TABLE core."clinic_device_schedules" DROP CONSTRAINT IF EXISTS "chk_cds_slot_duration";
ALTER TABLE core."clinic_device_schedules" DROP COLUMN IF EXISTS "slot_duration_minutes";

COMMIT;
