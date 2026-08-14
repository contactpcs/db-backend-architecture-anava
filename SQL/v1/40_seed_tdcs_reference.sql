-- 40_seed_tdcs_reference.sql
--
-- SEED DATA, not schema. Populates the clinical reference catalogue for the
-- tDCS device family from the tDCS Condition-Montage-Dosing mapping sheet.
--
-- APPLY ORDER: after 39. Requires reference.neuromod_devices to already hold
-- the three tDCS devices (BIO-001, MRB-002, SMA-003).
--
-- ###########################################################################
-- READ THIS BEFORE RUNNING
-- ###########################################################################
--
-- 1. RUN AS TABLE OWNER, NOT anava_app.
--    32 line 1664 REVOKEs INSERT/UPDATE/DELETE on every reference.* catalogue
--    table from anava_app. That is deliberate: an application bug must not be
--    able to alter a prescribed dose. This file therefore cannot run through
--    the application connection. In DBeaver, confirm with:
--        SELECT current_user;
--    If it returns anava_app, switch connections before running.
--
-- 2. THIS IS CLINICAL DATA. Every montage and dose below is transcribed from
--    the mapping sheet. Verify against the source before running on anything
--    a patient's prescription will be built from. A wrong montage arriving
--    silently is a clinical safety problem, not a data-entry problem.
--
-- 3. IDEMPOTENT. Every INSERT is guarded by a NOT EXISTS or ON CONFLICT, so
--    re-running adds nothing. Safe to run twice.
--
-- 4. SEEDS FOR TWO DEVICES ONLY: BIO-001 (Biothm Vagus One) and MRB-002
--    (Marbles HD System). SMA-003 (Sooma) is is_active=false in the registry
--    and the create path rejects inactive devices, so seeding a catalogue for
--    it would produce rows nothing can reach. Activate Sooma first if it is
--    meant to be prescribable, then re-run — the loop picks it up.
--
--
-- ###########################################################################
-- WHY EVERY PLACEMENT AND DOSE IS DUPLICATED PER DEVICE
-- ###########################################################################
--
-- reference.tdcs_placements and reference.tdcs_dosing both carry
-- device_id NOT NULL. A catalogue row belongs to one device, not to the tDCS
-- family as a whole. That is correct — two tDCS machines from different
-- manufacturers do not necessarily support the same electrode positions or
-- the same current range, and the same-device trigger on treatment_protocols
-- enforces that a protocol's placement and dosing both belong to its device.
--
-- The consequence is that seeding N devices writes N copies of each montage.
-- The DO block below loops over the active tDCS devices rather than repeating
-- the literal rows, so adding a fourth device is a one-line change.
--
--
-- ###########################################################################
-- "NOT SPECIFIED" IS A VALUE, NOT A GAP
-- ###########################################################################
--
-- The mapping sheet gives real numbers for Depression and partial numbers for
-- Anxiety. For Chronic Pain, ADHD and ASD it says "Not specified" or
-- "Varied / Not specified". Those rows are seeded with NULL columns AND a
-- matching row in reference.dosing_unspecified_notes (39), so the wizard can
-- tell "the evidence base does not specify this" apart from "nobody has
-- entered it yet". The first is a documented absence the doctor resolves from
-- judgement; the second is an import bug. They must not look identical.
--
-- tdcs_placements.chk_tdcs_placements_electrode_rule requires anode and
-- cathode to be both set or both NULL — which is exactly how an unspecified
-- montage is represented. No invented sites.


BEGIN;


-- ###########################################################################
-- 0  Guard: refuse to run as the application role
-- ###########################################################################
-- Running as anava_app would fail at the first INSERT with a permission error
-- partway through. Failing here instead names the actual problem.

DO $guard$
BEGIN
    IF current_user = 'anava_app' THEN
        RAISE EXCEPTION
            'This seed must run as the catalogue owner, not anava_app. 32 revokes INSERT on reference.* from anava_app by design.';
    END IF;
END
$guard$;


-- ###########################################################################
-- 1  Conditions  (wizard Step 2)
-- ###########################################################################
-- display_order drives the order of the cards in the picker. Depression first
-- because it is the only condition with a complete, evidence-A dosing row.

