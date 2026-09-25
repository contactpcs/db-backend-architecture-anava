-- 91_anamnesis_one_per_consultation.sql
--
-- Drops anamnesis versioning. One anamnesis per consultation (initial /
-- follow_up / protocol_followup appointment), edited in place until the
-- doctor marks that appointment completed; one registration anamnesis per
-- patient.
--
-- SCOPE: older duplicate rows marked 'superseded' (NOT deleted — anamnesis
-- is retain_locked clinical data), the (patient_id, version) unique dropped,
-- two partial unique indexes added. The version column stays, unused.
--
-- APPLY ORDER: after 90.
--
--
-- ###########################################################################
-- WHY
-- ###########################################################################
--
-- Every edit used to INSERT a new row with version = patient-wide MAX + 1,
-- and readers picked "the latest". The lock that stopped an old
-- consultation's anamnesis being overwritten lived only in the browser. Now
-- the row is updated in place and the server refuses edits once its
-- appointment is completed (anamnesis/service.py), so there must be exactly
-- one live row per appointment — enforced here, not just assumed.


-- ###########################################################################
-- 1  Collapse existing versions: newest live row wins, the rest superseded
-- ###########################################################################
-- Same "newest" the old readers used (version DESC), so what a doctor saw
-- before this migration is what they see after it.

UPDATE core."anamnesis_assessments" a
SET "status" = 'superseded'
FROM (
    SELECT "anamnesis_id",
           row_number() OVER (PARTITION BY "appointment_id" ORDER BY "version" DESC, "created_at" DESC) AS rn
    FROM core."anamnesis_assessments"
    WHERE "appointment_id" IS NOT NULL AND "status" <> 'superseded'
) ranked
WHERE a."anamnesis_id" = ranked."anamnesis_id" AND ranked.rn > 1;

UPDATE core."anamnesis_assessments" a
SET "status" = 'superseded'
FROM (
    SELECT "anamnesis_id",
           row_number() OVER (PARTITION BY "patient_id" ORDER BY "version" DESC, "created_at" DESC) AS rn
    FROM core."anamnesis_assessments"
    WHERE "assessment_stage" = 'registration' AND "status" <> 'superseded'
) ranked
WHERE a."anamnesis_id" = ranked."anamnesis_id" AND ranked.rn > 1;


-- ###########################################################################
-- 2  Versions are no longer allocated
-- ###########################################################################

ALTER TABLE core."anamnesis_assessments"
    DROP CONSTRAINT IF EXISTS "anamnesis_assessments_patient_id_version_key";

COMMENT ON COLUMN core."anamnesis_assessments"."version" IS
    'DEPRECATED (91): anamnesis is edited in place, one row per consultation. New rows keep the default 1. Historic values kept for rows written before 91.';
COMMENT ON COLUMN core."anamnesis_assessments"."status" IS
    'in_progress | completed | superseded. superseded (91) = an older version left over from before anamnesis stopped being versioned; hidden from every reader, kept for retention.';


-- ###########################################################################
-- 3  One live anamnesis per consultation, one registration per patient
-- ###########################################################################
-- Also what makes AnamnesisService.start()'s get-or-create race-safe: two
-- concurrent starts cannot both insert.

DROP INDEX IF EXISTS core.uq_anamnesis_one_per_appointment;
CREATE UNIQUE INDEX uq_anamnesis_one_per_appointment
    ON core."anamnesis_assessments" ("appointment_id")
    WHERE "appointment_id" IS NOT NULL AND "status" <> 'superseded';

DROP INDEX IF EXISTS core.uq_anamnesis_one_registration_per_patient;
CREATE UNIQUE INDEX uq_anamnesis_one_registration_per_patient
    ON core."anamnesis_assessments" ("patient_id")
    WHERE "assessment_stage" = 'registration' AND "status" <> 'superseded';


-- ###########################################################################
-- VERIFY
-- ###########################################################################
--  SELECT appointment_id, count(*) FROM core.anamnesis_assessments
--  WHERE appointment_id IS NOT NULL AND status <> 'superseded'
--  GROUP BY appointment_id HAVING count(*) > 1;                         -- 0 rows
--
--  SELECT conname FROM pg_constraint
--  WHERE conname = 'anamnesis_assessments_patient_id_version_key';     -- 0 rows
