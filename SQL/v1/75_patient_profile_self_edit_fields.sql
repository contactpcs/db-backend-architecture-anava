-- 75_patient_profile_self_edit_fields.sql
--
-- APPLY ORDER: after 74. Depends on 05 (core.patients).
--
-- THE PROBLEM
--
-- The patient self-edit profile page (prs-neurowellness's /patient/profile)
-- has always had form fields for weight, height, a government ID and its
-- type, but no column anywhere ever backed them — PatientSelfUpdate
-- (patients/schemas.py) silently dropped any edit to those fields, and the
-- frontend's users.service.ts had no server target to map them onto either.
-- A patient editing ONLY one of these fields got a response with none of
-- their other fields touched, which — before a separate frontend fix —
-- blanked their whole profile screen on save.
--
-- blood_group, allergies, occupation, marital_status, insurance_provider,
-- insurance_policy (core.patients) and language_pref (core.profiles)
-- already existed; those were wired through the API/frontend as part of
-- this same change, no schema needed for them.
--
-- THE FIX
--
-- Five new nullable columns on core.patients for the fields that had no
-- backing column at all. Nullable and no CHECK constraints — self-reported
-- values a patient can correct themselves, not something the schema should
-- police.

BEGIN;

ALTER TABLE core."patients"
    ADD COLUMN IF NOT EXISTS "weight_kg"      NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS "height_ft"      SMALLINT,
    ADD COLUMN IF NOT EXISTS "height_in"      SMALLINT,
    ADD COLUMN IF NOT EXISTS "government_id"  TEXT,
    ADD COLUMN IF NOT EXISTS "id_type"        TEXT;

COMMENT ON COLUMN core."patients"."weight_kg" IS 'Self-reported, editable by the patient. No clinical verification implied.';
COMMENT ON COLUMN core."patients"."height_ft" IS 'Self-reported height, feet component. Paired with height_in — imperial because that is what the patient profile form collects, not metric.';
COMMENT ON COLUMN core."patients"."height_in" IS 'Self-reported height, inches component (0-11). Paired with height_ft.';
COMMENT ON COLUMN core."patients"."government_id" IS 'Self-reported government ID number (Aadhaar, passport, etc — see id_type). Not verified against any registry; a display/record-keeping field only.';
COMMENT ON COLUMN core."patients"."id_type" IS 'Which kind of ID government_id holds (e.g. aadhaar, passport, driving_license, voter_id). Free text — no fixed catalogue existed to constrain it to.';

COMMIT;
