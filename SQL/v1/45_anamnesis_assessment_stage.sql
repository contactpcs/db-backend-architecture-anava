-- 45_anamnesis_assessment_stage.sql
--
-- Anamnesis never got the stage classification PRS already has
-- (reference.prs_scales.applicable_for / core.prs_assessment_instances.
-- assessment_stage / core.patient_scale_assignments.assessment_stage): which
-- visit produced a given row, general_registration vs main_clinical.
--
-- Anamnesis already supports re-taking — repository.latest_version() plus
-- taken_by 'patient' vs 'doctor_on_behalf' (AnamnesisForm's mode="patient"
-- vs mode="doctor") — but had no column recording WHICH visit produced a
-- given version. get_current() always returns the latest row with no way
-- to tell a registration-time answer set apart from a main-clinical one.
--
-- Mirrors prs_assessment_instances exactly: TEXT NOT NULL DEFAULT
-- 'general_registration', no CHECK (v2 Layer 2: text, not enum — same
-- reason PRS's own column has none), same btree index shape as
-- idx_pai_assessment_stage.
--
-- APPLY ORDER: after 44. Independent of 32-44.

BEGIN;

ALTER TABLE core."anamnesis_assessments"
    ADD COLUMN IF NOT EXISTS "assessment_stage" TEXT NOT NULL DEFAULT 'general_registration'::text;

COMMENT ON COLUMN core."anamnesis_assessments"."assessment_stage" IS
    'Which visit this anamnesis version was taken at — general_registration (patient self-service at signup) or main_clinical (doctor_on_behalf during the main clinical visit). Mirrors prs_assessment_instances.assessment_stage.';

CREATE INDEX IF NOT EXISTS idx_anamnesis_assessment_stage
    ON core."anamnesis_assessments" USING btree ("assessment_stage");

COMMIT;