INSERT INTO reference."neuromod_conditions" ("condition_name", "display_order", "is_active")
SELECT v.name, v.ord, TRUE
FROM (VALUES
    ('Depression',        10),
    ('Anxiety Disorders', 20),
    ('Chronic Pain',      30),
    ('ADHD',              40),
    ('Autism Spectrum Disorder', 50)
) AS v(name, ord)
WHERE NOT EXISTS (
    SELECT 1 FROM reference."neuromod_conditions" c WHERE c.condition_name = v.name
);


-- ###########################################################################
-- 2  ICD-10 diagnosis codes  (wizard Step 3)
-- ###########################################################################
-- Codes are ICD-10-CM. Each maps to exactly one condition; the wizard ranks a
-- multi-code selection by the evidence level of the codes' conditions.

INSERT INTO reference."neuromod_diagnoses" ("condition_id", "icd10_code", "icd10_description")
SELECT c.condition_id, v.code, v.descr
FROM (VALUES
    ('Depression',        'F32.0', 'Major depressive disorder, single episode, mild'),
    ('Depression',        'F32.1', 'Major depressive disorder, single episode, moderate'),
    ('Depression',        'F32.2', 'Major depressive disorder, single episode, severe without psychotic features'),
    ('Depression',        'F32.9', 'Major depressive disorder, single episode, unspecified'),
    ('Depression',        'F33.0', 'Major depressive disorder, recurrent, mild'),
    ('Depression',        'F33.1', 'Major depressive disorder, recurrent, moderate'),
    ('Depression',        'F33.2', 'Major depressive disorder, recurrent severe without psychotic features'),
    ('Depression',        'F33.9', 'Major depressive disorder, recurrent, unspecified'),
    ('Depression',        'F34.1', 'Dysthymic disorder'),

    ('Anxiety Disorders', 'F41.0', 'Panic disorder without agoraphobia'),
    ('Anxiety Disorders', 'F41.1', 'Generalized anxiety disorder'),
    ('Anxiety Disorders', 'F41.9', 'Anxiety disorder, unspecified'),
    ('Anxiety Disorders', 'F40.10', 'Social phobia, unspecified'),
    ('Anxiety Disorders', 'F42.9', 'Obsessive-compulsive disorder, unspecified'),

    ('Chronic Pain',      'G89.29', 'Other chronic pain'),
    ('Chronic Pain',      'G89.4',  'Chronic pain syndrome'),
    ('Chronic Pain',      'M79.7',  'Fibromyalgia'),
    ('Chronic Pain',      'M54.5',  'Low back pain'),
    ('Chronic Pain',      'G43.909','Migraine, unspecified, not intractable, without status migrainosus'),

    ('ADHD',              'F90.0',  'Attention-deficit hyperactivity disorder, predominantly inattentive type'),
    ('ADHD',              'F90.1',  'Attention-deficit hyperactivity disorder, predominantly hyperactive type'),
    ('ADHD',              'F90.2',  'Attention-deficit hyperactivity disorder, combined type'),
    ('ADHD',              'F90.9',  'Attention-deficit hyperactivity disorder, unspecified type'),

    ('Autism Spectrum Disorder', 'F84.0', 'Autistic disorder'),
    ('Autism Spectrum Disorder', 'F84.5', 'Asperger''s syndrome'),
    ('Autism Spectrum Disorder', 'F84.9', 'Pervasive developmental disorder, unspecified')
) AS v(cond, code, descr)
JOIN reference."neuromod_conditions" c ON c.condition_name = v.cond
WHERE NOT EXISTS (
    SELECT 1 FROM reference."neuromod_diagnoses" d
    WHERE d.condition_id = c.condition_id AND d.icd10_code = v.code
);


-- ###########################################################################
-- 3  Assessment scales  (wizard Step 6)
-- ###########################################################################
-- prs_scale_id is the bridge into the PRS questionnaire engine and is left
-- NULL here: it must match a scale that engine actually has built, and
-- guessing an identifier would produce a protocol that queues a questionnaire
-- which does not exist. Fill it in per scale as the PRS side lands:
--     UPDATE reference.neuromod_scales SET prs_scale_id = '<prs id>'
--      WHERE scale_code = 'PHQ-9';

