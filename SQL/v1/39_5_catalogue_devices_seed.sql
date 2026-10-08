-- 39_5_catalogue_devices_seed.sql
--
-- The device catalogue that the tDCS seed (40) depends on. File 40's section 4 loops over
-- the ACTIVE tDCS devices in reference.neuromod_devices; its own header notes that table is
-- "populated by hand, not by any numbered migration". On a fresh database it is therefore
-- empty, the loop writes nothing, and tdcs_placements / tdcs_dosing /
-- dosing_unspecified_notes stay empty. This file creates what the previous database had
-- (exported 2026-10-08), keeping the same ids:
--   companies  Biothm, Sooma Health
--   devices    BIO-001 (Biothm tDCS), SMO-002 (Sooma tDCS System)
--   TVNS-001   already created by 93_5_tvns_device_seed.sql; named and linked to Biothm here
--
-- RUN AS TABLE OWNER (postgres), not anava_app: file 32 revokes writes on reference.* from
-- anava_app. Idempotent: ON CONFLICT DO NOTHING / guarded UPDATE.
-- Apply order: after 39, before 40. (Runs after 93_5 only for the TVNS-001 rename; if
-- TVNS-001 does not exist yet that UPDATE matches nothing, and 113 repeats it.)

BEGIN;

INSERT INTO reference.device_companies
SELECT * FROM jsonb_populate_recordset(NULL::reference.device_companies, $json$[{"company_id": "1b4e2441-61a1-4d4b-b645-1365eb78830d", "company_code": "BIOTHM", "company_name": "Biothm", "country": "India", "website": "https://biothm.example.com", "support_email": "support@biothm.example.com", "support_phone": "+91-80-4000-1001", "regulatory_ids": {"ce": "CE-2024-0011"}, "is_active": true, "notes": "Dummy seed data", "created_at": "2026-08-14T08:49:40.669398+00:00", "updated_at": "2026-08-14T08:49:40.669398+00:00"}, {"company_id": "1bb4aea1-95b8-4839-9907-6f730d842920", "company_code": "SOOMA", "company_name": "Sooma Health", "country": "USA", "website": "https://sooma.example.com", "support_email": "support@sooma.example.com", "support_phone": "+1-415-555-0142", "regulatory_ids": {"fda": "K240123"}, "is_active": true, "notes": "Dummy seed data", "created_at": "2026-08-14T08:49:40.669398+00:00", "updated_at": "2026-09-28T04:49:32.60147+00:00"}]$json$::jsonb)
ON CONFLICT DO NOTHING;

INSERT INTO reference.neuromod_devices
SELECT * FROM jsonb_populate_recordset(NULL::reference.neuromod_devices, $json$[{"device_id": "d60dd2f3-3a0f-4e83-ad53-fd4048d74cda", "company_id": "1b4e2441-61a1-4d4b-b645-1365eb78830d", "device_code": "BIO-001", "device_name": "Biothm tDCS ", "model_number": "BTM-X100", "modality": "tDCS", "phase": 1, "is_active": true, "created_at": "2026-08-14T08:49:40.669398+00:00", "updated_at": "2026-09-28T15:15:16.430968+00:00"}, {"device_id": "7f8c7484-cc6f-4f50-b945-60b826ab069b", "company_id": "1bb4aea1-95b8-4839-9907-6f730d842920", "device_code": "SMO-002", "device_name": "Sooma tDCS System", "model_number": "SMO-2200", "modality": "tDCS", "phase": 1, "is_active": true, "created_at": "2026-08-14T08:49:40.669398+00:00", "updated_at": "2026-09-28T15:15:16.498518+00:00"}]$json$::jsonb)
ON CONFLICT DO NOTHING;

COMMIT;
