-- 81_disease_composite_weights.sql
--
-- APPLY ORDER: after 80. Independent of 71/74/79/80.
--
-- THE PROBLEM
--
-- Every one of the 41 PRS scales computes a correct individual score
-- (scoring_rules.py, Documents/Anava_PRS_Scoring_Engine_Specification_v1.docx
-- Section 2), but the disease-level composite (Section 3: Sigma(scale % x
-- weight%)) has never had any code or DB storage. prs_final_results even has
-- an unused composite_summary TEXT column reserved for this, but nothing
-- reads or writes it -- reference.prs_disease_scale_map has no weight column
-- at all, so there was nowhere to even keep the weight tables.
--
-- THE FIX
--
-- 1) reference.prs_disease_scale_map.weight_pct -- the per-(disease,scale)
--    weight from the spec doc's 14 weight tables, seeded below. All 14
--    diseases' weights sum to exactly 100 (asserted in the generator script
--    that produced this file, and re-verified by the query at the bottom).
-- 2) core.prs_scale_results.direction_corrected_percentage -- the scale's
--    0-100 'higher = worse' percentage (scoring_rules.compute_scale_score()'s
--    direction_corrected_percentage), persisted so the composite can be
--    recomputed from stored data. The existing generated `percentage` column
--    is calculated_value/max_possible only -- backwards for the 9 scales that
--    are natively 'higher = better' (ALSFRS-R, AMTS, Barthel, IADL, KPS, MoCA,
--    MRC, MSQ, SS-QOL) -- so composite scoring must never read it.
-- 3) core.prs_final_results.composite_score / composite_severity_level /
--    composite_severity_label -- where app/modules/prs/service.py::
--    _update_disease_composite writes the computed composite after each
--    scale is finalized (app/modules/prs/disease_scoring.py::
--    compute_disease_composite).

BEGIN;

ALTER TABLE reference.prs_disease_scale_map ADD COLUMN IF NOT EXISTS weight_pct NUMERIC(5,2);
ALTER TABLE core.prs_scale_results ADD COLUMN IF NOT EXISTS direction_corrected_percentage NUMERIC;
ALTER TABLE core.prs_final_results ADD COLUMN IF NOT EXISTS composite_score NUMERIC(6,2);
ALTER TABLE core.prs_final_results ADD COLUMN IF NOT EXISTS composite_severity_level TEXT;
ALTER TABLE core.prs_final_results ADD COLUMN IF NOT EXISTS composite_severity_label TEXT;

-- Weight tables: Documents/Anava_PRS_Scoring_Engine_Specification_v1.docx Section 3.

-- Depression/Anxiety (DEPRESSION/ANXIETY/2026)
UPDATE reference.prs_disease_scale_map SET weight_pct = 25.00 WHERE disease_id = 'DEPRESSION/ANXIETY/2026' AND scale_id = 'BDI-II/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 20.00 WHERE disease_id = 'DEPRESSION/ANXIETY/2026' AND scale_id = 'GAD-7/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'DEPRESSION/ANXIETY/2026' AND scale_id = 'DASS-21/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'DEPRESSION/ANXIETY/2026' AND scale_id = 'MADRS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'DEPRESSION/ANXIETY/2026' AND scale_id = 'PSQI/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'DEPRESSION/ANXIETY/2026' AND scale_id = 'COMPASS-31/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'DEPRESSION/ANXIETY/2026' AND scale_id = 'EQ-5D-5L/2026';

-- Chronic Pain (CHRONICPAIN/2026)
UPDATE reference.prs_disease_scale_map SET weight_pct = 25.00 WHERE disease_id = 'CHRONICPAIN/2026' AND scale_id = 'PRS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'CHRONICPAIN/2026' AND scale_id = 'DN-4/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'CHRONICPAIN/2026' AND scale_id = 'PainDETECT/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'CHRONICPAIN/2026' AND scale_id = 'DASS-21/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'CHRONICPAIN/2026' AND scale_id = 'GAD-7/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'CHRONICPAIN/2026' AND scale_id = 'PSQI/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'CHRONICPAIN/2026' AND scale_id = 'COMPASS-31/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'CHRONICPAIN/2026' AND scale_id = 'EQ-5D-5L/2026';

