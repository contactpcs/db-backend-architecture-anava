-- 94_tvns_disease_presets_data.sql
--
-- Dummy tVNS placement + dosing preset rows for the 6 diseases the doctor
-- asked for (Parkinson's Motor, Parkinson's Cognitive, Depression, ADHD,
-- Anxiety, Insomnia - Stroke/Alzheimer's/Cognitive Enhancement excluded).
-- One placement per condition (dummy montage, doctor can still pick/edit
-- placement separately), two dosing rows per condition where the source
-- gave two modes ("Intermittent/Modulation" etc - tvns_dosing already
-- supports multiple rows per condition, see 90_tvns_device.sql). strength_pct
-- wasn't specified per-disease in the source list, so all new rows reuse the
-- same dummy 20-60% band already used for the original Depression rows.
--
-- Depends on 93_tvns_disease_presets.sql (frequency_hz_min/max,
-- pulse_width_us_min/max columns + the 3 new condition rows).

BEGIN;

DO $$
DECLARE
    v_device_id UUID := 'd97db782-e4fc-4884-89a5-d8e8f6f86655'; -- TVNS-001

    v_parkinsons_motor UUID;
    v_parkinsons_cog   UUID;
    v_depression       UUID;
    v_adhd             UUID;
    v_anxiety          UUID;
    v_insomnia         UUID;

    v_pl_parkinsons_motor UUID;
    v_pl_parkinsons_cog   UUID;
    v_pl_adhd             UUID;
    v_pl_anxiety          UUID;
    v_pl_insomnia         UUID;
    v_pl_depression       UUID;
BEGIN
    SELECT condition_id INTO v_parkinsons_motor FROM reference.neuromod_conditions WHERE condition_name = 'Parkinson''s Disease - Motor Symptoms';
    SELECT condition_id INTO v_parkinsons_cog   FROM reference.neuromod_conditions WHERE condition_name = 'Parkinson''s Disease - Cognitive/Memory Decline';
    SELECT condition_id INTO v_depression       FROM reference.neuromod_conditions WHERE condition_name = 'Depression';
    SELECT condition_id INTO v_adhd             FROM reference.neuromod_conditions WHERE condition_name = 'ADHD';
    SELECT condition_id INTO v_anxiety          FROM reference.neuromod_conditions WHERE condition_name = 'Anxiety Disorders';
    SELECT condition_id INTO v_insomnia         FROM reference.neuromod_conditions WHERE condition_name = 'Insomnia / Sleep Disorder';

    -- Depression already has a placement on prod (d86560b9-b5c3-4bf3-b3ac-
    -- 12e700a638ed) but a fresh DB (CI, new environment) has none - that
    -- placement was hand-inserted outside any tracked migration, same root
    -- cause as 93_5_tvns_device_seed.sql. Self-heal here too instead of
    -- assuming it exists.
    SELECT tvns_placement_id INTO v_pl_depression FROM reference.tvns_placements WHERE condition_id = v_depression LIMIT 1;
    IF v_pl_depression IS NULL THEN
        INSERT INTO reference.tvns_placements (condition_id, device_id, montage_label, ear_side, auricular_site)
        VALUES (v_depression, v_device_id, 'Standard — Left Cymba Conchae', 'left', 'cymba_conchae')
        ON CONFLICT (condition_id, device_id, montage_label) DO NOTHING
        RETURNING tvns_placement_id INTO v_pl_depression;
        IF v_pl_depression IS NULL THEN
            SELECT tvns_placement_id INTO v_pl_depression FROM reference.tvns_placements WHERE condition_id = v_depression AND device_id = v_device_id LIMIT 1;
        END IF;
    END IF;

    -- One dummy placement per new condition.
    INSERT INTO reference.tvns_placements (condition_id, device_id, montage_label, ear_side, auricular_site)
    VALUES (v_parkinsons_motor, v_device_id, 'Standard — Left Cymba Conchae', 'left', 'cymba_conchae')
    ON CONFLICT (condition_id, device_id, montage_label) DO NOTHING
    RETURNING tvns_placement_id INTO v_pl_parkinsons_motor;
    IF v_pl_parkinsons_motor IS NULL THEN
        SELECT tvns_placement_id INTO v_pl_parkinsons_motor FROM reference.tvns_placements WHERE condition_id = v_parkinsons_motor AND device_id = v_device_id LIMIT 1;
    END IF;

    INSERT INTO reference.tvns_placements (condition_id, device_id, montage_label, ear_side, auricular_site)
    VALUES (v_parkinsons_cog, v_device_id, 'Standard — Left Cymba Conchae', 'left', 'cymba_conchae')
    ON CONFLICT (condition_id, device_id, montage_label) DO NOTHING
    RETURNING tvns_placement_id INTO v_pl_parkinsons_cog;
    IF v_pl_parkinsons_cog IS NULL THEN
        SELECT tvns_placement_id INTO v_pl_parkinsons_cog FROM reference.tvns_placements WHERE condition_id = v_parkinsons_cog AND device_id = v_device_id LIMIT 1;
    END IF;

    INSERT INTO reference.tvns_placements (condition_id, device_id, montage_label, ear_side, auricular_site)
    VALUES (v_adhd, v_device_id, 'Standard — Left Cymba Conchae', 'left', 'cymba_conchae')
    ON CONFLICT (condition_id, device_id, montage_label) DO NOTHING
    RETURNING tvns_placement_id INTO v_pl_adhd;
    IF v_pl_adhd IS NULL THEN
        SELECT tvns_placement_id INTO v_pl_adhd FROM reference.tvns_placements WHERE condition_id = v_adhd AND device_id = v_device_id LIMIT 1;
    END IF;

    INSERT INTO reference.tvns_placements (condition_id, device_id, montage_label, ear_side, auricular_site)
    VALUES (v_anxiety, v_device_id, 'Standard — Left Cymba Conchae', 'left', 'cymba_conchae')
    ON CONFLICT (condition_id, device_id, montage_label) DO NOTHING
    RETURNING tvns_placement_id INTO v_pl_anxiety;
    IF v_pl_anxiety IS NULL THEN
        SELECT tvns_placement_id INTO v_pl_anxiety FROM reference.tvns_placements WHERE condition_id = v_anxiety AND device_id = v_device_id LIMIT 1;
    END IF;

    INSERT INTO reference.tvns_placements (condition_id, device_id, montage_label, ear_side, auricular_site)
    VALUES (v_insomnia, v_device_id, 'Standard — Left Cymba Conchae', 'left', 'cymba_conchae')
    ON CONFLICT (condition_id, device_id, montage_label) DO NOTHING
    RETURNING tvns_placement_id INTO v_pl_insomnia;
    IF v_pl_insomnia IS NULL THEN
        SELECT tvns_placement_id INTO v_pl_insomnia FROM reference.tvns_placements WHERE condition_id = v_insomnia AND device_id = v_device_id LIMIT 1;
    END IF;

    -- Parkinson's Motor: 100-200Hz, 200-300us, biphasic, Intermittent + Modulation, 20min
    INSERT INTO reference.tvns_dosing
        (condition_id, device_id, tvns_placement_id, evidence_level, wavelength, pattern,
         strength_pct_min, strength_pct_max, frequency_hz_min, frequency_hz_max,
         pulse_width_us_min, pulse_width_us_max, session_duration_min, notes)
    VALUES
        (v_parkinsons_motor, v_device_id, v_pl_parkinsons_motor, 'C', 'biphasic', 'intermittent', 20, 60, 100, 200, 200, 300, 20, 'Preset range - doctor to confirm/edit before prescribing.'),
        (v_parkinsons_motor, v_device_id, v_pl_parkinsons_motor, 'C', 'biphasic', 'modulation',   20, 60, 100, 200, 200, 300, 20, 'Preset range - doctor to confirm/edit before prescribing.');

    -- Parkinson's Cognitive: 10-50Hz, 100-200us, biphasic, Modulation + Intermittent, 20min
    INSERT INTO reference.tvns_dosing
        (condition_id, device_id, tvns_placement_id, evidence_level, wavelength, pattern,
         strength_pct_min, strength_pct_max, frequency_hz_min, frequency_hz_max,
         pulse_width_us_min, pulse_width_us_max, session_duration_min, notes)
    VALUES
        (v_parkinsons_cog, v_device_id, v_pl_parkinsons_cog, 'C', 'biphasic', 'modulation',   20, 60, 10, 50, 100, 200, 20, 'Preset range - doctor to confirm/edit before prescribing.'),
        (v_parkinsons_cog, v_device_id, v_pl_parkinsons_cog, 'C', 'biphasic', 'intermittent', 20, 60, 10, 50, 100, 200, 20, 'Preset range - doctor to confirm/edit before prescribing.');

    -- Depression (additional presets, existing 3 rows stay): 20-200Hz, 200-300us, biphasic, Modulation + Intermittent, 20min
    INSERT INTO reference.tvns_dosing
        (condition_id, device_id, tvns_placement_id, evidence_level, wavelength, pattern,
         strength_pct_min, strength_pct_max, frequency_hz_min, frequency_hz_max,
         pulse_width_us_min, pulse_width_us_max, session_duration_min, notes)
    VALUES
        (v_depression, v_device_id, v_pl_depression, 'C', 'biphasic', 'modulation',   20, 60, 20, 200, 200, 300, 20, 'Preset range - doctor to confirm/edit before prescribing.'),
        (v_depression, v_device_id, v_pl_depression, 'C', 'biphasic', 'intermittent', 20, 60, 20, 200, 200, 300, 20, 'Preset range - doctor to confirm/edit before prescribing.');

    -- ADHD: 10-50Hz, 100-200us, biphasic, Modulation + Intermittent, 15min
    INSERT INTO reference.tvns_dosing
        (condition_id, device_id, tvns_placement_id, evidence_level, wavelength, pattern,
         strength_pct_min, strength_pct_max, frequency_hz_min, frequency_hz_max,
         pulse_width_us_min, pulse_width_us_max, session_duration_min, notes)
    VALUES
        (v_adhd, v_device_id, v_pl_adhd, 'C', 'biphasic', 'modulation',   20, 60, 10, 50, 100, 200, 15, 'Preset range - doctor to confirm/edit before prescribing.'),
        (v_adhd, v_device_id, v_pl_adhd, 'C', 'biphasic', 'intermittent', 20, 60, 10, 50, 100, 200, 15, 'Preset range - doctor to confirm/edit before prescribing.');

    -- Anxiety / Stress: 1-10Hz, 100-200us, biphasic, Modulation + Intermittent, 20min
    INSERT INTO reference.tvns_dosing
        (condition_id, device_id, tvns_placement_id, evidence_level, wavelength, pattern,
         strength_pct_min, strength_pct_max, frequency_hz_min, frequency_hz_max,
         pulse_width_us_min, pulse_width_us_max, session_duration_min, notes)
    VALUES
        (v_anxiety, v_device_id, v_pl_anxiety, 'C', 'biphasic', 'modulation',   20, 60, 1, 10, 100, 200, 20, 'Preset range - doctor to confirm/edit before prescribing.'),
        (v_anxiety, v_device_id, v_pl_anxiety, 'C', 'biphasic', 'intermittent', 20, 60, 1, 10, 100, 200, 20, 'Preset range - doctor to confirm/edit before prescribing.');

    -- Insomnia / Sleep Disorder: 1-10Hz, 100-200us, biphasic, Modulation + Continuous, 20min
    INSERT INTO reference.tvns_dosing
        (condition_id, device_id, tvns_placement_id, evidence_level, wavelength, pattern,
         strength_pct_min, strength_pct_max, frequency_hz_min, frequency_hz_max,
         pulse_width_us_min, pulse_width_us_max, session_duration_min, notes)
    VALUES
        (v_insomnia, v_device_id, v_pl_insomnia, 'C', 'biphasic', 'modulation', 20, 60, 1, 10, 100, 200, 20, 'Preset range - doctor to confirm/edit before prescribing.'),
        (v_insomnia, v_device_id, v_pl_insomnia, 'C', 'biphasic', 'continuous', 20, 60, 1, 10, 100, 200, 20, 'Preset range - doctor to confirm/edit before prescribing.');
END $$;

COMMIT;
