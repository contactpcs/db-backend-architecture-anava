-- 79_psqi_full_instrument_seed.sql
--
-- THE PROBLEM
--
-- PSQI was seeded with only its first 4 free-entry questions (bedtime,
-- sleep-latency minutes, wake time, hours slept) — the 14 Likert questions
-- that carry almost all of the official PSQI score (Q5a-5j sleep
-- disturbances, Q6 medication use, Q7 daytime sleepiness, Q8 enthusiasm,
-- Q9 subjective quality) were never seeded. PSQI could not be scored at
-- all as a result. Source: TR4872appendixS1.pdf (Buysse et al. 1989
-- official scoring guide). Q10 (bed partner questions) is deliberately
-- excluded — the source guide states it does not contribute to the score.
--
-- WHY THIS IS UPDATE/DELETE + INSERT ON Q1-Q4, NOT DROP-THE-QUESTIONS-AND-RECREATE
--
-- prs_responses.question_id has ON DELETE CASCADE onto prs_questions
-- (07_prs_tables.sql). Deleting PSQI/001-004 themselves to "start clean"
-- would silently destroy any real patient answers already recorded
-- against them, with no error — so those 4 question rows are kept and
-- only UPDATEd. What they're updated TO: Q1/Q3 (bedtime/wake time) move
-- from a free-text field to answer_type='time' (a native time picker),
-- and Q2/Q4 (latency minutes/hours slept) get explicit min_value/max_value
-- bounds on the question row itself. Both changes require the placeholder
-- "Numeric input (...)"/"Text input (...)" option rows attached to Q1-Q4 to
-- go away entirely (they were rendering as a single clickable choice
-- button instead of a real input field). prs_options has no incoming FK
-- from prs_responses (given_response is plain text, not a option_id FK),
-- so no patient-answer risk — but prs_option_translations DOES reference
-- option_id with ON DELETE RESTRICT, so its rows for these 4 options are
-- deleted first, then the options themselves.
-- Everything else here is a pure ADD: 14 new questions (PSQI/005-018), 56
-- new options, 14 new scale_question_map rows, and 70 new
-- disease_question_map rows (14 questions x the 5 diseases PSQI is
-- already mapped to). All INSERTs are idempotent (ON CONFLICT DO NOTHING)
-- — safe to re-run; the DELETE is also safe to re-run (no-op once applied).
--
-- Matches Data/prs_questions_rows.csv, prs_questions_rows_v1.csv,
-- prs_options_rows.csv, prs_scale_question_map_rows.csv and
-- prs_disease_question_map_rows.csv exactly — this file was generated
-- from the diff between those CSVs and git HEAD, not hand-typed.

BEGIN;

-- Q1/Q3 -> time picker; Q2/Q4 -> explicit numeric bounds
UPDATE prs_questions SET question_text = 'During the past month, what time have you usually gone to bed at night?', answer_type = 'time', min_value = NULL, max_value = NULL WHERE question_id = 'PSQI/001';
UPDATE prs_questions SET question_text = 'During the past month, how long (in minutes) has it usually taken you to fall asleep each night?', answer_type = 'number', min_value = 0, max_value = 300 WHERE question_id = 'PSQI/002';
UPDATE prs_questions SET question_text = 'During the past month, what time have you usually gotten up in the morning?', answer_type = 'time', min_value = NULL, max_value = NULL WHERE question_id = 'PSQI/003';
UPDATE prs_questions SET question_text = 'During the past month, how many hours of actual sleep did you get at night?', answer_type = 'number', min_value = 0, max_value = 24 WHERE question_id = 'PSQI/004';

-- Q1-Q4 are free-input fields (time picker / plain number) — no button
-- choices belong on them at all, so their placeholder/min-max option rows
-- are removed outright rather than updated. prs_option_translations has
-- fk_prs_option_translations_option_id ON DELETE RESTRICT (not CASCADE) —
-- its rows for these option_ids must go first or the DELETE below 23001s.
DELETE FROM prs_option_translations WHERE option_id IN ('PSQI/001/01', 'PSQI/002/01', 'PSQI/002/02', 'PSQI/003/01', 'PSQI/004/01', 'PSQI/004/02');
DELETE FROM prs_options WHERE option_id IN ('PSQI/001/01', 'PSQI/002/01', 'PSQI/002/02', 'PSQI/003/01', 'PSQI/004/01', 'PSQI/004/02');

