-- 55_drop_device_schedule_capacity.sql
--
-- THE PROBLEM
--
-- clinic_device_schedules.capacity (36 §1) was a separately admin-typed
-- number for how many device_session sessions can run at once. But that
-- number IS the clinic's owned unit count for the device — clinic_devices.
-- quantity (37) — in every ordinary case; a device pool with 2 physical
-- units can obviously run 2 concurrent sessions. Making the admin type a
-- second, independently-editable number for the same fact was pure
-- duplication (and the just-removed "suggested capacity" helper, 54, was
-- already trying to derive one from the other as a hint).
--
-- THE FIX
--
-- Booking logic (DeviceCapacityService._resolve_capacity) now reads
-- clinic_devices.quantity directly. The legitimate reason capacity ever
-- needed to differ from quantity — fewer units usable on a given day, e.g.
-- one out for maintenance or short-staffed — is still covered:
-- clinic_device_schedule_overrides.capacity (unchanged, still nullable,
-- NULL inherits quantity) already exists precisely for single-day
-- exceptions and stays exactly as it was.
--
-- NOT touched: clinic_device_schedule_overrides.capacity (kept, legitimate
-- per-day override), clinic_devices.quantity (the new source of truth,
-- already existed since 37).
--
-- SAFE TO APPLY: no code path reads clinic_device_schedules.capacity as of
-- this revision (confirmed via full-repo grep) — the admin device-schedule
-- form's capacity input is removed in the same change.

BEGIN;

ALTER TABLE core."clinic_device_schedules" DROP CONSTRAINT IF EXISTS "chk_cds_capacity_positive";
ALTER TABLE core."clinic_device_schedules" DROP COLUMN IF EXISTS "capacity";

COMMIT;
