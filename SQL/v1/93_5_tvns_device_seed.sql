-- 93_5_tvns_device_seed.sql
--
-- 90_tvns_device.sql deliberately shipped no seed data. The tVNS device row
-- (TVNS-001) was instead inserted by hand directly against prod, so any
-- other environment (CI, a fresh DB) replaying SQL/v1/*.sql in order never
-- gets it - and 94/95's dosing presets hardcode its id and fail FK/modality
-- checks there:
--   ERROR: tvns_placements expects a tVNS device, but device
--   d97db782-e4fc-4884-89a5-d8e8f6f86655 is <missing>
-- This backfills that row, id pinned to match prod exactly, idempotent via
-- ON CONFLICT so re-running (or running on prod, where it already exists)
-- is a no-op.

BEGIN;

INSERT INTO reference.neuromod_devices
    (device_id, company_id, device_code, device_name, model_number, modality, phase, is_active)
VALUES
    ('d97db782-e4fc-4884-89a5-d8e8f6f86655', NULL, 'TVNS-001', 'tVNS Device', NULL, 'tVNS', 1, true)
ON CONFLICT (device_id) DO NOTHING;

COMMIT;