INSERT INTO reference."neuromod_scales" ("scale_code", "scale_name", "prs_scale_id")
SELECT v.code, v.name, NULL
FROM (VALUES
    ('PHQ-9',   'Patient Health Questionnaire-9'),
    ('BDI-II',  'Beck Depression Inventory-II'),
    ('MADRS',   'Montgomery-Asberg Depression Rating Scale'),
    ('GAD-7',   'Generalized Anxiety Disorder-7'),
    ('HAM-A',   'Hamilton Anxiety Rating Scale'),
    ('Y-BOCS',  'Yale-Brown Obsessive Compulsive Scale'),
    ('VAS',     'Visual Analogue Scale (Pain)'),
    ('BPI',     'Brief Pain Inventory'),
    ('PCS',     'Pain Catastrophizing Scale'),
    ('ASRS',    'Adult ADHD Self-Report Scale'),
    ('CAARS',   'Conners Adult ADHD Rating Scales'),
    ('CARS-2',  'Childhood Autism Rating Scale, Second Edition'),
    ('SRS-2',   'Social Responsiveness Scale, Second Edition')
) AS v(code, name)
WHERE NOT EXISTS (
    SELECT 1 FROM reference."neuromod_scales" s WHERE s.scale_code = v.code
);


-- Which scales the wizard suggests for which condition. display_order puts the
-- primary instrument first — that is the one the UI pre-ticks.
INSERT INTO reference."neuromod_condition_scales" ("condition_id", "scale_id", "display_order")
SELECT c.condition_id, s.scale_id, v.ord
FROM (VALUES
    ('Depression',        'PHQ-9',  10),
    ('Depression',        'BDI-II', 20),
    ('Depression',        'MADRS',  30),

    ('Anxiety Disorders', 'GAD-7',  10),
    ('Anxiety Disorders', 'HAM-A',  20),
    ('Anxiety Disorders', 'Y-BOCS', 30),

    ('Chronic Pain',      'VAS',    10),
    ('Chronic Pain',      'BPI',    20),
    ('Chronic Pain',      'PCS',    30),

    ('ADHD',              'ASRS',   10),
    ('ADHD',              'CAARS',  20),

    ('Autism Spectrum Disorder', 'CARS-2', 10),
    ('Autism Spectrum Disorder', 'SRS-2',  20)
) AS v(cond, code, ord)
JOIN reference."neuromod_conditions" c ON c.condition_name = v.cond
JOIN reference."neuromod_scales" s ON s.scale_code = v.code
WHERE NOT EXISTS (
    SELECT 1 FROM reference."neuromod_condition_scales" m
    WHERE m.condition_id = c.condition_id AND m.scale_id = s.scale_id
);


-- ###########################################################################
-- 4  Placements and dosing, per active tDCS device
-- ###########################################################################
-- The loop exists because both tables key on device_id. Adding a device to
-- the catalogue and re-running this file gives it the same montage library
-- without editing any row literal.
--
-- Montage sites are 10-20 system electrode positions, transcribed from the
-- mapping sheet:
--
--   Depression         F3 anode / F4 cathode   1-2 mA  30 min  Evidence A
--                      "1 per day x 10 days (20-30 days attempted)"
--   Anxiety Disorders  F4 anode / F3 cathode   1-2 mA  Not specified  Evidence B
--   Chronic Pain       Not specified throughout                Evidence C
--   ADHD               Varied / Not specified                  Evidence C
--   ASD                Varied / Not specified                  Evidence C
--
-- num_sessions_text is TEXT, not an integer, precisely because
-- "1 per day x 10 days (20-30 days attempted)" is a protocol phrase. 32 chose
-- that deliberately; this seed does not flatten it into a number.

DO $seed$
DECLARE
    dev        RECORD;
    v_cond_id  UUID;
    v_place_id UUID;
    r          RECORD;
