-- 91_prescription_complete_per_modality.sql
--
-- APPLY ORDER: after 90.
--
-- THE PROBLEM
--
-- core.fn_check_protocol_prescription_complete() (39_protocol_prescription_
-- fields.sql, trigger trg_check_protocol_prescription_complete on
-- core.protocol_plan) unconditionally requires prescribed_current_ma and
-- prescribed_duration_min to be non-null before a protocol can reach status
-- 'active'/'completed' — regardless of the protocol's device modality.
-- Those two columns are tDCS/HD-tDCS-shaped (current in mA, session
-- duration in minutes charged against the device's ramp) and were the only
-- modality this wizard ever actually activated protocols for, so the gap
-- went unnoticed. tVNS doses in wavelength/pattern/strength%/frequency/
-- pulse-width — it has no "current_ma" concept at all — so every tVNS
-- protocol 400s on activation:
--
--   "Prescription is incomplete and cannot be activated. Missing:
--    prescribed_current_ma, prescribed_duration_min"
--
-- even when its tvns_dosing_id correctly points at a real catalogued dose.
-- (TPS/rTMS/other have the same latent gap — millijoules and %-motor-
-- threshold dosing don't map to mA either — but tVNS is the first of them
-- to actually reach activation.)
--
-- THE FIX
--
-- The real completeness requirement is modality-specific:
--   tDCS / HD-tDCS  prescribed_current_ma AND prescribed_duration_min
--                   must be set (the CA reads these off the screen and
--                   sets them on the machine — 39's own header comment).
--   every other      its own dosing_id (tvns_dosing_id / tps_dosing_id /
--   modality          rtms_dosing_id / other_dosing_id) must be set — the
--                     catalogue row it points at already carries that
--                     modality's real dose (wavelength/pattern/strength_pct
--                     for tVNS, etc.), so nothing further needs to be typed
--                     into the shared mA/duration columns.
--
-- sessions_per_week stays a universal requirement — visit cadence, not a
-- dose parameter, applies the same way to every modality.
BEGIN;


CREATE OR REPLACE FUNCTION core.fn_check_protocol_prescription_complete()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    missing  TEXT[] := ARRAY[]::TEXT[];
    v_modality TEXT;
BEGIN
    IF NEW.status NOT IN ('active', 'completed') THEN
        RETURN NEW;
    END IF;

    SELECT modality INTO v_modality
    FROM reference.neuromod_devices WHERE device_id = NEW.device_id;

    IF v_modality IN ('tDCS', 'HD-tDCS') THEN
        IF NEW.prescribed_current_ma IS NULL THEN
            missing := missing || 'prescribed_current_ma';
        END IF;
        IF NEW.prescribed_duration_min IS NULL THEN
            missing := missing || 'prescribed_duration_min';
        END IF;
    ELSIF NEW.custom_montage_id IS NULL THEN
        -- Every other catalogue-placement modality: its own dosing FK is
        -- the prescription (chk_protocol_plan_one_dosing already limits
        -- this to at most one non-null column, so checking all six covers
        -- whichever modality NEW actually is without a second modality
        -- branch per table). A custom-montage protocol (54) has no
        -- catalogue dosing at all by design — chk_protocol_plan_dosing_
        -- requires_catalogue_placement already enforces that pairing, so
        -- it's exempt from this check the same way tDCS's own manual
        -- current/duration fields serve as ITS prescription.
        IF num_nonnulls(
            NEW.tvns_dosing_id, NEW.tps_dosing_id, NEW.rtms_dosing_id, NEW.other_dosing_id
        ) = 0 THEN
            missing := missing || 'dosing_id';
        END IF;
    END IF;

    IF NEW.sessions_per_week IS NULL THEN
        missing := missing || 'sessions_per_week';
    END IF;

    IF array_length(missing, 1) > 0 THEN
        RAISE EXCEPTION
            'Protocol % cannot be % — prescription incomplete: %',
            NEW.protocol_id, NEW.status, array_to_string(missing, ', ')
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$function$;


COMMIT;
