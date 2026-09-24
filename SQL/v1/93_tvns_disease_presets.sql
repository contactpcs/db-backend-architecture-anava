-- 93_tvns_disease_presets.sql
--
-- Doctor asked for disease-linked tVNS dosing presets that autofill the
-- wizard's (now freely-editable, see 92_tvns_manual_prescription.sql)
-- dosing fields when a condition is selected. Source ranges are clinician-
-- supplied dummy values, one param block per disease, for:
--   1 Parkinson's Disease - Motor Symptoms (Rigidity/Bradykinesia/Gait)
--   2 Parkinson's Disease - Cognitive/Memory Decline
--   3 Mild-Moderate Depression (existing condition, reused)
--   4 ADHD (existing condition, reused)
--   7 Anxiety / Stress / Emotional Irritability (existing condition, reused)
--   8 Insomnia / Sleep Disorder
-- Diseases 5 (Stroke Rehab Motor), 6 (Alzheimer's/MCI) and 9 (Cognitive
-- Enhancement) were explicitly excluded by the doctor.
--
-- tvns_dosing previously stored single-point frequency_hz/pulse_width_us
-- (fine for the old catalogue-pick UI). The new preset data is given as
-- ranges ("100-200Hz"), so this adds _min/_max range columns alongside the
-- existing single-value ones (left in place, unused by new rows, so no
-- existing reader breaks).
--
-- Where a param block lists two modes ("Intermittent/Modulation"), that
-- becomes two dosing rows for the same condition/placement - tvns_dosing
-- already supports multiple rows per condition (see 3 existing Depression
-- rows), so no schema change needed for that.

BEGIN;

ALTER TABLE reference."tvns_dosing" ADD COLUMN IF NOT EXISTS "frequency_hz_min" NUMERIC(6,2);
ALTER TABLE reference."tvns_dosing" ADD COLUMN IF NOT EXISTS "frequency_hz_max" NUMERIC(6,2);
ALTER TABLE reference."tvns_dosing" ADD COLUMN IF NOT EXISTS "pulse_width_us_min" INTEGER;
ALTER TABLE reference."tvns_dosing" ADD COLUMN IF NOT EXISTS "pulse_width_us_max" INTEGER;

ALTER TABLE reference."tvns_dosing"
  DROP CONSTRAINT IF EXISTS "chk_tvns_dosing_frequency_hz_range_minmax";
ALTER TABLE reference."tvns_dosing"
  ADD CONSTRAINT "chk_tvns_dosing_frequency_hz_range_minmax"
  CHECK (
    (frequency_hz_min IS NULL OR (frequency_hz_min >= 1 AND frequency_hz_min <= 1000))
    AND (frequency_hz_max IS NULL OR (frequency_hz_max >= 1 AND frequency_hz_max <= 1000))
    AND (frequency_hz_min IS NULL OR frequency_hz_max IS NULL OR frequency_hz_min <= frequency_hz_max)
  );

ALTER TABLE reference."tvns_dosing"
  DROP CONSTRAINT IF EXISTS "chk_tvns_dosing_pulse_width_us_range_minmax";
ALTER TABLE reference."tvns_dosing"
  ADD CONSTRAINT "chk_tvns_dosing_pulse_width_us_range_minmax"
  CHECK (
    (pulse_width_us_min IS NULL OR (pulse_width_us_min >= 50 AND pulse_width_us_min <= 500))
    AND (pulse_width_us_max IS NULL OR (pulse_width_us_max >= 50 AND pulse_width_us_max <= 500))
    AND (pulse_width_us_min IS NULL OR pulse_width_us_max IS NULL OR pulse_width_us_min <= pulse_width_us_max)
  );

-- New conditions (Depression/ADHD/Anxiety Disorders already exist).
INSERT INTO reference.neuromod_conditions (condition_name, display_order)
VALUES
  ('Parkinson''s Disease - Motor Symptoms', 60),
  ('Parkinson''s Disease - Cognitive/Memory Decline', 70),
  ('Insomnia / Sleep Disorder', 80)
ON CONFLICT (condition_name) DO NOTHING;

COMMIT;
