-- ============================================================
-- Anava Clinic — DB Schema
-- File 47: PRS Assessment Language
--
-- With multi-language PRS content (45/46: Hindi 'hi', Marathi 'mr'),
-- record which language the patient took each assessment in.
-- Needed for clinical review (staff must see the exact wording the
-- patient answered) and for longitudinal comparison across follow-ups.
--
-- Design: one column on prs_assessment_instances — a patient takes
-- the whole assessment in one language. 'en' is the base language
-- stored directly in prs_questions/prs_options, so it is the default
-- and existing rows backfill to 'en' (all pre-translation assessments
-- were English).
--
-- No FK/enum on language_code: translation tables (45/46) use plain
-- VARCHAR codes ('hi', 'mr') with no languages master table; adding
-- a language must stay a pure data change, not a DDL change.
-- ============================================================

ALTER TABLE prs_assessment_instances
    ADD COLUMN IF NOT EXISTS language_code VARCHAR(10) NOT NULL DEFAULT 'en';

COMMENT ON COLUMN prs_assessment_instances.language_code IS
    'BCP-47-style language the patient took this assessment in (en/hi/mr). Matches prs_question_translations.language_code; en = base content in prs_questions/prs_options.';

-- Per-response language: patient may switch language mid-assessment
-- (each answer records the language of the wording actually shown).
-- Backfill 'en' for the same reason as above.
ALTER TABLE prs_responses
    ADD COLUMN IF NOT EXISTS language_code VARCHAR(10) NOT NULL DEFAULT 'en';

COMMENT ON COLUMN prs_responses.language_code IS
    'Language of the question wording shown when this answer was given (en/hi/mr). May differ from the instance language if the patient switched mid-assessment.';
