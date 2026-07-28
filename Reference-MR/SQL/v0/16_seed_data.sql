-- ============================================================
-- Anava Clinic — DB Schema
-- File 16: Seed Data
-- 1. consent_templates (8 types, version 1)
-- 2. prs_diseases (14 neurological conditions)
-- 3. prs_scales (41 clinical instruments)
-- 4. prs_disease_scale_map
-- 5. anamnesis_questions + anamnesis_options
--
-- Idempotent: safe to re-run; ON CONFLICT clauses prevent duplicates.
-- ============================================================

BEGIN;

-- ============================================================
-- CONSENT TEMPLATES (8 types)
-- ============================================================

INSERT INTO consent_templates (consent_type, version, title, content, is_active) VALUES
(
    'patient_onboarding', 1,
    'Patient Onboarding Consent',
    'I, the undersigned patient, consent to enroll in the Anava Clinic neurological care program. '
    'I understand that my personal and medical information will be collected and used for the purpose '
    'of providing neurological assessment, treatment planning, and follow-up care. I consent to the '
    'collection of EEG data, administration of standardized psychological rating scales (PRS), and '
    'other clinical assessments as determined by the clinical team. I understand that a Receptionist '
    'will witness this consent signing. I understand that my records will be retained permanently as '
    'required by medical record-keeping regulations. I have the right to exit the program at any time.',
    TRUE
),
(
    'patient_clinic_exit', 1,
    'Patient Clinic Exit Consent',
    'I, the undersigned patient, voluntarily consent to discharge from Anava Clinic. I understand '
    'that my clinical records will be archived permanently in read-only status. I acknowledge that '
    'the treatment program may be incomplete and that I am exiting at my own discretion. I understand '
    'that re-joining the clinic in the future will require a new registration process.',
    TRUE
),
(
    'patient_clinic_transfer', 1,
    'Patient Clinic Transfer Consent',
    'I, the undersigned patient, consent to the transfer of my complete clinical records from '
    '[FROM_CLINIC] to [TO_CLINIC]. I understand this transfer is being facilitated due to the '
    'closure of [FROM_CLINIC]. I consent to the allocation of a new Doctor at the receiving clinic. '
    'I understand that my treatment will continue without interruption from where it was paused. '
    'My records at the original clinic will be retained permanently in read-only status.',
    TRUE
),
(
    'patient_relocation_transfer', 1,
    'Patient Relocation Transfer Consent',
    'I, the undersigned patient, consent to the transfer of my complete clinical records from '
    '[FROM_CLINIC] to [TO_CLINIC] due to my permanent relocation to a new region. I understand '
    'that this is not an exit from the Anava care program — my treatment will continue at the '
    'new clinic. I consent to the auto-allocation of a new Doctor at the receiving clinic. '
    'Any active appointment block will resume from its current position without restart. '
    'My records at the original clinic will be retained permanently in read-only status.',
    TRUE
),
(
    'staff_onboarding', 1,
    'Staff Onboarding Consent',
    'I, the undersigned staff member, consent to joining [CLINIC_NAME] as [ROLE]. I acknowledge '
    'receipt of the staff handbook and code of conduct. I understand my responsibilities regarding '
    'patient confidentiality, data protection, and clinical protocols as defined by Anava Clinic '
    'and Mana Health Sciences Group. I consent to the storage of my professional credentials '
    'and employment records in the Anava platform.',
    TRUE
),
(
    'staff_offboarding', 1,
    'Staff Offboarding Consent',
    'I, the undersigned staff member, acknowledge the termination of my association with '
    '[CLINIC_NAME] as [ROLE]. I understand that my access to the Anava platform will be '
    'revoked immediately upon signing this consent. I confirm that I have no outstanding '
    'patient responsibilities and that all pending clinical documentation has been completed. '
    'My employment records will be retained permanently as required by regulation.',
    TRUE
),
(
    'clinic_join_anava', 1,
    'Partner Clinic — Join Anava Network Consent',
    'We, the authorized representatives of [PARTNER_CLINIC_NAME], consent to joining the '
    'Anava Clinic partner network operated by Mana Health Sciences Group. We agree to operate '
    'under the Anava clinical protocols, patient care standards, and data governance framework. '
    'We acknowledge that Regional Admin oversight applies to our clinic operations and staff. '
    'We understand the obligations regarding patient record retention and data security.',
    TRUE
),
(
    'clinic_leave_anava', 1,
    'Partner Clinic — Leave Anava Network Consent',
    'We, the authorized representatives of [PARTNER_CLINIC_NAME], consent to the closure of '
    'our partnership with the Anava Clinic network operated by Mana Health Sciences Group. '
    'We acknowledge that all patient records must be transferred or archived before closure '
    'can be completed. We understand that data retention obligations continue after departure '
    'and that all records will remain in the Anava system in read-only status permanently.',
    TRUE
)
ON CONFLICT (consent_type, version) DO NOTHING;


