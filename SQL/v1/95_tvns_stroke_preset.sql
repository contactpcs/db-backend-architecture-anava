-- 95_tvns_stroke_preset.sql
--
-- Doctor asked to add disease 5 back (previously excluded):
--   Stroke Rehabilitation - Motor Function (Hemiplegia)
--   Frequency: 50-100Hz  Pulse Width: 200-300us  Waveform: Biphasic
--   Mode: Modulation/Intermittent  Duration: 20min
-- Same pattern as 94_tvns_disease_presets_data.sql: one dummy placement,
-- two dosing rows (one per mode).

BEGIN;

DO $$
DECLARE
    v_device_id UUID := 'd97db782-e4fc-4884-89a5-d8e8f6f86655'; -- TVNS-001
    v_stroke UUID;
    v_pl_stroke UUID;
BEGIN
    INSERT INTO reference.neuromod_conditions (condition_name, display_order)
    VALUES ('Stroke Rehabilitation - Motor Function (Hemiplegia)', 65)
    ON CONFLICT (condition_name) DO NOTHING;

    SELECT condition_id INTO v_stroke FROM reference.neuromod_conditions
    WHERE condition_name = 'Stroke Rehabilitation - Motor Function (Hemiplegia)';

    INSERT INTO reference.tvns_placements (condition_id, device_id, montage_label, ear_side, auricular_site)
    VALUES (v_stroke, v_device_id, 'Standard — Left Cymba Conchae', 'left', 'cymba_conchae')
    ON CONFLICT (condition_id, device_id, montage_label) DO NOTHING
    RETURNING tvns_placement_id INTO v_pl_stroke;
    IF v_pl_stroke IS NULL THEN
        SELECT tvns_placement_id INTO v_pl_stroke FROM reference.tvns_placements
        WHERE condition_id = v_stroke AND device_id = v_device_id LIMIT 1;
    END IF;

    INSERT INTO reference.tvns_dosing
        (condition_id, device_id, tvns_placement_id, evidence_level, wavelength, pattern,
         strength_pct_min, strength_pct_max, frequency_hz_min, frequency_hz_max,
         pulse_width_us_min, pulse_width_us_max, session_duration_min, notes)
    VALUES
        (v_stroke, v_device_id, v_pl_stroke, 'C', 'biphasic', 'modulation',   20, 60, 50, 100, 200, 300, 20, 'Preset range - doctor to confirm/edit before prescribing.'),
        (v_stroke, v_device_id, v_pl_stroke, 'C', 'biphasic', 'intermittent', 20, 60, 50, 100, 200, 300, 20, 'Preset range - doctor to confirm/edit before prescribing.');
END $$;

COMMIT;
