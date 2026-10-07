-- 97_device_catalogue_admin_writes.sql
--
-- Lets super_admin maintain the device catalogue (manufacturers + device
-- models) through the application instead of hand-run SQL against prod.
--
-- APPLY ORDER: after 96. Depends on 32 (tables, RLS policies, grants).
--
--
-- ###########################################################################
-- WHY
-- ###########################################################################
--
-- 32 §11 revoked INSERT/UPDATE/DELETE on every reference.* catalogue table
-- from anava_app, so an application bug could never silently alter a
-- prescribed dose or montage. That reasoning is right for the dosing and
-- placement tables and stays in force for them — this file does not touch
-- them.
--
-- It was never the right reasoning for reference.device_companies and
-- reference.neuromod_devices: these are the vendor/model registry, not a
-- dose. The consequence of leaving them locked was that every device and
-- manufacturer in the system (BIO-001, MRB-002, SMA-003, TVNS-001, the
-- BIOTHM/SOOMA companies) had to be inserted by hand directly against prod
-- (see 93_5_tvns_device_seed.sql's header for one instance of the drift
-- that caused), with no audit of who added what.
--
-- 32 already wrote RLS INSERT/UPDATE policies on both tables restricted to
-- rls_user_role() = 'super_admin' — the row-level half of this was designed
-- in from the start; only the table-level GRANT was missing. Granting it
-- here makes those existing policies reachable. Nothing else can write:
-- every other role still fails the RLS WITH CHECK.
--
-- DELETE stays revoked. Both tables are Retention Bucket 3 (a historic
-- protocol must still name the device and vendor that delivered it) — a
-- device is retired with is_active = false, never deleted. Every FK into
-- neuromod_devices is ON DELETE RESTRICT anyway.

GRANT INSERT, UPDATE ON
    reference."device_companies",
    reference."neuromod_devices"
    TO anava_app;