-- ============================================================
-- PRS DISEASES — 14 conditions (v6 seed data, TEXT composite PKs)
-- disease_id format: 'DISEASENAME/2026'
-- Matches existing application code and seed_scales.py
-- ============================================================

INSERT INTO prs_diseases (disease_id, disease_code, disease_name, version, status) VALUES
('DEPRESSION/ANXIETY/2026',    'DEPRESSIONANXIETY',    'Depression/Anxiety',       'v1.0', TRUE),
('CHRONICPAIN/2026',           'CHRONICPAIN',          'Chronic Pain',             'v1.0', TRUE),
('FIBROMYALGIA/2026',          'FIBROMYALGIA',         'Fibromyalgia',             'v1.0', TRUE),
('MIGRAINE/2026',              'MIGRAINE',             'Migraine',                 'v1.0', TRUE),
('ATAXIA/2026',                'ATAXIA',               'Ataxia',                   'v1.0', TRUE),
('AFTERSTROKE/TBI/2026',       'AFTERSTROKETBI',       'After Stroke/TBI',         'v1.0', TRUE),
('DEMENTIA/2026',              'DEMENTIA',             'Dementia',                 'v1.0', TRUE),
('PARKINSONSDISEASE/2026',     'PARKINSONSDISEASE',    'Parkinson''s Disease',     'v1.0', TRUE),
('TINNITUS/2026',              'TINNITUS',             'Tinnitus',                 'v1.0', TRUE),
('INSOMNIA/2026',              'INSOMNIA',             'Insomnia',                 'v1.0', TRUE),
('MULTIPLESCLEROSIS/2026',     'MULTIPLESCLEROSIS',    'Multiple Sclerosis',       'v1.0', TRUE),
('ADHD/2026',                  'ADHD',                 'ADHD',                     'v1.0', TRUE),
('ALS/2026',                   'ALS',                  'ALS',                      'v1.0', TRUE),
('IRRITABLEBOWELDISEASE/2026', 'IRRITABLEBOWELDISEASE','Irritable Bowel Disease',  'v1.0', TRUE)
ON CONFLICT (disease_id) DO NOTHING;


-- ============================================================
-- PRS SCALES — 41 clinical instruments (v6 seed data)
-- scale_id format: 'SCALECODE/2026'
-- Matches existing application code and seed_scales.py
-- ============================================================

