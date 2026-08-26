-- 63_seed_anamnesis_registration_questionnaire.sql
--
-- Replaces the placeholder 4-question registration set from 62_ with the
-- real spec: Documents/Anaba_Clinic_Onboarding_Questionnaire_Spec.pdf
-- ("Anaba Clinic — Patient Onboarding Questionnaire Spec", Post-Registration
-- Clinical Intake, sequence: Registration -> Password Setup -> EQ-5D-5L ->
-- this questionnaire).
--
-- 7 rows (Q1-Q5, plus conditional Q2a and Q5a), all type='registration',
-- sections 9-13 continuing after main's 1-8 / display_order 22-28 continuing
-- after main's max 21. Field IDs from the spec are reused verbatim as
-- question_code (none collide with the existing main question_codes).
--
-- answer_type mapping to this schema's existing vocabulary: spec's
-- "multi-select dropdown" -> 'checkbox' (same choice made for the existing
-- secondary_symptoms question — this schema has no native multi-select
-- dropdown widget); "single-select dropdown" -> 'select'; Q5a's free-text
-- conditional field -> 'conditional_text' (same pattern as operations_details
-- / mri_details).
--
-- Q5a's spec condition is an OR across two treatment_status values
-- ("Yes, currently" OR "Yes, previously but not now") — this schema's
-- depends_on_value only supports a single equality value, not a set. Left
-- depends_on_question_id/depends_on_value NULL (always visible, optional)
-- rather than silently hiding it for one of the two spec'd trigger values.
-- Flagged here, not silently narrowed.
--
-- APPLY ORDER: after 62. Supersedes 62's INSERT for the same 4 question_ids.

BEGIN;

DELETE FROM reference."anamnesis_options"
    WHERE "question_id" IN ('ANA/S09/Q001', 'ANA/S10/Q001', 'ANA/S11/Q001', 'ANA/S12/Q001');

DELETE FROM reference."anamnesis_questions"
    WHERE "question_id" IN ('ANA/S09/Q001', 'ANA/S10/Q001', 'ANA/S11/Q001', 'ANA/S12/Q001');

INSERT INTO reference."anamnesis_questions"
    (question_id, type, section_number, section_title, question_code, question_text,
     answer_type, is_required, display_order, depends_on_question_id, depends_on_value, helper_text)
VALUES
('ANA/S09/Q001', 'registration', 9, 'Primary Symptom(s)', 'symptom_primary',
 'What symptoms are you experiencing?',
 'checkbox', TRUE, 22, NULL, NULL, 'Select all that apply'),

('ANA/S10/Q001', 'registration', 10, 'Prior Neuromodulation Treatment', 'neuromod_prior',
 'Have you had prior neuromodulation treatment?',
 'select', TRUE, 23, NULL, NULL, NULL),

('ANA/S10/Q002', 'registration', 10, 'Prior Neuromodulation Treatment', 'neuromod_type',
 'What type of prior neuromodulation treatment?',
 'select', FALSE, 24, 'ANA/S10/Q001', 'yes', NULL),

('ANA/S11/Q001', 'registration', 11, 'Diagnosed Condition', 'condition_diagnosed',
 'What is your diagnosed condition?',
 'select', TRUE, 25, NULL, NULL, NULL),

('ANA/S12/Q001', 'registration', 12, 'Current Symptom Severity', 'symptom_severity',
 'How severe are your current symptoms?',
 'select', TRUE, 26, NULL, NULL, 'Uses the same 5-point scale language as EQ-5D-5L'),

('ANA/S13/Q001', 'registration', 13, 'Current Medication / Treatment Status', 'treatment_status',
 'Are you currently on medication or treatment for this condition?',
 'select', TRUE, 27, NULL, NULL, NULL),

('ANA/S13/Q002', 'registration', 13, 'Current Medication / Treatment Status', 'medication_name',
 'Medication name',
 'conditional_text', FALSE, 28, NULL, NULL, 'Optional — only if currently or previously on medication')

ON CONFLICT (question_id) DO UPDATE SET
    question_text  = EXCLUDED.question_text,
    helper_text    = EXCLUDED.helper_text,
    section_title  = EXCLUDED.section_title,
    display_order  = EXCLUDED.display_order;

INSERT INTO reference."anamnesis_options"
    (option_id, question_id, option_label, option_value, display_order)