BEGIN
    FOR dev IN
        SELECT device_id, device_code, device_name
        FROM reference."neuromod_devices"
        WHERE modality = 'tDCS' AND is_active = TRUE
        ORDER BY device_code
    LOOP
        FOR r IN
            SELECT * FROM (VALUES
                -- cond,   montage_label,        anode, cathode, evidence,
                --   ma_min, ma_max, dur_min, per_day, sessions_text
                ('Depression', 'Left DLPFC anodal (F3 -> F4)', 'F3', 'F4', 'A',
                 1.0::numeric, 2.0::numeric, 30, 1,
                 '1 per day x 10 days (20-30 days attempted)'),

                ('Anxiety Disorders', 'Right DLPFC anodal (F4 -> F3)', 'F4', 'F3', 'B',
                 1.0::numeric, 2.0::numeric, NULL::integer, 1,
                 NULL::text),

                ('Chronic Pain', 'Not specified', NULL::text, NULL::text, 'C',
                 NULL::numeric, NULL::numeric, NULL::integer, NULL::integer,
                 NULL::text),

                ('ADHD', 'Varied / Not specified', NULL::text, NULL::text, 'C',
                 NULL::numeric, NULL::numeric, NULL::integer, NULL::integer,
                 NULL::text),

                ('Autism Spectrum Disorder', 'Varied / Not specified', NULL::text, NULL::text, 'C',
                 NULL::numeric, NULL::numeric, NULL::integer, NULL::integer,
                 NULL::text)
            ) AS t(cond, montage_label, anode, cathode, evidence,
                   ma_min, ma_max, dur_min, per_day, sessions_text)
        LOOP
            SELECT condition_id INTO v_cond_id
            FROM reference."neuromod_conditions"
            WHERE condition_name = r.cond;

            IF v_cond_id IS NULL THEN
                RAISE EXCEPTION 'Condition % missing — section 1 did not run', r.cond;
            END IF;

            -- Placement. Matched on (condition, device, label) so a re-run
            -- finds the existing row instead of creating a duplicate montage.
            SELECT tdcs_placement_id INTO v_place_id
            FROM reference."tdcs_placements"
            WHERE condition_id = v_cond_id
              AND device_id = dev.device_id
              AND montage_label = r.montage_label;

            IF v_place_id IS NULL THEN
                INSERT INTO reference."tdcs_placements"
                    (condition_id, device_id, montage_label, anode_site, cathode_site, is_active)
                VALUES
                    (v_cond_id, dev.device_id, r.montage_label, r.anode, r.cathode, TRUE)
                RETURNING tdcs_placement_id INTO v_place_id;
            END IF;

            -- Dosing, one row per placement.
            IF NOT EXISTS (
                SELECT 1 FROM reference."tdcs_dosing"
                WHERE condition_id = v_cond_id
                  AND device_id = dev.device_id
                  AND tdcs_placement_id = v_place_id
            ) THEN
                INSERT INTO reference."tdcs_dosing"
                    (condition_id, device_id, tdcs_placement_id, evidence_level,
                     current_ma_min, current_ma_max, session_duration_min,
                     sessions_per_day, num_sessions_text, notes, is_active)
                VALUES
                    (v_cond_id, dev.device_id, v_place_id, r.evidence,
                     r.ma_min, r.ma_max, r.dur_min,
                     r.per_day, r.sessions_text,
                     'Seeded from tDCS condition-montage-dosing mapping sheet.', TRUE);
            END IF;
        END LOOP;

        RAISE NOTICE 'Seeded tDCS catalogue for % (%)', dev.device_name, dev.device_code;
    END LOOP;
END
$seed$;


-- ###########################################################################
-- 5  Explicit "Not specified" markers  (39)
-- ###########################################################################
-- The rows above carry NULLs. These say WHY they are NULL: the source states
-- no value, rather than the value being lost in transit. Without this, a
-- doctor looking at Chronic Pain cannot tell an unspecified protocol from a
-- broken import — and those call for opposite responses.

INSERT INTO reference."dosing_unspecified_notes"
    ("condition_id", "device_id", "field_name", "source_text")