INSERT INTO prs_scales (scale_id, scale_code, scale_name, is_common_scale, num_diseases_used, applicable_for) VALUES
('AIS/2026',         'AIS',         'AIS - Athens Insomnia Scale',                                FALSE, 1,  'main_clinical'),
('ALSFRS-R/2026',    'ALSFRS-R',    'ALSFRS-R - ALS Functional Rating Scale - Revised',           FALSE, 1,  'main_clinical'),
('AMTS/2026',        'AMTS',        'AMTS - Abbreviated Mental Test Score',                       FALSE, 1,  'main_clinical'),
('ASRS-v1.1/2026',   'ASRS-v1.1',   'ASRS-v1.1 - Adult ADHD Self-Report Scale',                  FALSE, 1,  'main_clinical'),
('BDI-II/2026',      'BDI-II',      'BDI-II - Beck''s Depression Inventory Version 2',            TRUE,  5,  'main_clinical'),
('BARTHEL/2026',     'BARTHEL',     'Barthel Index',                                              TRUE,  2,  'main_clinical'),
('COMPASS-31/2026',  'COMPASS-31',  'COMPASS-31',                                                 TRUE,  14, 'all'),
('DASS-21/2026',     'DASS-21',     'DASS-21',                                                    TRUE,  11, 'all'),
('DHI/2026',         'DHI',         'DHI - Dizziness Handicap Inventory',                         TRUE,  2,  'main_clinical'),
('DN-4/2026',        'DN-4',        'DN-4',                                                       FALSE, 1,  'main_clinical'),
('DSRS/2026',        'DSRS',        'DSRS - Dementia Severity Rating Scale',                      FALSE, 1,  'main_clinical'),
('EQ-5D-5L/2026',    'EQ-5D-5L',    'EQ-5D-5L Health Questionnaire',                             TRUE,  12, 'all'),
('FFS/2026',         'FFS',         'FFS - Flinders Fatigue Scale',                               FALSE, 1,  'main_clinical'),
('FIQR/2026',        'FIQR',        'FIQR - Revised Fibromyalgia Impact Questionnaire',           FALSE, 1,  'main_clinical'),
('FSS/2026',         'FSS',         'FSS - Fatigue Severity Scale',                               FALSE, 1,  'main_clinical'),
('GAD-7/2026',       'GAD-7',       'GAD-7',                                                      TRUE,  5,  'general_registration'),
('GDS/2026',         'GDS',         'GDS - Global Deterioration Scale',                           FALSE, 1,  'main_clinical'),
('HDRS/2026',        'HDRS',        'HDRS - Hamilton Depression Rating Scale',                    FALSE, 1,  'main_clinical'),
('IADL/2026',        'IADL',        'IADL - Lawton Instrumental Activities of Daily Living Scale',FALSE, 1,  'main_clinical'),
('IBS-SSS/2026',     'IBS-SSS',     'IBS-SSS - IBS Symptom Severity Scale',                      FALSE, 1,  'main_clinical'),
('ISI/2026',         'ISI',         'ISI - Insomnia Severity Index',                              FALSE, 1,  'main_clinical'),
('KPS/2026',         'KPS',         'KPS - Karnofsky Performance Status Scale',                   FALSE, 1,  'main_clinical'),
('MADRS/2026',       'MADRS',       'MADRS - Montgomery and Asberg Depression Scale',             FALSE, 1,  'main_clinical'),
('MAS/2026',         'MAS',         'MAS - Modified Ashworth Scale',                              TRUE,  2,  'main_clinical'),
('MFIS/2026',        'MFIS',        'MFIS - Modified Fatigue Impact Scale',                       FALSE, 1,  'main_clinical'),
('MIDAS/2026',       'MIDAS',       'MIDAS - Migraine Disability Assessment',                     FALSE, 1,  'main_clinical'),
('MRC/2026',         'MRC',         'MRC - Medical Research Council Scale for Muscle Strength',   FALSE, 1,  'main_clinical'),
('MSQ/2026',         'MSQ',         'MSQ - Migraine-specific Quality of Life Questionnaire',      FALSE, 1,  'main_clinical'),
('MoCA/2026',        'MoCA',        'MoCA - Montreal Cognitive Assessment',                       TRUE,  4,  'main_clinical'),
('PDSS/2026',        'PDSS',        'PDSS - Parkinson''s Disease Sleep Scale',                    FALSE, 1,  'main_clinical'),
('PFS-16/2026',      'PFS-16',      'PFS-16 - Parkinson''s Disease Fatigue Scale',                FALSE, 1,  'main_clinical'),
('PSQI/2026',        'PSQI',        'PSQI - Pittsburgh Sleep Quality Index',                      TRUE,  5,  'general_registration'),
('PRS/2026',         'PRS',         'Pain Rating Scale',                                          TRUE,  4,  'main_clinical'),
('PainDETECT/2026',  'PainDETECT',  'PainDETECT',                                                 TRUE,  4,  'main_clinical'),
('SARA/2026',        'SARA',        'SARA - Scale for the Assessment and Rating of Ataxia',       TRUE,  2,  'main_clinical'),
('SNAP-IV/2026',     'SNAP-IV',     'SNAP-IV 26-Item Teacher and Parent Rating Scale',            FALSE, 1,  'main_clinical'),
('SS-QOL/2026',      'SS-QOL',      'SS-QOL - Stroke Specific Quality of Life Scale',             FALSE, 1,  'main_clinical'),
('SLEEP-50/2026',    'SLEEP-50',    'Sleep-50',                                                   FALSE, 1,  'main_clinical'),
('THI/2026',         'THI',         'THI - Tinnitus Handicap Inventory',                          FALSE, 1,  'main_clinical'),
('VAS/2026',         'VAS',         'VAS',                                                        FALSE, 1,  'main_clinical'),
('VVAS/2026',        'VVAS',        'VVAS - Visual Vertigo Analogue Scale',                       FALSE, 1,  'main_clinical')
ON CONFLICT (scale_id) DO NOTHING;


-- ============================================================
-- PRS DISEASE-SCALE MAP (v6 seed data)
-- ds_map_id format: 'DiseaseName/ScaleCode'
-- ============================================================

