-- _fix_void_duplicate_session_prs.sql
--
-- A human-run data fix, NOT a build step (the leading underscore keeps it out
-- of sequence.txt, apply_sql_v1.py and CI, like the other _*.sql scripts).
--
-- THE PROBLEM
--
-- Before commit 67735a3 (2026-09-23, PrsAssessmentService.start() resuming the
-- instance already tied to the appointment), opening a device session's PRS
-- again created a brand-new prs_assessment_instances row instead of reopening
-- the first one. A scale answered three times for one session therefore left
-- three completed PRS behind. Only the last one is linked to the session
-- (device_session_scales.prs_instance_id); the earlier ones are linked to
-- nothing, but every reader that lists completed PRS still shows them: the
-- Final Report printed three rows for one device session.
--
-- THE FIX
--
-- Void the leftovers (is_voided, SQL/v1/84), the same way 95 handled duplicate
-- general_registration instances. Nothing is deleted: PRS answers are clinical
-- data. A voided PRS is skipped by the disease composite, the reports and the
-- Final Report.
--
-- A completed PRS is voided only when BOTH hold:
--   1. a LATER completed, non-voided PRS exists for the same patient,
--      appointment, condition and stage (so the visit keeps its last answer);
--   2. nothing points at it: no device_session_scales row, no
--      device_session_prs_responses row, no followup_prs_responses row.
-- Two parallel PRS that are BOTH linked to session records are left alone on
-- purpose: which one counts there is a clinical decision, not a clean-up.
--
-- NOT touched: core.disease_composite_scores keeps the composite rows that were
-- computed when the voided attempts were completed. The doctor dashboard reads
-- that history, so it still shows a point for each attempt.
--
-- Idempotent: a second run finds nothing left to void.
-- Run as a role the UPDATE policy admits (rls_pai_update: super_admin).

-- 1. PREVIEW — run this first and read the list.
SELECT i.instance_id, i.patient_id, i.appointment_id, i.disease_id, i.completed_at, i.initiated_by
FROM core."prs_assessment_instances" i
WHERE i.status = 'completed' AND NOT i.is_voided AND i.appointment_id IS NOT NULL
  AND EXISTS (
        SELECT 1 FROM core."prs_assessment_instances" k
        WHERE k.patient_id = i.patient_id AND k.appointment_id = i.appointment_id
          AND k.disease_id IS NOT DISTINCT FROM i.disease_id AND k.assessment_stage = i.assessment_stage
          AND k.status = 'completed' AND NOT k.is_voided
          AND (k.completed_at, k.instance_id) > (i.completed_at, i.instance_id))
  AND NOT EXISTS (SELECT 1 FROM core."device_session_scales" s WHERE s.prs_instance_id = i.instance_id)
  AND NOT EXISTS (SELECT 1 FROM core."device_session_prs_responses" d WHERE d.instance_id = i.instance_id)
  AND NOT EXISTS (SELECT 1 FROM core."followup_prs_responses" f WHERE f.instance_id = i.instance_id)
ORDER BY i.completed_at;

-- 2. VOID — the same selection.
BEGIN;

UPDATE core."prs_assessment_instances" i
SET "is_voided" = TRUE,
    "voided_at" = now(),
    "voided_reason" = 'Duplicate device-session PRS: same condition answered again under one appointment before the resume fix (67735a3). Superseded by the later completed PRS of that visit (_fix_void_duplicate_session_prs.sql)'
WHERE i.status = 'completed' AND NOT i.is_voided AND i.appointment_id IS NOT NULL
  AND EXISTS (
        SELECT 1 FROM core."prs_assessment_instances" k
        WHERE k.patient_id = i.patient_id AND k.appointment_id = i.appointment_id
          AND k.disease_id IS NOT DISTINCT FROM i.disease_id AND k.assessment_stage = i.assessment_stage
          AND k.status = 'completed' AND NOT k.is_voided
          AND (k.completed_at, k.instance_id) > (i.completed_at, i.instance_id))
  AND NOT EXISTS (SELECT 1 FROM core."device_session_scales" s WHERE s.prs_instance_id = i.instance_id)
  AND NOT EXISTS (SELECT 1 FROM core."device_session_prs_responses" d WHERE d.instance_id = i.instance_id)
  AND NOT EXISTS (SELECT 1 FROM core."followup_prs_responses" f WHERE f.instance_id = i.instance_id);

COMMIT;

-- TO UNDO one row:
--   UPDATE core."prs_assessment_instances"
--   SET is_voided = FALSE, voided_at = NULL, voided_reason = NULL WHERE instance_id = '<id>';
