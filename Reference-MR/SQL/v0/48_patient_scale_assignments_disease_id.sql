-- ============================================================
-- Anava Clinic — DB Schema
-- File 48: patient_scale_assignments.disease_id
--
-- patient_scale_assignments only ever stored scale_id — no way to
-- reconstruct "which disease was this assignment for" without it,
-- since a scale can be shared across multiple diseases
-- (prs_scales.num_diseases_used). This broke the doctor-assigns-PRS
-- flow end to end: the permissions list normalizer had nothing real
-- to read and fell back to an empty disease_id, so starting the
-- assessment from that permission could never resolve which scales
-- to load.
--
-- Nullable: existing rows predate this column and can't be safely
-- backfilled (the same scale_id may map to several diseases; there's
-- no way to know which one a historical assignment was actually for).
-- ============================================================

ALTER TABLE patient_scale_assignments
    ADD COLUMN IF NOT EXISTS disease_id TEXT REFERENCES prs_diseases(disease_id) ON DELETE SET NULL;

COMMENT ON COLUMN patient_scale_assignments.disease_id IS
    'Which disease this scale was assigned for — required for new assignments (set by the app), NULL on pre-existing rows.';