INSERT INTO prs_disease_scale_map (ds_map_id, disease_id, scale_id, display_order, is_required) VALUES
('Depression/Anxiety/EQ-5D-5L',               'DEPRESSION/ANXIETY/2026',    'EQ-5D-5L/2026',   1,  TRUE),
('Depression/Anxiety/COMPASS-31',              'DEPRESSION/ANXIETY/2026',    'COMPASS-31/2026',  2,  TRUE),
('Depression/Anxiety/DASS-21',                 'DEPRESSION/ANXIETY/2026',    'DASS-21/2026',     3,  TRUE),
('Depression/Anxiety/BDI-II',                  'DEPRESSION/ANXIETY/2026',    'BDI-II/2026',      4,  TRUE),
('Depression/Anxiety/GAD-7',                   'DEPRESSION/ANXIETY/2026',    'GAD-7/2026',       5,  TRUE),
('Depression/Anxiety/MADRS',                   'DEPRESSION/ANXIETY/2026',    'MADRS/2026',       6,  TRUE),
('Depression/Anxiety/PSQI',                    'DEPRESSION/ANXIETY/2026',    'PSQI/2026',        7,  TRUE),
('Chronic Pain/EQ-5D-5L',                      'CHRONICPAIN/2026',           'EQ-5D-5L/2026',   1,  TRUE),
('Chronic Pain/COMPASS-31',                    'CHRONICPAIN/2026',           'COMPASS-31/2026',  2,  TRUE),
('Chronic Pain/DASS-21',                       'CHRONICPAIN/2026',           'DASS-21/2026',     3,  TRUE),
('Chronic Pain/DN-4',                          'CHRONICPAIN/2026',           'DN-4/2026',        4,  TRUE),
('Chronic Pain/PainDETECT',                    'CHRONICPAIN/2026',           'PainDETECT/2026',  5,  TRUE),
('Chronic Pain/PRS',                           'CHRONICPAIN/2026',           'PRS/2026',         6,  TRUE),
('Chronic Pain/GAD-7',                         'CHRONICPAIN/2026',           'GAD-7/2026',       7,  TRUE),
('Chronic Pain/PSQI',                          'CHRONICPAIN/2026',           'PSQI/2026',        8,  TRUE),
('Fibromyalgia/EQ-5D-5L',                      'FIBROMYALGIA/2026',          'EQ-5D-5L/2026',   1,  TRUE),
('Fibromyalgia/COMPASS-31',                    'FIBROMYALGIA/2026',          'COMPASS-31/2026',  2,  TRUE),
('Fibromyalgia/PRS',                           'FIBROMYALGIA/2026',          'PRS/2026',         3,  TRUE),
('Fibromyalgia/PainDETECT',                    'FIBROMYALGIA/2026',          'PainDETECT/2026',  4,  TRUE),
('Fibromyalgia/FSS',                           'FIBROMYALGIA/2026',          'FSS/2026',         5,  TRUE),
('Fibromyalgia/VAS',                           'FIBROMYALGIA/2026',          'VAS/2026',         6,  TRUE),
('Fibromyalgia/FIQR',                          'FIBROMYALGIA/2026',          'FIQR/2026',        7,  TRUE),
('Migraine/EQ-5D-5L',                          'MIGRAINE/2026',              'EQ-5D-5L/2026',   1,  TRUE),
('Migraine/COMPASS-31',                        'MIGRAINE/2026',              'COMPASS-31/2026',  2,  TRUE),
('Migraine/MIDAS',                             'MIGRAINE/2026',              'MIDAS/2026',       3,  TRUE),
('Migraine/MSQ',                               'MIGRAINE/2026',              'MSQ/2026',         4,  TRUE),
('Migraine/PRS',                               'MIGRAINE/2026',              'PRS/2026',         5,  TRUE),
('Migraine/DASS-21',                           'MIGRAINE/2026',              'DASS-21/2026',     6,  TRUE),
('Migraine/PSQI',                              'MIGRAINE/2026',              'PSQI/2026',        7,  TRUE),
('Migraine/BDI-II',                            'MIGRAINE/2026',              'BDI-II/2026',      8,  TRUE),
('Ataxia/EQ-5D-5L',                            'ATAXIA/2026',                'EQ-5D-5L/2026',   1,  TRUE),
('Ataxia/COMPASS-31',                          'ATAXIA/2026',                'COMPASS-31/2026',  2,  TRUE),
('Ataxia/DHI',                                 'ATAXIA/2026',                'DHI/2026',         3,  TRUE),
('Ataxia/SARA',                                'ATAXIA/2026',                'SARA/2026',        4,  TRUE),
('Ataxia/DASS-21',                             'ATAXIA/2026',                'DASS-21/2026',     5,  TRUE),
('Ataxia/VVAS',                                'ATAXIA/2026',                'VVAS/2026',        6,  TRUE),
('Ataxia/BDI-II',                              'ATAXIA/2026',                'BDI-II/2026',      7,  TRUE),
('After Stroke/TBI/COMPASS-31',                'AFTERSTROKE/TBI/2026',       'COMPASS-31/2026',  1,  TRUE),
('After Stroke/TBI/KPS',                       'AFTERSTROKE/TBI/2026',       'KPS/2026',         2,  TRUE),
('After Stroke/TBI/SS-QOL',                    'AFTERSTROKE/TBI/2026',       'SS-QOL/2026',      3,  TRUE),
('After Stroke/TBI/MAS',                       'AFTERSTROKE/TBI/2026',       'MAS/2026',         4,  TRUE),
('After Stroke/TBI/MRC',                       'AFTERSTROKE/TBI/2026',       'MRC/2026',         5,  TRUE),
('After Stroke/TBI/DASS-21',                   'AFTERSTROKE/TBI/2026',       'DASS-21/2026',     6,  TRUE),
('After Stroke/TBI/MoCA',                      'AFTERSTROKE/TBI/2026',       'MoCA/2026',        7,  TRUE),
('After Stroke/TBI/BARTHEL',                   'AFTERSTROKE/TBI/2026',       'BARTHEL/2026',     8,  TRUE),
('After Stroke/TBI/PainDETECT',                'AFTERSTROKE/TBI/2026',       'PainDETECT/2026',  9,  TRUE),
('Dementia/EQ-5D-5L',                          'DEMENTIA/2026',              'EQ-5D-5L/2026',   1,  TRUE),
('Dementia/COMPASS-31',                        'DEMENTIA/2026',              'COMPASS-31/2026',  2,  TRUE),
('Dementia/AMTS',                              'DEMENTIA/2026',              'AMTS/2026',        3,  TRUE),
('Dementia/MoCA',                              'DEMENTIA/2026',              'MoCA/2026',        4,  TRUE),
('Dementia/DSRS',                              'DEMENTIA/2026',              'DSRS/2026',        5,  TRUE),
('Dementia/GDS',                               'DEMENTIA/2026',              'GDS/2026',         6,  TRUE),
('Dementia/IADL',                              'DEMENTIA/2026',              'IADL/2026',        7,  TRUE),
('Dementia/DASS-21',                           'DEMENTIA/2026',              'DASS-21/2026',     8,  TRUE),
('Parkinson''s Disease/COMPASS-31',            'PARKINSONSDISEASE/2026',     'COMPASS-31/2026',  1,  TRUE),
('Parkinson''s Disease/PDSS',                  'PARKINSONSDISEASE/2026',     'PDSS/2026',        2,  TRUE),
('Parkinson''s Disease/PFS-16',                'PARKINSONSDISEASE/2026',     'PFS-16/2026',      3,  TRUE),
('Parkinson''s Disease/MoCA',                  'PARKINSONSDISEASE/2026',     'MoCA/2026',        4,  TRUE),
('Parkinson''s Disease/PainDETECT',            'PARKINSONSDISEASE/2026',     'PainDETECT/2026',  5,  TRUE),
('Tinnitus/EQ-5D-5L',                          'TINNITUS/2026',              'EQ-5D-5L/2026',   1,  TRUE),
('Tinnitus/COMPASS-31',                        'TINNITUS/2026',              'COMPASS-31/2026',  2,  TRUE),
('Tinnitus/THI',                               'TINNITUS/2026',              'THI/2026',         3,  TRUE),
('Tinnitus/DASS-21',                           'TINNITUS/2026',              'DASS-21/2026',     4,  TRUE),
('Tinnitus/GAD-7',                             'TINNITUS/2026',              'GAD-7/2026',       5,  TRUE),
('Tinnitus/PSQI',                              'TINNITUS/2026',              'PSQI/2026',        6,  TRUE),
('Insomnia/EQ-5D-5L',                          'INSOMNIA/2026',              'EQ-5D-5L/2026',   1,  TRUE),
('Insomnia/COMPASS-31',                        'INSOMNIA/2026',              'COMPASS-31/2026',  2,  TRUE),
('Insomnia/DASS-21',                           'INSOMNIA/2026',              'DASS-21/2026',     3,  TRUE),
('Insomnia/GAD-7',                             'INSOMNIA/2026',              'GAD-7/2026',       4,  TRUE),
('Insomnia/PSQI',                              'INSOMNIA/2026',              'PSQI/2026',        5,  TRUE),
('Insomnia/AIS',                               'INSOMNIA/2026',              'AIS/2026',         6,  TRUE),
('Insomnia/FFS',                               'INSOMNIA/2026',              'FFS/2026',         7,  TRUE),
('Insomnia/ISI',                               'INSOMNIA/2026',              'ISI/2026',         8,  TRUE),
('Insomnia/SLEEP-50',                          'INSOMNIA/2026',              'SLEEP-50/2026',    9,  TRUE),
('Multiple Sclerosis/EQ-5D-5L',               'MULTIPLESCLEROSIS/2026',     'EQ-5D-5L/2026',   1,  TRUE),
('Multiple Sclerosis/COMPASS-31',             'MULTIPLESCLEROSIS/2026',     'COMPASS-31/2026',  2,  TRUE),
('Multiple Sclerosis/DHI',                    'MULTIPLESCLEROSIS/2026',     'DHI/2026',         3,  TRUE),
('Multiple Sclerosis/SARA',                   'MULTIPLESCLEROSIS/2026',     'SARA/2026',        4,  TRUE),
('Multiple Sclerosis/MFIS',                   'MULTIPLESCLEROSIS/2026',     'MFIS/2026',        5,  TRUE),
('Multiple Sclerosis/MoCA',                   'MULTIPLESCLEROSIS/2026',     'MoCA/2026',        6,  TRUE),
('Multiple Sclerosis/BARTHEL',                'MULTIPLESCLEROSIS/2026',     'BARTHEL/2026',     7,  TRUE),
('ADHD/EQ-5D-5L',                             'ADHD/2026',                  'EQ-5D-5L/2026',   1,  TRUE),
('ADHD/COMPASS-31',                           'ADHD/2026',                  'COMPASS-31/2026',  2,  TRUE),
('ADHD/ASRS-v1.1',                            'ADHD/2026',                  'ASRS-v1.1/2026',   3,  TRUE),
('ADHD/DASS-21',                              'ADHD/2026',                  'DASS-21/2026',     4,  TRUE),
('ADHD/SNAP-IV',                              'ADHD/2026',                  'SNAP-IV/2026',     5,  TRUE),
('ALS/EQ-5D-5L',                              'ALS/2026',                   'EQ-5D-5L/2026',   1,  TRUE),
('ALS/COMPASS-31',                            'ALS/2026',                   'COMPASS-31/2026',  2,  TRUE),
('ALS/DASS-21',                               'ALS/2026',                   'DASS-21/2026',     3,  TRUE),
('ALS/BDI-II',                                'ALS/2026',                   'BDI-II/2026',      4,  TRUE),
('ALS/MAS',                                   'ALS/2026',                   'MAS/2026',         5,  TRUE),
('ALS/GAD-7',                                 'ALS/2026',                   'GAD-7/2026',       6,  TRUE),
('ALS/ALSFRS-R',                              'ALS/2026',                   'ALSFRS-R/2026',    7,  TRUE),
('Irritable Bowel Disease/EQ-5D-5L',          'IRRITABLEBOWELDISEASE/2026', 'EQ-5D-5L/2026',   1,  TRUE),
('Irritable Bowel Disease/COMPASS-31',        'IRRITABLEBOWELDISEASE/2026', 'COMPASS-31/2026',  2,  TRUE),
('Irritable Bowel Disease/IBS-SSS',           'IRRITABLEBOWELDISEASE/2026', 'IBS-SSS/2026',     3,  TRUE),
('Irritable Bowel Disease/PRS',               'IRRITABLEBOWELDISEASE/2026', 'PRS/2026',         4,  TRUE),
('Irritable Bowel Disease/DASS-21',           'IRRITABLEBOWELDISEASE/2026', 'DASS-21/2026',     5,  TRUE),
('Irritable Bowel Disease/BDI-II',            'IRRITABLEBOWELDISEASE/2026', 'BDI-II/2026',      6,  TRUE),
('Irritable Bowel Disease/HDRS',              'IRRITABLEBOWELDISEASE/2026', 'HDRS/2026',        7,  TRUE)
ON CONFLICT (ds_map_id) DO NOTHING;