SELECT c.condition_id, d.device_id, v.field, v.src
FROM (VALUES
    ('Anxiety Disorders', 'session_duration_min', 'Not specified'),
    ('Anxiety Disorders', 'num_sessions',         'Not specified'),

    ('Chronic Pain',      'placement',            'Not specified'),
    ('Chronic Pain',      'current_ma',           'Not specified'),
    ('Chronic Pain',      'session_duration_min', 'Not specified'),
    ('Chronic Pain',      'num_sessions',         'Not specified'),

    ('ADHD',              'placement',            'Varied / Not specified'),
    ('ADHD',              'current_ma',           'Not specified'),
    ('ADHD',              'session_duration_min', 'Not specified'),
    ('ADHD',              'num_sessions',         'Not specified'),

    ('Autism Spectrum Disorder', 'placement',            'Varied / Not specified'),
    ('Autism Spectrum Disorder', 'current_ma',           'Not specified'),
    ('Autism Spectrum Disorder', 'session_duration_min', 'Not specified'),
    ('Autism Spectrum Disorder', 'num_sessions',         'Not specified')
) AS v(cond, field, src)
JOIN reference."neuromod_conditions" c ON c.condition_name = v.cond
JOIN reference."neuromod_devices" d ON d.modality = 'tDCS' AND d.is_active = TRUE
ON CONFLICT ("condition_id", "device_id", "field_name") DO NOTHING;


COMMIT;


-- ###########################################################################
-- VERIFY  — run after COMMIT
-- ###########################################################################
--
-- Expected with two active tDCS devices (BIO-001, MRB-002):
--
--   conditions          5
--   diagnoses          26
--   scales             13
--   condition_scales   13
--   tdcs_placements    10   (5 conditions x 2 devices)
--   tdcs_dosing        10
--   unspecified_notes  28   (14 rows x 2 devices)
--
-- SELECT 'conditions' AS t, count(*) FROM reference.neuromod_conditions
-- UNION ALL SELECT 'diagnoses',         count(*) FROM reference.neuromod_diagnoses
-- UNION ALL SELECT 'scales',            count(*) FROM reference.neuromod_scales
-- UNION ALL SELECT 'condition_scales',  count(*) FROM reference.neuromod_condition_scales
-- UNION ALL SELECT 'tdcs_placements',   count(*) FROM reference.tdcs_placements
-- UNION ALL SELECT 'tdcs_dosing',       count(*) FROM reference.tdcs_dosing
-- UNION ALL SELECT 'unspecified_notes', count(*) FROM reference.dosing_unspecified_notes;
--
--
-- The row the wizard's happy path depends on — Depression on Biothm, the only
-- fully-specified protocol in the sheet:
--
-- SELECT dev.device_name, c.condition_name, p.montage_label,
--        p.anode_site, p.cathode_site, dg.evidence_level,
--        dg.current_ma_min, dg.current_ma_max, dg.session_duration_min,
--        dg.num_sessions_text
--   FROM reference.tdcs_dosing dg
--   JOIN reference.tdcs_placements p ON p.tdcs_placement_id = dg.tdcs_placement_id
--   JOIN reference.neuromod_conditions c ON c.condition_id = dg.condition_id
--   JOIN reference.neuromod_devices dev ON dev.device_id = dg.device_id
--  WHERE c.condition_name = 'Depression'
--  ORDER BY dev.device_code;
--
--
-- ###########################################################################
-- WHAT THIS FILE DELIBERATELY DOES NOT SEED
-- ###########################################################################
--
--   1. Sooma (SMA-003). is_active = false in the registry; ProtocolService
--      .create raises DEVICE_INACTIVE before a catalogue is ever consulted, so
--      its rows would be unreachable. Set is_active = true and re-run to add
--      them.
--
--   2. hd_tdcs_placements / hd_tdcs_dosing. No device in the registry has
--      modality = 'HD-tDCS' — "Marbles HD System" is registered as 'tDCS'.
--      If that machine is genuinely an HD 4x1 device, its modality is wrong
--      and the electrode validator will cap it at one cathode instead of four.
--      That is a data question, not a seed question: fix the device row first,
--      then seed the HD catalogue.
--
--   3. prs_scale_id on any scale. See section 3 — an invented identifier
--      queues a questionnaire that does not exist.
--
--   4. Anything for taVNS, TPS, rTMS or 'other'. No devices of those
--      modalities are registered.
-- ###########################################################################
