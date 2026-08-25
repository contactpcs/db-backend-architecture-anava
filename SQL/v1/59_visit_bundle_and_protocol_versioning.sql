-- 59_visit_bundle_and_protocol_versioning.sql
--
-- Ties anamnesis and PRS assessment instances to the specific appointment
-- that produced them, and gives core.protocol_plan a major.minor version
-- number driven by the supersedes_protocol_id chain that 39 added but the
-- app never wired up.
--
-- SCOPE: two new nullable columns (anamnesis_assessments.appointment_id,
-- prs_assessment_instances.appointment_id), two new columns on protocol_plan
-- (version_major, version_minor). ADDITIVE ONLY.
--
-- APPLY ORDER: after 58.
--
-- NOT YET EXECUTED ANYWHERE.
--
-- Design doc: Documents/Anava_Visit_Bundle_And_Protocol_Versioning_v1.md
--
--
-- ###########################################################################
-- WHY
-- ###########################################################################
--
-- Doctor portal shows a toggle strip per patient visit (Initial Consultation,
-- Follow-up 1, Follow-up 2...). Anamnesis and PRS assessment instances are
-- stored patient-wide today with no record of which visit produced them, so
-- there is no way to answer "what was captured at Follow-up 1 specifically."
--
-- protocol_plan.supersedes_protocol_id (39) already models an amendment
-- pointing back at what it replaced, but nothing ever turned that chain into
-- a number a doctor can read. version_major/version_minor do that: a new
-- protocol lineage bumps major, an amendment (supersedes_protocol_id set)
-- bumps minor within the current lineage.
--
--
BEGIN;


-- ###########################################################################
-- 1  Visit linkage
-- ###########################################################################
-- Mirrors protocol_plan.authored_in_appointment_id (39) exactly: provenance
-- only, nullable, same ON DELETE RESTRICT (an appointment with clinical data
-- hanging off it should not vanish out from under that data).

ALTER TABLE core."anamnesis_assessments"
    ADD COLUMN IF NOT EXISTS "appointment_id" UUID;

COMMENT ON COLUMN core."anamnesis_assessments"."appointment_id" IS
    'The visit during which this anamnesis was captured/edited. Nullable: every row before this migration predates the column, and an anamnesis may still be taken outside a booked visit.';

ALTER TABLE core."anamnesis_assessments"
    DROP CONSTRAINT IF EXISTS "fk_anamnesis_assessments_appointment_id";
ALTER TABLE core."anamnesis_assessments"
    ADD CONSTRAINT "fk_anamnesis_assessments_appointment_id"
    FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT
    NOT VALID;

CREATE INDEX IF NOT EXISTS idx_anamnesis_assessments_appointment
    ON core."anamnesis_assessments" USING btree ("appointment_id")
    WHERE "appointment_id" IS NOT NULL;

ALTER TABLE core."prs_assessment_instances"
    ADD COLUMN IF NOT EXISTS "appointment_id" UUID;

COMMENT ON COLUMN core."prs_assessment_instances"."appointment_id" IS
    'The visit at which this PRS instance was assigned. Distinct from session_id (a specific device session) — this answers "which visit" for any appointment type, including a plain follow-up with no device session at all. Nullable: every row before this migration predates the column.';

ALTER TABLE core."prs_assessment_instances"
    DROP CONSTRAINT IF EXISTS "fk_prs_assessment_instances_appointment_id";
ALTER TABLE core."prs_assessment_instances"
    ADD CONSTRAINT "fk_prs_assessment_instances_appointment_id"
    FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT
    NOT VALID;

CREATE INDEX IF NOT EXISTS idx_prs_assessment_instances_appointment
    ON core."prs_assessment_instances" USING btree ("appointment_id")
    WHERE "appointment_id" IS NOT NULL;


-- ###########################################################################
-- 2  Protocol major.minor versioning
-- ###########################################################################
-- major: which lineage. A protocol authored with no supersedes_protocol_id
-- starts a new lineage and gets the next major within its instance_id.
-- minor: how many times the current lineage has been amended. An amendment
-- (supersedes_protocol_id set) inherits its target's major and adds one to
-- its minor. Both default to the root value (1, 0) so every pre-migration
-- row reads as version "1" — no lineage recoverable further back than that,
-- documented in the design doc as a known limitation, not backfilled.

ALTER TABLE core."protocol_plan"
    ADD COLUMN IF NOT EXISTS "version_major" INTEGER NOT NULL DEFAULT 1;
ALTER TABLE core."protocol_plan"
    ADD COLUMN IF NOT EXISTS "version_minor" INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN core."protocol_plan"."version_major" IS
    'Which protocol lineage within the instance. Starts at 1; a fresh protocol authored with no supersedes_protocol_id gets the next unused major for its instance_id.';
COMMENT ON COLUMN core."protocol_plan"."version_minor" IS
    'How many times this lineage has been amended. 0 on the lineage root; an amendment (supersedes_protocol_id set) is the target''s version_minor + 1. Displayed as "major" when 0, "major.minor" otherwise.';

ALTER TABLE core."protocol_plan"
    DROP CONSTRAINT IF EXISTS "chk_protocol_plan_version_major";
ALTER TABLE core."protocol_plan"
    ADD CONSTRAINT "chk_protocol_plan_version_major"
    CHECK ("version_major" >= 1)
    NOT VALID;

ALTER TABLE core."protocol_plan"
    DROP CONSTRAINT IF EXISTS "chk_protocol_plan_version_minor";
ALTER TABLE core."protocol_plan"
    ADD CONSTRAINT "chk_protocol_plan_version_minor"
    CHECK ("version_minor" >= 0)
    NOT VALID;


-- ###########################################################################
-- 3  Validate everything added NOT VALID above
-- ###########################################################################

ALTER TABLE core."anamnesis_assessments" VALIDATE CONSTRAINT "fk_anamnesis_assessments_appointment_id";
ALTER TABLE core."prs_assessment_instances" VALIDATE CONSTRAINT "fk_prs_assessment_instances_appointment_id";
ALTER TABLE core."protocol_plan" VALIDATE CONSTRAINT "chk_protocol_plan_version_major";
ALTER TABLE core."protocol_plan" VALIDATE CONSTRAINT "chk_protocol_plan_version_minor";

COMMIT;