-- NOTE: prs_questions, prs_options, prs_scale_question_map,
-- prs_disease_question_map seed data comes from PRS_DET.xlsx.
-- Run: python backend/scripts/seed_scales.py


-- ============================================================
-- ANAMNESIS QUESTIONS (21 questions, 8 sections)
-- Exact from v6 MasterDB.sql — do not modify question_ids.
-- ============================================================

INSERT INTO anamnesis_questions
    (question_id, section_number, section_title, question_code, question_text,
     answer_type, is_required, display_order, depends_on_question_id, depends_on_value, helper_text)
VALUES
('ANA/S01/Q001', 1, 'Chief Complaint & Diagnosis', 'chief_complaint',
 'Why are you here today? / Primary Diagnosis',
 'textarea', TRUE, 1, NULL, NULL, 'Describe the main reason for this visit and any existing diagnosis'),

('ANA/S02/Q001', 2, 'Main Symptoms', 'main_symptoms',
 'What are your main symptoms?',
 'textarea', TRUE, 2, NULL, NULL, 'Describe the primary symptoms you are experiencing'),

('ANA/S02/Q002', 2, 'Main Symptoms', 'initial_symptoms',
 'What were the initial symptoms?',
 'textarea', TRUE, 3, NULL, NULL, 'Describe how your symptoms first appeared'),

