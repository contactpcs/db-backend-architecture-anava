-- 96_prs_assessment_cursor.sql
--
-- THE PROBLEM
--
-- Resuming an in-progress prs_assessment_instance (hard refresh, closing
-- the tab, doctor reopening the page later) always lands the patient/doctor
-- on the first UNANSWERED question of the first incomplete scale. That's
-- correct for the common case, but wrong the moment someone deliberately
-- navigates past unanswered questions (e.g. skips 5-7 to look ahead at 8,
-- or the assessment allows answering out of order) — "first unanswered"
-- silently discards how far they'd actually gotten and drops them back
-- earlier than where they left off.
--
-- THE FIX
--
-- One nullable cursor column on the instance: which question index (within
-- whichever scale is currently the first-incomplete one — scale order is
-- fixed and derived from is_completed per scale, so no scale_id is needed
-- alongside it) the caller was last looking at. Updated by a lightweight
-- PATCH on every Next/Previous navigation. On resume, if set, this wins
-- over the first-unanswered fallback; NULL (a never-navigated or
-- pre-migration instance) falls back to the existing first-unanswered
-- behavior unchanged.

BEGIN;

ALTER TABLE core."prs_assessment_instances"
    ADD COLUMN "current_question_index" INTEGER;

COMMENT ON COLUMN core."prs_assessment_instances"."current_question_index" IS
    'Last-viewed question index (0-based) within the instance''s current '
    '(first-incomplete) scale. NULL = never navigated / resume falls back '
    'to first-unanswered. Set via PATCH /prs-assessment-instances/{id}/cursor.';

COMMIT;
