-- 115_prs_scales_applicable_for_seed.sql
--
-- APPLY ORDER: after 114. Needs the PRS content seed
-- (scripts/seed_prs_clinical_content.py) to have run. Run as table owner
-- (postgres). Idempotent.
--
-- THE PROBLEM
--
-- reference.prs_scales.applicable_for says which PRS stage a scale belongs to.
-- Data/prs_scales_rows.csv has no such column and no SQL/v1 file sets it, so a
-- database built from SQL/v1 has every scale on the column default,
-- 'main_clinical'. Found on Anava_App_v1 (built 2026-10-08): 41 of 41 scales.
--
-- PrsCatalogRepository.scales_for_disease filters on this column with
-- [instance stage, 'all'] when an assessment is opened and the patient has no
-- scale assignments for that stage and disease (prs/service.py, the fallback
-- in the scale-composition step). With every scale on 'main_clinical':
--
--   main_clinical  GAD-7 and PSQI are added to the list for the six diseases
--                  mapped to them, and the instance cannot complete until they
--                  are answered too.
--   followup       nothing matches (no scale is 'all' or 'followup'), so the
--                  list is empty.
--
-- Registration is not affected (it is hardcoded to EQ-5D-5L), and neither is
-- any stage where scales were assigned to the patient.
--
-- THE FIX
--
-- The five non-default values from the previous hand-curated seed
-- (Reference-MR/SQL/v0/16_seed_data.sql). The other 36 scales are
-- 'main_clinical' there as well, so the default is already right for them.
--
-- SOURCE CHECK: these are the values in that seed FILE. If the previous
-- database is still reachable, confirm it agrees before applying:
--
--     SELECT scale_id, applicable_for FROM prs_scales
--      WHERE applicable_for <> 'main_clinical' ORDER BY 1;

BEGIN;

UPDATE reference.prs_scales
   SET applicable_for = 'all', updated_at = now()
 WHERE scale_id IN ('COMPASS-31/2026', 'DASS-21/2026', 'EQ-5D-5L/2026')
   AND applicable_for <> 'all';

UPDATE reference.prs_scales
   SET applicable_for = 'general_registration', updated_at = now()
 WHERE scale_id IN ('GAD-7/2026', 'PSQI/2026')
   AND applicable_for <> 'general_registration';

COMMIT;


-- VERIFY — run after COMMIT
--
-- SELECT applicable_for, count(*) FROM reference.prs_scales GROUP BY 1 ORDER BY 1;
--   all                   3
--   general_registration  2
--   main_clinical         36