('ANA/S02/Q003', 2, 'Main Symptoms', 'diagnosis_related',
 'Is there a diagnosis related to the symptoms?',
 'radio', TRUE, 4, NULL, NULL, NULL),

('ANA/S02/Q004', 2, 'Main Symptoms', 'diagnosis_details',
 'If yes, please specify the diagnosis',
 'conditional_text', FALSE, 5, 'ANA/S02/Q003', 'yes', 'Please specify the confirmed or suspected diagnosis'),

('ANA/S02/Q005', 2, 'Main Symptoms', 'symptoms_start',
 'When did the symptoms start?',
 'text', TRUE, 6, NULL, NULL, 'e.g. 3 months ago, January 2024'),

('ANA/S02/Q006', 2, 'Main Symptoms', 'symptoms_duration',
 'For how long have you had these symptoms?',
 'text', TRUE, 7, NULL, NULL, 'e.g. 2 weeks, 6 months, 2 years'),

('ANA/S02/Q007', 2, 'Main Symptoms', 'symptoms_frequency',
 'How often do you have these symptoms?',
 'select', TRUE, 8, NULL, NULL, NULL),

('ANA/S02/Q008', 2, 'Main Symptoms', 'symptoms_intensity',
 'How intense or severe are these symptoms?',
 'select', TRUE, 9, NULL, NULL, NULL),

