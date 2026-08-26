-- 61_anamnesis_stage_registration_main.sql
--
-- core.anamnesis_assessments.assessment_stage (added in 45_) originally
-- mirrored prs_assessment_instances' 3-value vocabulary (general_registration/
-- main_clinical/followup) verbatim. Anamnesis-only decision: collapse to the
-- same 2 values as reference.anamnesis_questions.type (60_) — 'registration'
-- and 'main' — so the registration-record page's API can filter both the
-- question catalog AND the assessment/response fetch by one consistent word,
-- instead of the frontend having to know 'general_registration' means
-- catalog type 'registration' but 'main_clinical'/'followup' both mean
-- catalog type 'main'.
--
-- SCOPE: anamnesis only. prs_assessment_instances / patient_scale_assignments
-- keep their existing 3-value assessment_stage untouched — PRS's scale
-- assignment logic (prs/service.py) genuinely branches on all 3 stages
-- (general_registration gets a narrowed scale set; main_clinical/followup do
-- not), so collapsing there would be a real behavior change, not a rename.
-- Anamnesis has no such per-stage branching — every non-registration visit
-- already gets the identical 'main' question catalog — so collapsing here is
-- a pure rename, not a logic change.
--
-- Existing rows: 'general_registration' -> 'registration',
-- 'main_clinical' and 'followup' -> 'main'. No CHECK existed on this column
-- before (Phase A deliberately deferred it pending a value-set audit — see
-- SQL/v1/NOTES.md); this is that audit for this column, so a CHECK is added
-- here for the first time.
--
-- APPLY ORDER: after 60. Independent of 32-60 otherwise.

BEGIN;

UPDATE core."anamnesis_assessments" SET "assessment_stage" = 'registration'
    WHERE "assessment_stage" = 'general_registration';

UPDATE core."anamnesis_assessments" SET "assessment_stage" = 'main'
    WHERE "assessment_stage" IN ('main_clinical', 'followup');

ALTER TABLE core."anamnesis_assessments"
    ALTER COLUMN "assessment_stage" SET DEFAULT 'registration'::text;

ALTER TABLE core."anamnesis_assessments"
    DROP CONSTRAINT IF EXISTS "chk_anamnesis_assessments_stage";

ALTER TABLE core."anamnesis_assessments"
    ADD CONSTRAINT "chk_anamnesis_assessments_stage" CHECK ("assessment_stage" IN ('registration', 'main'));

COMMENT ON COLUMN core."anamnesis_assessments"."assessment_stage" IS
    'Which flow this anamnesis version was taken in — registration (patient self-service at signup) or main (any visit after, doctor_on_behalf or otherwise). Matches reference.anamnesis_questions.type. Renamed from the 3-value general_registration/main_clinical/followup vocab in 61_ — anamnesis only, PRS keeps the original 3-value vocab.';

COMMIT;
