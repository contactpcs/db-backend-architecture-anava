-- 109_clinic_google_maps_url.sql
--
-- core.clinics gains three columns:
--   full_address     the complete postal address as one block of text. Kept
--                    alongside the existing address / city / state / country
--                    columns (05_tables_core.sql), which are unchanged.
--   pincode          postal code. Free text, same as core.profiles.pincode.
--   google_maps_url  the "open in Google Maps" link shown next to the address.
--
-- Read by patients through GET /patients/{patient_id}/clinic.
--
-- https-only check on google_maps_url: the value is rendered as a link, so a
-- scheme such as javascript: must never be storable, whichever code path
-- writes it. An empty string is allowed: that is how the edit form clears the
-- link, same as it clears address (PATCH /clinics/{id} skips NULLs).
--
-- No RLS change needed: rls_clinics_update (17_rls_policies.sql) already lets
-- a clinic_admin update their own clinic row, a regional_admin their region's
-- and a super_admin any. Same reasoning as is_operational in 65.
--
-- APPLY ORDER: after 108, and BEFORE deploying the backend that writes them
-- (POST /clinics inserts all three). Nullable, so no existing row changes.

ALTER TABLE core."clinics"
    ADD COLUMN IF NOT EXISTS "full_address" TEXT,
    ADD COLUMN IF NOT EXISTS "pincode" TEXT,
    ADD COLUMN IF NOT EXISTS "google_maps_url" TEXT;

ALTER TABLE core."clinics" DROP CONSTRAINT IF EXISTS "chk_clinics_google_maps_url";
ALTER TABLE core."clinics" ADD CONSTRAINT "chk_clinics_google_maps_url"
    CHECK ("google_maps_url" IS NULL OR "google_maps_url" = '' OR "google_maps_url" ~ '^https://');

COMMENT ON COLUMN core."clinics"."full_address" IS 'Complete postal address of the clinic as one block of text. Editable by the clinic_admin of the clinic (rls_clinics_update).';

COMMENT ON COLUMN core."clinics"."pincode" IS 'Postal code of the clinic. Editable by the clinic_admin of the clinic (rls_clinics_update).';

COMMENT ON COLUMN core."clinics"."google_maps_url" IS 'Google Maps link for the clinic location, shown with the address. https only. Editable by the clinic_admin of the clinic (rls_clinics_update).';