('ANA/S02/Q009', 2, 'Main Symptoms', 'symptoms_progression',
 'Are the symptoms getting better, worse, or staying about the same?',
 'select', TRUE, 10, NULL, NULL, NULL),

('ANA/S03/Q001', 3, 'Secondary Symptoms', 'secondary_symptoms',
 'What are your secondary symptoms? (select all that apply)',
 'checkbox', FALSE, 11, NULL, NULL, 'Check all that apply'),

('ANA/S03/Q002', 3, 'Secondary Symptoms', 'secondary_symptoms_details',
 'Additional details about secondary symptoms',
 'textarea', FALSE, 12, NULL, NULL, 'Please provide more details about the checked symptoms'),

('ANA/S04/Q001', 4, 'Operations / Surgeries', 'has_operations',
 'Have you had any operations or surgeries?',
 'radio', TRUE, 13, NULL, NULL, NULL),

('ANA/S04/Q002', 4, 'Operations / Surgeries', 'operations_details',
 'If yes, please provide details',
 'conditional_text', FALSE, 14, 'ANA/S04/Q001', 'yes',
 'Include: which operations, how many, when performed, post-surgery condition / effects'),

('ANA/S05/Q001', 5, 'Previous or Ongoing Treatments', 'previous_treatments',
 'Previous or ongoing treatments (physiotherapy, speech therapy, psychotherapy, etc.)',
 'textarea', FALSE, 15, NULL, NULL,
 'Include: type of treatment, how long, how often, outcomes / improvements'),

('ANA/S06/Q001', 6, 'Medications & Supplements', 'current_medications',
 'Current medications and supplements',
 'textarea', FALSE, 16, NULL, NULL, 'List all current medications and supplements with dosages'),

('ANA/S07/Q001', 7, 'Brain MRI & Other Scans', 'has_brain_mri',
 'Have you had a Brain MRI?',
 'radio', TRUE, 17, NULL, NULL, NULL),

('ANA/S07/Q002', 7, 'Brain MRI & Other Scans', 'mri_details',
 'If yes, when was it performed and what were the results?',
 'conditional_text', FALSE, 18, 'ANA/S07/Q001', 'yes',
 'Include: date of MRI, results, any other relevant findings'),

('ANA/S07/Q003', 7, 'Brain MRI & Other Scans', 'other_scans',
 'Other scans (CT, EEG, EMG, etc.)',
 'textarea', FALSE, 19, NULL, NULL, 'List any other diagnostic scans or tests performed'),

