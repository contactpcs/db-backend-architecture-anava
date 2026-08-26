-- 62_seed_anamnesis_registration_questions.sql
--
-- First 4 registration-type (type='main' -> 'registration', see 60_) questions
-- — one-time background/history, asked only at signup. Sections 9-12,
-- display_order 22-25, continuing after the existing main catalog's max (8 /
-- 21). New question_ids, no collision with ANA/S01-S08.
--
-- APPLY ORDER: after 61.

BEGIN;

INSERT INTO reference."anamnesis_questions"
    (question_id, type, section_number, section_title, question_code, question_text,
     answer_type, is_required, display_order, depends_on_question_id, depends_on_value, helper_text)
VALUES
('ANA/S09/Q001', 'registration', 9, 'Medical History', 'past_diagnoses',
 'Do you have any existing medical diagnoses?',
 'textarea', TRUE, 22, NULL, NULL, 'e.g. epilepsy, stroke, ADHD, autism, depression'),

('ANA/S10/Q001', 'registration', 10, 'Operations / Surgeries', 'reg_has_operations',
 'Have you had any operations or surgeries?',
 'radio', TRUE, 23, NULL, NULL, NULL),

('ANA/S11/Q001', 'registration', 11, 'Medications & Supplements', 'reg_current_medications',
 'Current medications and supplements',
 'textarea', FALSE, 24, NULL, NULL, 'List all current medications and supplements with dosages'),

('ANA/S12/Q001', 'registration', 12, 'Brain MRI & Other Scans', 'reg_has_brain_mri',
 'Have you had a Brain MRI or other brain scan?',
 'radio', TRUE, 25, NULL, NULL, NULL)

ON CONFLICT (question_id) DO UPDATE SET
    question_text  = EXCLUDED.question_text,
    helper_text    = EXCLUDED.helper_text,
    section_title  = EXCLUDED.section_title,
    display_order  = EXCLUDED.display_order;

INSERT INTO reference."anamnesis_options"
    (option_id, question_id, option_label, option_value, display_order)
VALUES
('ANA/S10/Q001/O01', 'ANA/S10/Q001', 'Yes', 'yes', 1),
('ANA/S10/Q001/O02', 'ANA/S10/Q001', 'No', 'no', 2),
('ANA/S12/Q001/O01', 'ANA/S12/Q001', 'Yes', 'yes', 1),
('ANA/S12/Q001/O02', 'ANA/S12/Q001', 'No', 'no', 2)

ON CONFLICT (option_id) DO UPDATE SET
    option_label  = EXCLUDED.option_label,
    option_value  = EXCLUDED.option_value,
    display_order = EXCLUDED.display_order;

COMMIT;
