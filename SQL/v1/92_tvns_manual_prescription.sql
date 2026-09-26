-- 92_tvns_manual_prescription.sql
--
-- APPLY ORDER: after 91.
--
-- THE PROBLEM
--
-- tVNS had no doctor-editable prescription path at all: reference.
-- tvns_placements/tvns_dosing are RLS-locked to super_admin INSERT (32's
-- own design — catalogue data is curated, never doctor-authored), and
-- unlike tDCS, tVNS has no shared plain columns on protocol_plan the
-- doctor could freely type into. tDCS's actual "free editing" was never a
-- separate custom-catalogue object at all — prescribed_current_ma/
-- prescribed_duration_min/ramp_seconds are just plain, always-available
-- columns on protocol_plan, independent of whichever catalogue dosing_id
-- (if any) was picked (39's own comment: "dosing_id is provenance ...
-- not the prescription itself"). dosing_id there is optional, freely typed
-- values are what actually gets prescribed and read off the screen.
--
-- THE FIX
--
-- Give tVNS the exact same shape: prescribed_tvns_wavelength/pattern/
-- strength_pct/frequency_hz/pulse_width_us/duration_min/ramp_up_sec/
-- ramp_down_sec as plain nullable columns on protocol_plan, freely typed
-- by the doctor within range — no FK, no RLS gate, no separate table.
-- tvns_dosing_id stays exactly what it already was (an optional catalogue
-- reference / provenance pointer), never required. fn_check_protocol_
-- prescription_complete (91) is updated the same way: tVNS now checks
-- these plain columns directly, exactly like tDCS checks prescribed_
-- current_ma/prescribed_duration_min, instead of requiring a catalogue
-- dosing_id FK.
--
-- prescribed_duration_min (the shared tDCS column) is NOT reused for tVNS
-- duration: chk_protocol_plan_duration_min caps it at 120 minutes, but the
-- tVNS duration list goes up to 4h (240 min) — a session at 2h15m+ would
-- silently violate that CHECK. prescribed_tvns_duration_min gets its own
-- column with a 240-minute cap instead, matching core.tvns_session_
-- settings' own chk_tvns_session_settings_duration_min range (90).
--
-- ear_side/auricular_site stay picked from reference.tvns_placements for
-- now (mirrors tDCS's catalogue-montage-or-nothing state before 38 added
-- custom montages) — this migration's scope is dosing only, matching what
-- was actually asked for.

BEGIN;


-- ---------------------------------------------------------------------------
-- 1. Plain tVNS prescription columns on protocol_plan
-- ---------------------------------------------------------------------------

ALTER TABLE core."protocol_plan" ADD COLUMN IF NOT EXISTS "prescribed_tvns_wavelength" TEXT;
ALTER TABLE core."protocol_plan" ADD COLUMN IF NOT EXISTS "prescribed_tvns_pattern" TEXT;
ALTER TABLE core."protocol_plan" ADD COLUMN IF NOT EXISTS "prescribed_tvns_strength_pct" SMALLINT;
ALTER TABLE core."protocol_plan" ADD COLUMN IF NOT EXISTS "prescribed_tvns_frequency_hz" NUMERIC(6,2);
ALTER TABLE core."protocol_plan" ADD COLUMN IF NOT EXISTS "prescribed_tvns_pulse_width_us" INTEGER;
ALTER TABLE core."protocol_plan" ADD COLUMN IF NOT EXISTS "prescribed_tvns_duration_min" NUMERIC(6,2);
-- Ramp only applies to pattern='intermittent' — enforced at the application
-- layer (schemas.py's _tvns_ramp_requires_intermittent), not a CHECK here,
-- same reasoning device_sessions took for its own ramp fields: a genuine
-- clinical exception isn't a schema violation.
ALTER TABLE core."protocol_plan" ADD COLUMN IF NOT EXISTS "prescribed_tvns_ramp_up_sec" INTEGER;
ALTER TABLE core."protocol_plan" ADD COLUMN IF NOT EXISTS "prescribed_tvns_ramp_down_sec" INTEGER;

COMMENT ON COLUMN core."protocol_plan"."prescribed_tvns_wavelength" IS 'tVNS prescription, freely typed by the doctor — the exact equivalent of prescribed_current_ma for tDCS. alternant | biphasic.';
COMMENT ON COLUMN core."protocol_plan"."prescribed_tvns_pattern" IS 'continuous | modulation | intermittent. Ramp fields below only apply when this is intermittent.';

ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_protocol_plan_tvns_wavelength";
ALTER TABLE core."protocol_plan" ADD CONSTRAINT "chk_protocol_plan_tvns_wavelength" CHECK
    ("prescribed_tvns_wavelength" IS NULL OR "prescribed_tvns_wavelength" IN ('alternant', 'biphasic'));

ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_protocol_plan_tvns_pattern";
ALTER TABLE core."protocol_plan" ADD CONSTRAINT "chk_protocol_plan_tvns_pattern" CHECK
    ("prescribed_tvns_pattern" IS NULL OR "prescribed_tvns_pattern" IN ('continuous', 'modulation', 'intermittent'));

ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_protocol_plan_tvns_strength_pct";
ALTER TABLE core."protocol_plan" ADD CONSTRAINT "chk_protocol_plan_tvns_strength_pct" CHECK
    ("prescribed_tvns_strength_pct" IS NULL OR "prescribed_tvns_strength_pct" BETWEEN 0 AND 100);

ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_protocol_plan_tvns_frequency_hz";
ALTER TABLE core."protocol_plan" ADD CONSTRAINT "chk_protocol_plan_tvns_frequency_hz" CHECK
    ("prescribed_tvns_frequency_hz" IS NULL OR ("prescribed_tvns_frequency_hz" >= 1 AND "prescribed_tvns_frequency_hz" <= 1000));

ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_protocol_plan_tvns_pulse_width_us";
ALTER TABLE core."protocol_plan" ADD CONSTRAINT "chk_protocol_plan_tvns_pulse_width_us" CHECK
    ("prescribed_tvns_pulse_width_us" IS NULL OR "prescribed_tvns_pulse_width_us" BETWEEN 50 AND 500);

ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_protocol_plan_tvns_duration_min";
ALTER TABLE core."protocol_plan" ADD CONSTRAINT "chk_protocol_plan_tvns_duration_min" CHECK
    ("prescribed_tvns_duration_min" IS NULL OR ("prescribed_tvns_duration_min" > 0 AND "prescribed_tvns_duration_min" <= 240));

ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_protocol_plan_tvns_ramp_up_sec";
ALTER TABLE core."protocol_plan" ADD CONSTRAINT "chk_protocol_plan_tvns_ramp_up_sec" CHECK
    ("prescribed_tvns_ramp_up_sec" IS NULL OR "prescribed_tvns_ramp_up_sec" BETWEEN 0 AND 120);

ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_protocol_plan_tvns_ramp_down_sec";
ALTER TABLE core."protocol_plan" ADD CONSTRAINT "chk_protocol_plan_tvns_ramp_down_sec" CHECK
    ("prescribed_tvns_ramp_down_sec" IS NULL OR "prescribed_tvns_ramp_down_sec" BETWEEN 0 AND 120);


-- ---------------------------------------------------------------------------
-- 2. fn_check_protocol_prescription_complete: tVNS checks its own plain
--    columns directly, same tier as tDCS's prescribed_current_ma/
--    prescribed_duration_min — no dosing_id FK requirement at all now.
-- ---------------------------------------------------------------------------

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
    ELSIF v_modality = 'tVNS' THEN
        IF NEW.prescribed_tvns_wavelength IS NULL THEN
            missing := missing || 'prescribed_tvns_wavelength';
        END IF;
        IF NEW.prescribed_tvns_pattern IS NULL THEN
            missing := missing || 'prescribed_tvns_pattern';
        END IF;
        IF NEW.prescribed_tvns_strength_pct IS NULL THEN
            missing := missing || 'prescribed_tvns_strength_pct';
        END IF;
        IF NEW.prescribed_tvns_frequency_hz IS NULL THEN
            missing := missing || 'prescribed_tvns_frequency_hz';
        END IF;
        IF NEW.prescribed_tvns_pulse_width_us IS NULL THEN
            missing := missing || 'prescribed_tvns_pulse_width_us';
        END IF;
        IF NEW.prescribed_tvns_duration_min IS NULL THEN
            missing := missing || 'prescribed_tvns_duration_min';
        END IF;
    ELSIF NEW.custom_montage_id IS NULL THEN
        -- TPS / rTMS / other: dose still lives on the catalogue dosing FK
        -- (no shared plain-column prescription exists for these yet).
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