('ANA/S08/Q001', 8, 'Neuromodulation Experience', 'has_neuromodulation',
 'Have you used any neuromodulation techniques before?',
 'radio', TRUE, 20, NULL, NULL, NULL),

('ANA/S08/Q002', 8, 'Neuromodulation Experience', 'neuromodulation_details',
 'If yes, please specify devices used and experience',
 'conditional_text', FALSE, 21, 'ANA/S08/Q001', 'yes',
 'Include: type of device, duration of use, effectiveness, any side effects')

ON CONFLICT (question_id) DO UPDATE SET
    question_text  = EXCLUDED.question_text,
    helper_text    = EXCLUDED.helper_text,
    section_title  = EXCLUDED.section_title,
    display_order  = EXCLUDED.display_order;


-- ============================================================
-- ANAMNESIS OPTIONS (31 options for radio/select/checkbox questions)
-- ============================================================

INSERT INTO anamnesis_options (option_id, question_id, option_label, option_value, display_order)
VALUES
('ANA/S02/Q003/O01', 'ANA/S02/Q003', 'Yes', 'yes', 1),
('ANA/S02/Q003/O02', 'ANA/S02/Q003', 'No',  'no',  2),

('ANA/S02/Q007/O01', 'ANA/S02/Q007', 'Daily',                'daily',              1),
('ANA/S02/Q007/O02', 'ANA/S02/Q007', 'Several times a week', 'several-times-week', 2),
('ANA/S02/Q007/O03', 'ANA/S02/Q007', 'Weekly',               'weekly',             3),
('ANA/S02/Q007/O04', 'ANA/S02/Q007', 'Monthly',              'monthly',            4),
('ANA/S02/Q007/O05', 'ANA/S02/Q007', 'Occasionally',         'occasionally',       5),

('ANA/S02/Q008/O01', 'ANA/S02/Q008', 'Mild',        'mild',       1),
('ANA/S02/Q008/O02', 'ANA/S02/Q008', 'Moderate',    'moderate',   2),
('ANA/S02/Q008/O03', 'ANA/S02/Q008', 'Severe',      'severe',     3),
('ANA/S02/Q008/O04', 'ANA/S02/Q008', 'Very Severe', 'very-severe',4),

('ANA/S02/Q009/O01', 'ANA/S02/Q009', 'Getting better',         'better',      1),
('ANA/S02/Q009/O02', 'ANA/S02/Q009', 'Getting worse',          'worse',       2),
('ANA/S02/Q009/O03', 'ANA/S02/Q009', 'Staying about the same', 'same',        3),
('ANA/S02/Q009/O04', 'ANA/S02/Q009', 'Fluctuating',            'fluctuating', 4),

('ANA/S03/Q001/O01', 'ANA/S03/Q001', 'Sleep Issues',           'sleep',            1),
('ANA/S03/Q001/O02', 'ANA/S03/Q001', 'Concentration Problems', 'concentration',    2),
('ANA/S03/Q001/O03', 'ANA/S03/Q001', 'Memory Issues',          'memory',           3),
('ANA/S03/Q001/O04', 'ANA/S03/Q001', 'Gastrointestinal Issues','gastrointestinal', 4),
('ANA/S03/Q001/O05', 'ANA/S03/Q001', 'Mood Fluctuations',      'mood',             5),
('ANA/S03/Q001/O06', 'ANA/S03/Q001', 'Fatigue',                'fatigue',          6),
('ANA/S03/Q001/O07', 'ANA/S03/Q001', 'Weakness',               'weakness',         7),
('ANA/S03/Q001/O08', 'ANA/S03/Q001', 'Pain',                   'pain',             8),
('ANA/S03/Q001/O09', 'ANA/S03/Q001', 'Depression/Anxiety',     'depression',       9),
('ANA/S03/Q001/O10', 'ANA/S03/Q001', 'Bladder Function Issues','bladder',          10),

('ANA/S04/Q001/O01', 'ANA/S04/Q001', 'Yes', 'yes', 1),
('ANA/S04/Q001/O02', 'ANA/S04/Q001', 'No',  'no',  2),

('ANA/S07/Q001/O01', 'ANA/S07/Q001', 'Yes', 'yes', 1),
('ANA/S07/Q001/O02', 'ANA/S07/Q001', 'No',  'no',  2),

('ANA/S08/Q001/O01', 'ANA/S08/Q001', 'Yes', 'yes', 1),
('ANA/S08/Q001/O02', 'ANA/S08/Q001', 'No',  'no',  2)

ON CONFLICT (option_id) DO NOTHING;

COMMIT;
