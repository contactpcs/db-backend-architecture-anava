-- 113_catalogue_links_seed.sql
--
-- Catalogue content that exists in the previous database but in no SQL seed (exported
-- 2026-10-08). Everything is matched by NAME, because condition ids are generated per
-- database. Run as table owner (postgres). Idempotent.
--
--   1. TVNS-001: name "Biothm tVNS", company Biothm (as in the previous database).
--   2. reference.neuromod_condition_prs_diseases: condition -> PRS disease links, read by
--      51_protocol_scales_from_prs.sql to suggest scales from a condition.
--
-- NOT copied: three extra Depression tVNS dosing rows from the previous database. Their notes
-- read "Placeholder catalogue entry - dummy dose pending real clinical protocol", so they
-- are not real doses. The 14 presets from files 93-95 are the catalogue.

BEGIN;

UPDATE reference.neuromod_devices d
   SET device_name = 'Biothm tVNS',
       company_id  = (SELECT company_id FROM reference.device_companies WHERE company_code = 'BIOTHM')
 WHERE d.device_code = 'TVNS-001'
   AND EXISTS (SELECT 1 FROM reference.device_companies WHERE company_code = 'BIOTHM');

INSERT INTO reference.neuromod_condition_prs_diseases (condition_id, disease_id)
SELECT c.condition_id, v.disease_id
FROM (VALUES
    ('ADHD', 'ADHD/2026'),
    ('Anxiety Disorders', 'DEPRESSION/ANXIETY/2026'),
    ('Chronic Pain', 'CHRONICPAIN/2026'),
    ('Depression', 'DEPRESSION/ANXIETY/2026')
) AS v(condition_name, disease_id)
JOIN reference.neuromod_conditions c ON c.condition_name = v.condition_name
WHERE EXISTS (SELECT 1 FROM reference.prs_diseases p WHERE p.disease_id = v.disease_id)
  AND NOT EXISTS (SELECT 1 FROM reference.neuromod_condition_prs_diseases m
                   WHERE m.condition_id = c.condition_id AND m.disease_id = v.disease_id);

COMMIT;