-- Fibromyalgia (FIBROMYALGIA/2026)
UPDATE reference.prs_disease_scale_map SET weight_pct = 40.00 WHERE disease_id = 'FIBROMYALGIA/2026' AND scale_id = 'FIQR/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'FIBROMYALGIA/2026' AND scale_id = 'FSS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'FIBROMYALGIA/2026' AND scale_id = 'PRS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'FIBROMYALGIA/2026' AND scale_id = 'VAS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'FIBROMYALGIA/2026' AND scale_id = 'PainDETECT/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'FIBROMYALGIA/2026' AND scale_id = 'COMPASS-31/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'FIBROMYALGIA/2026' AND scale_id = 'EQ-5D-5L/2026';

-- Migraine (MIGRAINE/2026)
UPDATE reference.prs_disease_scale_map SET weight_pct = 30.00 WHERE disease_id = 'MIGRAINE/2026' AND scale_id = 'MIDAS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'MIGRAINE/2026' AND scale_id = 'MSQ/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'MIGRAINE/2026' AND scale_id = 'PRS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'MIGRAINE/2026' AND scale_id = 'DASS-21/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'MIGRAINE/2026' AND scale_id = 'BDI-II/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'MIGRAINE/2026' AND scale_id = 'PSQI/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'MIGRAINE/2026' AND scale_id = 'COMPASS-31/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'MIGRAINE/2026' AND scale_id = 'EQ-5D-5L/2026';

-- Ataxia (ATAXIA/2026)
UPDATE reference.prs_disease_scale_map SET weight_pct = 40.00 WHERE disease_id = 'ATAXIA/2026' AND scale_id = 'SARA/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'ATAXIA/2026' AND scale_id = 'DHI/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'ATAXIA/2026' AND scale_id = 'VVAS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'ATAXIA/2026' AND scale_id = 'DASS-21/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'ATAXIA/2026' AND scale_id = 'BDI-II/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'ATAXIA/2026' AND scale_id = 'COMPASS-31/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'ATAXIA/2026' AND scale_id = 'EQ-5D-5L/2026';

-- After Stroke/TBI (AFTERSTROKE/TBI/2026)
UPDATE reference.prs_disease_scale_map SET weight_pct = 25.00 WHERE disease_id = 'AFTERSTROKE/TBI/2026' AND scale_id = 'BARTHEL/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 20.00 WHERE disease_id = 'AFTERSTROKE/TBI/2026' AND scale_id = 'SS-QOL/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'AFTERSTROKE/TBI/2026' AND scale_id = 'KPS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'AFTERSTROKE/TBI/2026' AND scale_id = 'MRC/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'AFTERSTROKE/TBI/2026' AND scale_id = 'MAS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'AFTERSTROKE/TBI/2026' AND scale_id = 'MoCA/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'AFTERSTROKE/TBI/2026' AND scale_id = 'DASS-21/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'AFTERSTROKE/TBI/2026' AND scale_id = 'COMPASS-31/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'AFTERSTROKE/TBI/2026' AND scale_id = 'PainDETECT/2026';

-- Dementia (DEMENTIA/2026)
UPDATE reference.prs_disease_scale_map SET weight_pct = 30.00 WHERE disease_id = 'DEMENTIA/2026' AND scale_id = 'MoCA/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 20.00 WHERE disease_id = 'DEMENTIA/2026' AND scale_id = 'AMTS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'DEMENTIA/2026' AND scale_id = 'DSRS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'DEMENTIA/2026' AND scale_id = 'GDS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'DEMENTIA/2026' AND scale_id = 'IADL/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'DEMENTIA/2026' AND scale_id = 'DASS-21/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'DEMENTIA/2026' AND scale_id = 'COMPASS-31/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'DEMENTIA/2026' AND scale_id = 'EQ-5D-5L/2026';

-- Parkinson's Disease (PARKINSONSDISEASE/2026)
UPDATE reference.prs_disease_scale_map SET weight_pct = 30.00 WHERE disease_id = 'PARKINSONSDISEASE/2026' AND scale_id = 'PDSS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 25.00 WHERE disease_id = 'PARKINSONSDISEASE/2026' AND scale_id = 'PFS-16/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 20.00 WHERE disease_id = 'PARKINSONSDISEASE/2026' AND scale_id = 'MoCA/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'PARKINSONSDISEASE/2026' AND scale_id = 'PainDETECT/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'PARKINSONSDISEASE/2026' AND scale_id = 'COMPASS-31/2026';

-- Tinnitus (TINNITUS/2026)
UPDATE reference.prs_disease_scale_map SET weight_pct = 50.00 WHERE disease_id = 'TINNITUS/2026' AND scale_id = 'THI/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'TINNITUS/2026' AND scale_id = 'DASS-21/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'TINNITUS/2026' AND scale_id = 'GAD-7/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'TINNITUS/2026' AND scale_id = 'PSQI/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'TINNITUS/2026' AND scale_id = 'COMPASS-31/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'TINNITUS/2026' AND scale_id = 'EQ-5D-5L/2026';