VALUES
-- Q1 symptom_primary
('ANA/S09/Q001/O01', 'ANA/S09/Q001', 'Anxiety', 'anxiety', 1),
('ANA/S09/Q001/O02', 'ANA/S09/Q001', 'Depression / Low mood', 'depression_low_mood', 2),
('ANA/S09/Q001/O03', 'ANA/S09/Q001', 'Chronic pain', 'chronic_pain', 3),
('ANA/S09/Q001/O04', 'ANA/S09/Q001', 'Sleep problems / Insomnia', 'sleep_problems', 4),
('ANA/S09/Q001/O05', 'ANA/S09/Q001', 'Difficulty concentrating / Focus', 'difficulty_concentrating', 5),
('ANA/S09/Q001/O06', 'ANA/S09/Q001', 'Tremors or movement difficulty', 'tremors', 6),
('ANA/S09/Q001/O07', 'ANA/S09/Q001', 'Memory issues', 'memory_issues', 7),
('ANA/S09/Q001/O08', 'ANA/S09/Q001', 'Fatigue', 'fatigue', 8),
('ANA/S09/Q001/O09', 'ANA/S09/Q001', 'Other', 'other', 9),
('ANA/S09/Q001/O10', 'ANA/S09/Q001', 'Not sure', 'not_sure', 10),

-- Q2 neuromod_prior
('ANA/S10/Q001/O01', 'ANA/S10/Q001', 'Yes', 'yes', 1),
('ANA/S10/Q001/O02', 'ANA/S10/Q001', 'No', 'no', 2),
('ANA/S10/Q001/O03', 'ANA/S10/Q001', 'Not sure', 'not_sure', 3),

-- Q2a neuromod_type
('ANA/S10/Q002/O01', 'ANA/S10/Q002', 'TMS (Transcranial Magnetic Stimulation)', 'tms', 1),
('ANA/S10/Q002/O02', 'ANA/S10/Q002', 'tDCS (Transcranial Direct Current Stimulation)', 'tdcs', 2),
('ANA/S10/Q002/O03', 'ANA/S10/Q002', 'ECT (Electroconvulsive Therapy)', 'ect', 3),
('ANA/S10/Q002/O04', 'ANA/S10/Q002', 'DBS (Deep Brain Stimulation)', 'dbs', 4),
('ANA/S10/Q002/O05', 'ANA/S10/Q002', 'Vagus Nerve Stimulation', 'vagus_nerve_stimulation', 5),
('ANA/S10/Q002/O06', 'ANA/S10/Q002', 'Other', 'other', 6),
('ANA/S10/Q002/O07', 'ANA/S10/Q002', 'Not sure', 'not_sure', 7),

-- Q3 condition_diagnosed
('ANA/S11/Q001/O01', 'ANA/S11/Q001', 'Depression', 'depression', 1),
('ANA/S11/Q001/O02', 'ANA/S11/Q001', 'Anxiety disorder', 'anxiety_disorder', 2),
('ANA/S11/Q001/O03', 'ANA/S11/Q001', 'ADHD', 'adhd', 3),
('ANA/S11/Q001/O04', 'ANA/S11/Q001', 'Parkinson''s disease', 'parkinsons_disease', 4),
('ANA/S11/Q001/O05', 'ANA/S11/Q001', 'OCD', 'ocd', 5),
('ANA/S11/Q001/O06', 'ANA/S11/Q001', 'PTSD', 'ptsd', 6),
('ANA/S11/Q001/O07', 'ANA/S11/Q001', 'Bipolar disorder', 'bipolar_disorder', 7),
('ANA/S11/Q001/O08', 'ANA/S11/Q001', 'Chronic pain', 'chronic_pain', 8),
('ANA/S11/Q001/O09', 'ANA/S11/Q001', 'Other', 'other', 9),
('ANA/S11/Q001/O10', 'ANA/S11/Q001', 'Not sure / Not diagnosed', 'not_sure_not_diagnosed', 10),

-- Q4 symptom_severity
('ANA/S12/Q001/O01', 'ANA/S12/Q001', 'None', 'none', 1),
('ANA/S12/Q001/O02', 'ANA/S12/Q001', 'Slight', 'slight', 2),
('ANA/S12/Q001/O03', 'ANA/S12/Q001', 'Moderate', 'moderate', 3),
('ANA/S12/Q001/O04', 'ANA/S12/Q001', 'Severe', 'severe', 4),
('ANA/S12/Q001/O05', 'ANA/S12/Q001', 'Extreme', 'extreme', 5),

-- Q5 treatment_status
('ANA/S13/Q001/O01', 'ANA/S13/Q001', 'Yes, currently', 'yes_currently', 1),
('ANA/S13/Q001/O02', 'ANA/S13/Q001', 'Yes, previously but not now', 'yes_previously', 2),
('ANA/S13/Q001/O03', 'ANA/S13/Q001', 'No', 'no', 3),
('ANA/S13/Q001/O04', 'ANA/S13/Q001', 'Not sure', 'not_sure', 4)

ON CONFLICT (option_id) DO UPDATE SET
    option_label  = EXCLUDED.option_label,
    option_value  = EXCLUDED.option_value,
    display_order = EXCLUDED.display_order;

COMMIT;