-- existing option rows whose label/value/points changed

-- 14 new PSQI questions (005-018)
INSERT INTO prs_questions (question_id, question_code, disease_id, scale_id, ds_map_id, question_text, answer_type, min_value, max_value, is_required, skip_logic, display_order, is_common_scale, created_at) VALUES
  ('PSQI/005', 'PSQI/005', NULL, 'PSQI/2026', NULL, 'During the past month, how often have you had trouble sleeping because you cannot get to sleep within 30 minutes?', 'radio', NULL, NULL, TRUE, NULL, 5, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/006', 'PSQI/006', NULL, 'PSQI/2026', NULL, 'During the past month, how often have you had trouble sleeping because you wake up in the middle of the night or early morning?', 'radio', NULL, NULL, TRUE, NULL, 6, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/007', 'PSQI/007', NULL, 'PSQI/2026', NULL, 'During the past month, how often have you had trouble sleeping because you have to get up to use the bathroom?', 'radio', NULL, NULL, TRUE, NULL, 7, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/008', 'PSQI/008', NULL, 'PSQI/2026', NULL, 'During the past month, how often have you had trouble sleeping because you cannot breathe comfortably?', 'radio', NULL, NULL, TRUE, NULL, 8, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/009', 'PSQI/009', NULL, 'PSQI/2026', NULL, 'During the past month, how often have you had trouble sleeping because you cough or snore loudly?', 'radio', NULL, NULL, TRUE, NULL, 9, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/010', 'PSQI/010', NULL, 'PSQI/2026', NULL, 'During the past month, how often have you had trouble sleeping because you feel too cold?', 'radio', NULL, NULL, TRUE, NULL, 10, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/011', 'PSQI/011', NULL, 'PSQI/2026', NULL, 'During the past month, how often have you had trouble sleeping because you feel too hot?', 'radio', NULL, NULL, TRUE, NULL, 11, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/012', 'PSQI/012', NULL, 'PSQI/2026', NULL, 'During the past month, how often have you had trouble sleeping because you have bad dreams?', 'radio', NULL, NULL, TRUE, NULL, 12, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/013', 'PSQI/013', NULL, 'PSQI/2026', NULL, 'During the past month, how often have you had trouble sleeping because you have pain?', 'radio', NULL, NULL, TRUE, NULL, 13, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/014', 'PSQI/014', NULL, 'PSQI/2026', NULL, 'During the past month, how often have you had trouble sleeping for any other reason?', 'radio', NULL, NULL, TRUE, NULL, 14, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/015', 'PSQI/015', NULL, 'PSQI/2026', NULL, 'During the past month, how often have you taken medicine to help you sleep (prescribed or "over the counter")?', 'radio', NULL, NULL, TRUE, NULL, 15, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/016', 'PSQI/016', NULL, 'PSQI/2026', NULL, 'During the past month, how often have you had trouble staying awake while driving, eating meals, or engaging in social activity?', 'radio', NULL, NULL, TRUE, NULL, 16, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/017', 'PSQI/017', NULL, 'PSQI/2026', NULL, 'During the past month, how much of a problem has it been for you to keep up enough enthusiasm to get things done?', 'radio', NULL, NULL, TRUE, NULL, 17, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/018', 'PSQI/018', NULL, 'PSQI/2026', NULL, 'During the past month, how would you rate your sleep quality overall?', 'radio', NULL, NULL, TRUE, NULL, 18, TRUE, '2026-09-04 00:00:00+00')
ON CONFLICT (question_id) DO NOTHING;

-- 56 new options for those 14 questions (4 each)
INSERT INTO prs_options (option_id, question_id, option_label, option_value, points, display_order, status, created_at) VALUES
  ('PSQI/005/01', 'PSQI/005', 'Not during the past month', '0', 0, 1, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/005/02', 'PSQI/005', 'Less than once a week', '1', 1, 2, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/005/03', 'PSQI/005', 'Once or twice a week', '2', 2, 3, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/005/04', 'PSQI/005', 'Three or more times a week', '3', 3, 4, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/006/01', 'PSQI/006', 'Not during the past month', '0', 0, 1, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/006/02', 'PSQI/006', 'Less than once a week', '1', 1, 2, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/006/03', 'PSQI/006', 'Once or twice a week', '2', 2, 3, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/006/04', 'PSQI/006', 'Three or more times a week', '3', 3, 4, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/007/01', 'PSQI/007', 'Not during the past month', '0', 0, 1, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/007/02', 'PSQI/007', 'Less than once a week', '1', 1, 2, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/007/03', 'PSQI/007', 'Once or twice a week', '2', 2, 3, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/007/04', 'PSQI/007', 'Three or more times a week', '3', 3, 4, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/008/01', 'PSQI/008', 'Not during the past month', '0', 0, 1, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/008/02', 'PSQI/008', 'Less than once a week', '1', 1, 2, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/008/03', 'PSQI/008', 'Once or twice a week', '2', 2, 3, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/008/04', 'PSQI/008', 'Three or more times a week', '3', 3, 4, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/009/01', 'PSQI/009', 'Not during the past month', '0', 0, 1, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/009/02', 'PSQI/009', 'Less than once a week', '1', 1, 2, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/009/03', 'PSQI/009', 'Once or twice a week', '2', 2, 3, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/009/04', 'PSQI/009', 'Three or more times a week', '3', 3, 4, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/010/01', 'PSQI/010', 'Not during the past month', '0', 0, 1, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/010/02', 'PSQI/010', 'Less than once a week', '1', 1, 2, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/010/03', 'PSQI/010', 'Once or twice a week', '2', 2, 3, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/010/04', 'PSQI/010', 'Three or more times a week', '3', 3, 4, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/011/01', 'PSQI/011', 'Not during the past month', '0', 0, 1, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/011/02', 'PSQI/011', 'Less than once a week', '1', 1, 2, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/011/03', 'PSQI/011', 'Once or twice a week', '2', 2, 3, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/011/04', 'PSQI/011', 'Three or more times a week', '3', 3, 4, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/012/01', 'PSQI/012', 'Not during the past month', '0', 0, 1, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/012/02', 'PSQI/012', 'Less than once a week', '1', 1, 2, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/012/03', 'PSQI/012', 'Once or twice a week', '2', 2, 3, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/012/04', 'PSQI/012', 'Three or more times a week', '3', 3, 4, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/013/01', 'PSQI/013', 'Not during the past month', '0', 0, 1, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/013/02', 'PSQI/013', 'Less than once a week', '1', 1, 2, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/013/03', 'PSQI/013', 'Once or twice a week', '2', 2, 3, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/013/04', 'PSQI/013', 'Three or more times a week', '3', 3, 4, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/014/01', 'PSQI/014', 'Not during the past month', '0', 0, 1, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/014/02', 'PSQI/014', 'Less than once a week', '1', 1, 2, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/014/03', 'PSQI/014', 'Once or twice a week', '2', 2, 3, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/014/04', 'PSQI/014', 'Three or more times a week', '3', 3, 4, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/015/01', 'PSQI/015', 'Not during the past month', '0', 0, 1, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/015/02', 'PSQI/015', 'Less than once a week', '1', 1, 2, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/015/03', 'PSQI/015', 'Once or twice a week', '2', 2, 3, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/015/04', 'PSQI/015', 'Three or more times a week', '3', 3, 4, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/016/01', 'PSQI/016', 'Not during the past month', '0', 0, 1, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/016/02', 'PSQI/016', 'Less than once a week', '1', 1, 2, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/016/03', 'PSQI/016', 'Once or twice a week', '2', 2, 3, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/016/04', 'PSQI/016', 'Three or more times a week', '3', 3, 4, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/017/01', 'PSQI/017', 'No problem at all', '0', 0, 1, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/017/02', 'PSQI/017', 'Only a very slight problem', '1', 1, 2, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/017/03', 'PSQI/017', 'Somewhat of a problem', '2', 2, 3, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/017/04', 'PSQI/017', 'A very big problem', '3', 3, 4, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/018/01', 'PSQI/018', 'Very good', '0', 0, 1, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/018/02', 'PSQI/018', 'Fairly good', '1', 1, 2, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/018/03', 'PSQI/018', 'Fairly bad', '2', 2, 3, TRUE, '2026-09-04 00:00:00+00'),
  ('PSQI/018/04', 'PSQI/018', 'Very bad', '3', 3, 4, TRUE, '2026-09-04 00:00:00+00')
ON CONFLICT (option_id) DO NOTHING;

-- 14 new scale-question map rows
INSERT INTO prs_scale_question_map (sq_map_id, scale_id, question_id, display_order, created_at) VALUES
  ('PSQI/2026/PSQI/005', 'PSQI/2026', 'PSQI/005', 5, '2026-09-04 00:00:00+00'),
  ('PSQI/2026/PSQI/006', 'PSQI/2026', 'PSQI/006', 6, '2026-09-04 00:00:00+00'),
  ('PSQI/2026/PSQI/007', 'PSQI/2026', 'PSQI/007', 7, '2026-09-04 00:00:00+00'),
  ('PSQI/2026/PSQI/008', 'PSQI/2026', 'PSQI/008', 8, '2026-09-04 00:00:00+00'),
  ('PSQI/2026/PSQI/009', 'PSQI/2026', 'PSQI/009', 9, '2026-09-04 00:00:00+00'),
  ('PSQI/2026/PSQI/010', 'PSQI/2026', 'PSQI/010', 10, '2026-09-04 00:00:00+00'),
  ('PSQI/2026/PSQI/011', 'PSQI/2026', 'PSQI/011', 11, '2026-09-04 00:00:00+00'),
  ('PSQI/2026/PSQI/012', 'PSQI/2026', 'PSQI/012', 12, '2026-09-04 00:00:00+00'),
  ('PSQI/2026/PSQI/013', 'PSQI/2026', 'PSQI/013', 13, '2026-09-04 00:00:00+00'),
  ('PSQI/2026/PSQI/014', 'PSQI/2026', 'PSQI/014', 14, '2026-09-04 00:00:00+00'),
  ('PSQI/2026/PSQI/015', 'PSQI/2026', 'PSQI/015', 15, '2026-09-04 00:00:00+00'),
  ('PSQI/2026/PSQI/016', 'PSQI/2026', 'PSQI/016', 16, '2026-09-04 00:00:00+00'),
  ('PSQI/2026/PSQI/017', 'PSQI/2026', 'PSQI/017', 17, '2026-09-04 00:00:00+00'),
  ('PSQI/2026/PSQI/018', 'PSQI/2026', 'PSQI/018', 18, '2026-09-04 00:00:00+00')
ON CONFLICT (sq_map_id) DO NOTHING;

-- 70 new disease-question map rows (14 questions x 5 diseases)
INSERT INTO prs_disease_question_map (dq_map_id, disease_id, question_id, display_order, created_at) VALUES
  ('CHRONICPAIN/2026/PSQI/005', 'CHRONICPAIN/2026', 'PSQI/005', 8005, '2026-05-28 09:02:29.97722+00'),
  ('CHRONICPAIN/2026/PSQI/006', 'CHRONICPAIN/2026', 'PSQI/006', 8006, '2026-05-28 09:02:29.97722+00'),
  ('CHRONICPAIN/2026/PSQI/007', 'CHRONICPAIN/2026', 'PSQI/007', 8007, '2026-05-28 09:02:29.97722+00'),
  ('CHRONICPAIN/2026/PSQI/008', 'CHRONICPAIN/2026', 'PSQI/008', 8008, '2026-05-28 09:02:29.97722+00'),
  ('CHRONICPAIN/2026/PSQI/009', 'CHRONICPAIN/2026', 'PSQI/009', 8009, '2026-05-28 09:02:29.97722+00'),
  ('CHRONICPAIN/2026/PSQI/010', 'CHRONICPAIN/2026', 'PSQI/010', 8010, '2026-05-28 09:02:29.97722+00'),
  ('CHRONICPAIN/2026/PSQI/011', 'CHRONICPAIN/2026', 'PSQI/011', 8011, '2026-05-28 09:02:29.97722+00'),
  ('CHRONICPAIN/2026/PSQI/012', 'CHRONICPAIN/2026', 'PSQI/012', 8012, '2026-05-28 09:02:29.97722+00'),
  ('CHRONICPAIN/2026/PSQI/013', 'CHRONICPAIN/2026', 'PSQI/013', 8013, '2026-05-28 09:02:29.97722+00'),
  ('CHRONICPAIN/2026/PSQI/014', 'CHRONICPAIN/2026', 'PSQI/014', 8014, '2026-05-28 09:02:29.97722+00'),
  ('CHRONICPAIN/2026/PSQI/015', 'CHRONICPAIN/2026', 'PSQI/015', 8015, '2026-05-28 09:02:29.97722+00'),
  ('CHRONICPAIN/2026/PSQI/016', 'CHRONICPAIN/2026', 'PSQI/016', 8016, '2026-05-28 09:02:29.97722+00'),
  ('CHRONICPAIN/2026/PSQI/017', 'CHRONICPAIN/2026', 'PSQI/017', 8017, '2026-05-28 09:02:29.97722+00'),
  ('CHRONICPAIN/2026/PSQI/018', 'CHRONICPAIN/2026', 'PSQI/018', 8018, '2026-05-28 09:02:29.97722+00'),
  ('DEPRESSION/ANXIETY/2026/PSQI/005', 'DEPRESSION/ANXIETY/2026', 'PSQI/005', 7005, '2026-05-28 09:02:29.97722+00'),
  ('DEPRESSION/ANXIETY/2026/PSQI/006', 'DEPRESSION/ANXIETY/2026', 'PSQI/006', 7006, '2026-05-28 09:02:29.97722+00'),
  ('DEPRESSION/ANXIETY/2026/PSQI/007', 'DEPRESSION/ANXIETY/2026', 'PSQI/007', 7007, '2026-05-28 09:02:29.97722+00'),
  ('DEPRESSION/ANXIETY/2026/PSQI/008', 'DEPRESSION/ANXIETY/2026', 'PSQI/008', 7008, '2026-05-28 09:02:29.97722+00'),
  ('DEPRESSION/ANXIETY/2026/PSQI/009', 'DEPRESSION/ANXIETY/2026', 'PSQI/009', 7009, '2026-05-28 09:02:29.97722+00'),
  ('DEPRESSION/ANXIETY/2026/PSQI/010', 'DEPRESSION/ANXIETY/2026', 'PSQI/010', 7010, '2026-05-28 09:02:29.97722+00'),
  ('DEPRESSION/ANXIETY/2026/PSQI/011', 'DEPRESSION/ANXIETY/2026', 'PSQI/011', 7011, '2026-05-28 09:02:29.97722+00'),
  ('DEPRESSION/ANXIETY/2026/PSQI/012', 'DEPRESSION/ANXIETY/2026', 'PSQI/012', 7012, '2026-05-28 09:02:29.97722+00'),
  ('DEPRESSION/ANXIETY/2026/PSQI/013', 'DEPRESSION/ANXIETY/2026', 'PSQI/013', 7013, '2026-05-28 09:02:29.97722+00'),
  ('DEPRESSION/ANXIETY/2026/PSQI/014', 'DEPRESSION/ANXIETY/2026', 'PSQI/014', 7014, '2026-05-28 09:02:29.97722+00'),
  ('DEPRESSION/ANXIETY/2026/PSQI/015', 'DEPRESSION/ANXIETY/2026', 'PSQI/015', 7015, '2026-05-28 09:02:29.97722+00'),
  ('DEPRESSION/ANXIETY/2026/PSQI/016', 'DEPRESSION/ANXIETY/2026', 'PSQI/016', 7016, '2026-05-28 09:02:29.97722+00'),
  ('DEPRESSION/ANXIETY/2026/PSQI/017', 'DEPRESSION/ANXIETY/2026', 'PSQI/017', 7017, '2026-05-28 09:02:29.97722+00'),
  ('DEPRESSION/ANXIETY/2026/PSQI/018', 'DEPRESSION/ANXIETY/2026', 'PSQI/018', 7018, '2026-05-28 09:02:29.97722+00'),
  ('INSOMNIA/2026/PSQI/005', 'INSOMNIA/2026', 'PSQI/005', 5005, '2026-05-28 09:02:29.97722+00'),
  ('INSOMNIA/2026/PSQI/006', 'INSOMNIA/2026', 'PSQI/006', 5006, '2026-05-28 09:02:29.97722+00'),
  ('INSOMNIA/2026/PSQI/007', 'INSOMNIA/2026', 'PSQI/007', 5007, '2026-05-28 09:02:29.97722+00'),
  ('INSOMNIA/2026/PSQI/008', 'INSOMNIA/2026', 'PSQI/008', 5008, '2026-05-28 09:02:29.97722+00'),
  ('INSOMNIA/2026/PSQI/009', 'INSOMNIA/2026', 'PSQI/009', 5009, '2026-05-28 09:02:29.97722+00'),
  ('INSOMNIA/2026/PSQI/010', 'INSOMNIA/2026', 'PSQI/010', 5010, '2026-05-28 09:02:29.97722+00'),
  ('INSOMNIA/2026/PSQI/011', 'INSOMNIA/2026', 'PSQI/011', 5011, '2026-05-28 09:02:29.97722+00'),
  ('INSOMNIA/2026/PSQI/012', 'INSOMNIA/2026', 'PSQI/012', 5012, '2026-05-28 09:02:29.97722+00'),
  ('INSOMNIA/2026/PSQI/013', 'INSOMNIA/2026', 'PSQI/013', 5013, '2026-05-28 09:02:29.97722+00'),
  ('INSOMNIA/2026/PSQI/014', 'INSOMNIA/2026', 'PSQI/014', 5014, '2026-05-28 09:02:29.97722+00'),
  ('INSOMNIA/2026/PSQI/015', 'INSOMNIA/2026', 'PSQI/015', 5015, '2026-05-28 09:02:29.97722+00'),
  ('INSOMNIA/2026/PSQI/016', 'INSOMNIA/2026', 'PSQI/016', 5016, '2026-05-28 09:02:29.97722+00'),
  ('INSOMNIA/2026/PSQI/017', 'INSOMNIA/2026', 'PSQI/017', 5017, '2026-05-28 09:02:29.97722+00'),
  ('INSOMNIA/2026/PSQI/018', 'INSOMNIA/2026', 'PSQI/018', 5018, '2026-05-28 09:02:29.97722+00'),
  ('MIGRAINE/2026/PSQI/005', 'MIGRAINE/2026', 'PSQI/005', 7005, '2026-05-28 09:02:29.97722+00'),
  ('MIGRAINE/2026/PSQI/006', 'MIGRAINE/2026', 'PSQI/006', 7006, '2026-05-28 09:02:29.97722+00'),
  ('MIGRAINE/2026/PSQI/007', 'MIGRAINE/2026', 'PSQI/007', 7007, '2026-05-28 09:02:29.97722+00'),
  ('MIGRAINE/2026/PSQI/008', 'MIGRAINE/2026', 'PSQI/008', 7008, '2026-05-28 09:02:29.97722+00'),
  ('MIGRAINE/2026/PSQI/009', 'MIGRAINE/2026', 'PSQI/009', 7009, '2026-05-28 09:02:29.97722+00'),
  ('MIGRAINE/2026/PSQI/010', 'MIGRAINE/2026', 'PSQI/010', 7010, '2026-05-28 09:02:29.97722+00'),
  ('MIGRAINE/2026/PSQI/011', 'MIGRAINE/2026', 'PSQI/011', 7011, '2026-05-28 09:02:29.97722+00'),
  ('MIGRAINE/2026/PSQI/012', 'MIGRAINE/2026', 'PSQI/012', 7012, '2026-05-28 09:02:29.97722+00'),
  ('MIGRAINE/2026/PSQI/013', 'MIGRAINE/2026', 'PSQI/013', 7013, '2026-05-28 09:02:29.97722+00'),
  ('MIGRAINE/2026/PSQI/014', 'MIGRAINE/2026', 'PSQI/014', 7014, '2026-05-28 09:02:29.97722+00'),
  ('MIGRAINE/2026/PSQI/015', 'MIGRAINE/2026', 'PSQI/015', 7015, '2026-05-28 09:02:29.97722+00'),
  ('MIGRAINE/2026/PSQI/016', 'MIGRAINE/2026', 'PSQI/016', 7016, '2026-05-28 09:02:29.97722+00'),
  ('MIGRAINE/2026/PSQI/017', 'MIGRAINE/2026', 'PSQI/017', 7017, '2026-05-28 09:02:29.97722+00'),
  ('MIGRAINE/2026/PSQI/018', 'MIGRAINE/2026', 'PSQI/018', 7018, '2026-05-28 09:02:29.97722+00'),
  ('TINNITUS/2026/PSQI/005', 'TINNITUS/2026', 'PSQI/005', 6005, '2026-05-28 09:02:29.97722+00'),
  ('TINNITUS/2026/PSQI/006', 'TINNITUS/2026', 'PSQI/006', 6006, '2026-05-28 09:02:29.97722+00'),
  ('TINNITUS/2026/PSQI/007', 'TINNITUS/2026', 'PSQI/007', 6007, '2026-05-28 09:02:29.97722+00'),
  ('TINNITUS/2026/PSQI/008', 'TINNITUS/2026', 'PSQI/008', 6008, '2026-05-28 09:02:29.97722+00'),
  ('TINNITUS/2026/PSQI/009', 'TINNITUS/2026', 'PSQI/009', 6009, '2026-05-28 09:02:29.97722+00'),
  ('TINNITUS/2026/PSQI/010', 'TINNITUS/2026', 'PSQI/010', 6010, '2026-05-28 09:02:29.97722+00'),
  ('TINNITUS/2026/PSQI/011', 'TINNITUS/2026', 'PSQI/011', 6011, '2026-05-28 09:02:29.97722+00'),
  ('TINNITUS/2026/PSQI/012', 'TINNITUS/2026', 'PSQI/012', 6012, '2026-05-28 09:02:29.97722+00'),
  ('TINNITUS/2026/PSQI/013', 'TINNITUS/2026', 'PSQI/013', 6013, '2026-05-28 09:02:29.97722+00'),
  ('TINNITUS/2026/PSQI/014', 'TINNITUS/2026', 'PSQI/014', 6014, '2026-05-28 09:02:29.97722+00'),
  ('TINNITUS/2026/PSQI/015', 'TINNITUS/2026', 'PSQI/015', 6015, '2026-05-28 09:02:29.97722+00'),
  ('TINNITUS/2026/PSQI/016', 'TINNITUS/2026', 'PSQI/016', 6016, '2026-05-28 09:02:29.97722+00'),
  ('TINNITUS/2026/PSQI/017', 'TINNITUS/2026', 'PSQI/017', 6017, '2026-05-28 09:02:29.97722+00'),
  ('TINNITUS/2026/PSQI/018', 'TINNITUS/2026', 'PSQI/018', 6018, '2026-05-28 09:02:29.97722+00')
ON CONFLICT (dq_map_id) DO NOTHING;

COMMIT;
