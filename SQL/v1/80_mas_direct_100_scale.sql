-- 80_mas_direct_100_scale.sql
--
-- THE PROBLEM
--
-- MAS/001's 6 options (Ashworth grades 0-5) were seeded with points 0-5,
-- i.e. max_possible=5 -- correct on its own native scale, but not on the
-- 0-100 scale every other scale in the system normalizes to.
--
-- THE FIX (clinic decision)
--
-- Re-seed the same 6 options directly on a 0-100 scale: grade 0=0pts,
-- grade 1=20pts, ... grade 5=100pts. No new scoring code needed -- the
-- existing flat-sum path already produces the final 0-100 score once
-- points are seeded this way (see scoring_rules.py SCALE_CONFIG["MAS"]).
-- option_value (the raw grade 0-5 the clinician selects) is unchanged;
-- only points (what the grade is worth on the composite's 0-100 scale)
-- changes. UPDATE only -- no rows added or removed, no FK impact.
--
-- Matches Data/prs_options_rows.csv exactly -- generated from the diff
-- against git HEAD, not hand-typed.

BEGIN;

UPDATE prs_options SET points = 20 WHERE option_id = 'MAS/001/02';
UPDATE prs_options SET points = 40 WHERE option_id = 'MAS/001/03';
UPDATE prs_options SET points = 60 WHERE option_id = 'MAS/001/04';
UPDATE prs_options SET points = 80 WHERE option_id = 'MAS/001/05';
UPDATE prs_options SET points = 100 WHERE option_id = 'MAS/001/06';

COMMIT;
