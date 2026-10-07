-- 95_one_general_registration_prs.sql
--
-- One live general-registration PRS per patient.
--
-- SCOPE: voids (never deletes) duplicate general_registration instances left
-- by a race, then adds a partial unique index so the race cannot recur.
--
-- APPLY ORDER: after 94.
--
--
-- WHY
-- Two "start assessment" requests arriving together (a page firing its load
-- twice) both found no in-progress instance in PrsAssessmentService.start()
-- and both created one, in the same second. The patient finished one; the
-- other stayed 'in_progress' with no answers forever. Readers that take the
-- newest instance — the doctor workspace's Registration Record — then showed
-- the empty one, so general PRS appeared blank. Found on 3 patients, the
-- latest on 2026-09-27.


-- 1. Keep the best instance per patient (completed first, then newest);
--    void the rest. Voided rows keep their data for audit; every reader and
--    the start() lookups skip them.
UPDATE core."prs_assessment_instances" pai
SET "is_voided" = TRUE,
    "voided_at" = now(),
    "voided_reason" = 'Duplicate general_registration instance from a concurrent start (95)'
FROM (
    SELECT "instance_id",
           row_number() OVER (
               PARTITION BY "patient_id"
               ORDER BY ("status" = 'completed') DESC, "completed_at" DESC NULLS LAST, "started_at" DESC
           ) AS rn
    FROM core."prs_assessment_instances"
    WHERE "assessment_stage" = 'general_registration'
      AND "appointment_id" IS NULL
      AND "is_voided" = FALSE
) ranked
WHERE pai."instance_id" = ranked."instance_id" AND ranked.rn > 1;


-- 2. The race can no longer produce two: the second insert fails, and
--    start() returns the first one instead (prs/service.py).
DROP INDEX IF EXISTS core.uq_prs_one_general_registration;
CREATE UNIQUE INDEX uq_prs_one_general_registration
    ON core."prs_assessment_instances" ("patient_id")
    WHERE "assessment_stage" = 'general_registration'
      AND "appointment_id" IS NULL
      AND "is_voided" = FALSE;


-- VERIFY
--  SELECT patient_id, count(*) FROM core.prs_assessment_instances
--  WHERE assessment_stage = 'general_registration' AND appointment_id IS NULL AND NOT is_voided
--  GROUP BY 1 HAVING count(*) > 1;   -- 0 rows