-- Insomnia (INSOMNIA/2026)
UPDATE reference.prs_disease_scale_map SET weight_pct = 25.00 WHERE disease_id = 'INSOMNIA/2026' AND scale_id = 'PSQI/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 20.00 WHERE disease_id = 'INSOMNIA/2026' AND scale_id = 'ISI/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'INSOMNIA/2026' AND scale_id = 'AIS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'INSOMNIA/2026' AND scale_id = 'SLEEP-50/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'INSOMNIA/2026' AND scale_id = 'FFS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'INSOMNIA/2026' AND scale_id = 'DASS-21/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'INSOMNIA/2026' AND scale_id = 'GAD-7/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'INSOMNIA/2026' AND scale_id = 'COMPASS-31/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'INSOMNIA/2026' AND scale_id = 'EQ-5D-5L/2026';

-- Multiple Sclerosis (MULTIPLESCLEROSIS/2026)
UPDATE reference.prs_disease_scale_map SET weight_pct = 30.00 WHERE disease_id = 'MULTIPLESCLEROSIS/2026' AND scale_id = 'MFIS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 20.00 WHERE disease_id = 'MULTIPLESCLEROSIS/2026' AND scale_id = 'SARA/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'MULTIPLESCLEROSIS/2026' AND scale_id = 'DHI/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'MULTIPLESCLEROSIS/2026' AND scale_id = 'MoCA/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'MULTIPLESCLEROSIS/2026' AND scale_id = 'BARTHEL/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'MULTIPLESCLEROSIS/2026' AND scale_id = 'COMPASS-31/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'MULTIPLESCLEROSIS/2026' AND scale_id = 'EQ-5D-5L/2026';

-- ADHD (ADHD/2026)
UPDATE reference.prs_disease_scale_map SET weight_pct = 40.00 WHERE disease_id = 'ADHD/2026' AND scale_id = 'ASRS-v1.1/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 30.00 WHERE disease_id = 'ADHD/2026' AND scale_id = 'SNAP-IV/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'ADHD/2026' AND scale_id = 'DASS-21/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'ADHD/2026' AND scale_id = 'COMPASS-31/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'ADHD/2026' AND scale_id = 'EQ-5D-5L/2026';

-- ALS (ALS/2026)
UPDATE reference.prs_disease_scale_map SET weight_pct = 40.00 WHERE disease_id = 'ALS/2026' AND scale_id = 'ALSFRS-R/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'ALS/2026' AND scale_id = 'MAS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'ALS/2026' AND scale_id = 'BDI-II/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'ALS/2026' AND scale_id = 'GAD-7/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'ALS/2026' AND scale_id = 'DASS-21/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'ALS/2026' AND scale_id = 'COMPASS-31/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'ALS/2026' AND scale_id = 'EQ-5D-5L/2026';

-- Irritable Bowel Disease (IRRITABLEBOWELDISEASE/2026)
UPDATE reference.prs_disease_scale_map SET weight_pct = 40.00 WHERE disease_id = 'IRRITABLEBOWELDISEASE/2026' AND scale_id = 'IBS-SSS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 15.00 WHERE disease_id = 'IRRITABLEBOWELDISEASE/2026' AND scale_id = 'PRS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'IRRITABLEBOWELDISEASE/2026' AND scale_id = 'DASS-21/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'IRRITABLEBOWELDISEASE/2026' AND scale_id = 'BDI-II/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'IRRITABLEBOWELDISEASE/2026' AND scale_id = 'HDRS/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 10.00 WHERE disease_id = 'IRRITABLEBOWELDISEASE/2026' AND scale_id = 'COMPASS-31/2026';
UPDATE reference.prs_disease_scale_map SET weight_pct = 5.00 WHERE disease_id = 'IRRITABLEBOWELDISEASE/2026' AND scale_id = 'EQ-5D-5L/2026';

COMMIT;

-- ###########################################################################
-- VERIFICATION -- run after COMMIT
-- ###########################################################################
-- 1) Every one of the 14 diseases' weights sums to exactly 100:
--
--   SELECT disease_id, SUM(weight_pct) AS total_weight
--   FROM reference.prs_disease_scale_map
--   WHERE weight_pct IS NOT NULL
--   GROUP BY disease_id
--   HAVING SUM(weight_pct) <> 100;
--
--   Expect ZERO rows back.
--
-- 2) Row count: 100 (disease, scale) pairs updated across 14 diseases.

