-- 60_anamnesis_question_type.sql
--
-- reference.anamnesis_questions has always been one undifferentiated list —
-- the same catalog is returned to both the registration flow and the
-- every-visit main anamnesis flow (GET /anamnesis-catalog took no stage
-- param). Product ask: split the catalog so registration only shows
-- one-time background/history questions, and the main flow (answered every
-- visit, per anamnesis_assessments.assessment_stage/version) only shows the
-- current-condition questions that actually change visit to visit.
--
-- 'registration' / 'main' matches anamnesis_assessments.assessment_stage's
-- own vocabulary (originally a 3-value general_registration/main_clinical/
-- followup borrowed from PRS via 45_, collapsed to the same 2 values here
-- by 61_ — anamnesis-only, PRS keeps its 3-value stage). This column records
-- which STAGE a QUESTION belongs to; assessment_stage records which VISIT
-- produced an answer set — same words, different axis. Both main_clinical
-- and followup assessments always pulled from the same 'main' catalog, so
-- collapsing assessment_stage's vocab to match this column's 2 values was a
-- pure rename, not a logic change.
--
-- All existing questions stay 'main' via the column DEFAULT — no backfill
-- needed. Registration questions are net-new rows, seeded separately.
--
-- APPLY ORDER: after 59. Independent of 32-59.

BEGIN;

ALTER TABLE reference."anamnesis_questions"
    ADD COLUMN IF NOT EXISTS "type" TEXT NOT NULL DEFAULT 'main'::text;

ALTER TABLE reference."anamnesis_questions"
    DROP CONSTRAINT IF EXISTS "chk_anamnesis_questions_type";

ALTER TABLE reference."anamnesis_questions"
    ADD CONSTRAINT "chk_anamnesis_questions_type" CHECK ("type" IN ('registration', 'main'));

COMMENT ON COLUMN reference."anamnesis_questions"."type" IS
    'registration: one-time background/history question, shown only during patient registration. main: current-condition question, shown every anamnesis visit after registration. Same vocabulary as anamnesis_assessments.assessment_stage (see 61_) — that column records which visit produced an answer set, this one records which stage a question belongs to.';

CREATE INDEX IF NOT EXISTS idx_anamnesis_questions_type
    ON reference."anamnesis_questions" USING btree ("type");

COMMIT;
