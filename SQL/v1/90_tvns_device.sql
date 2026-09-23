-- ###########################################################################
-- 90  tVNS device: repurpose taVNS -> tVNS, add session-time settings
-- ###########################################################################
-- taVNS (transcutaneous auricular VNS) shipped in 32_treatment_protocol.sql
-- as one of six catalogue modalities, but was never activated: no device row
-- in neuromod_devices, no seed data (see 40_seed_tdcs_reference.sql's own
-- "WHAT THIS FILE DELIBERATELY DOES NOT SEED" note). Product now wants a
-- tVNS device with a different parameter set (wavelength, pattern, % strength,
-- frequency to 1000Hz, pulse width, a fixed duration list) than taVNS's
-- clinical mA/duty-cycle dosing shape. Since taVNS was never live, this
-- renames its tables/columns/constraints in place rather than adding a
-- seventh modality next to an unused one — same tables, same triggers, same
-- RLS, just tVNS's name and (for dosing) tVNS's columns.
--
-- Session-time settings (wavelength/pattern/strength/frequency/pulse width/
-- duration) are NOT clinical dosing-catalogue data — they're picked per
-- session, same tier as device_sessions.actual_intensity_ma. They get their
-- own child table, tvns_session_settings, keyed to device_session_record_id,
-- following the device_session_scales/device_session_notes pattern in
-- 56_device_session_records.sql exactly (see that file for the split
-- rationale: one child table per session-scoped concern).

-- ---------------------------------------------------------------------------
-- 1. Rename taVNS -> tVNS throughout reference.*
-- ---------------------------------------------------------------------------

ALTER TABLE reference."tavns_placements" RENAME TO "tvns_placements";
ALTER TABLE reference."tvns_placements" RENAME COLUMN "tavns_placement_id" TO "tvns_placement_id";

ALTER TABLE reference."tvns_placements" RENAME CONSTRAINT "tavns_placements_pkey" TO "tvns_placements_pkey";
ALTER TABLE reference."tvns_placements" RENAME CONSTRAINT "uq_tavns_placements_label" TO "uq_tvns_placements_label";
ALTER TABLE reference."tvns_placements" RENAME CONSTRAINT "fk_tavns_placements_condition_id" TO "fk_tvns_placements_condition_id";
ALTER TABLE reference."tvns_placements" RENAME CONSTRAINT "fk_tavns_placements_device_id" TO "fk_tvns_placements_device_id";
ALTER TABLE reference."tvns_placements" RENAME CONSTRAINT "chk_tavns_placements_ear_side" TO "chk_tvns_placements_ear_side";

ALTER INDEX IF EXISTS reference."idx_tavns_placements_cond_dev" RENAME TO "idx_tvns_placements_cond_dev";

COMMENT ON TABLE reference."tvns_placements" IS 'tVNS placement. Sites are ear landmarks (cymba conchae, tragus, earlobe), not 10-20 scalp positions -- carried over from taVNS, which this table replaces (see 90_tvns_device.sql). Retention: Bucket 3.';

ALTER TABLE reference."tavns_dosing" RENAME TO "tvns_dosing";
ALTER TABLE reference."tvns_dosing" RENAME COLUMN "tavns_dosing_id" TO "tvns_dosing_id";
ALTER TABLE reference."tvns_dosing" RENAME COLUMN "tavns_placement_id" TO "tvns_placement_id";

ALTER TABLE reference."tvns_dosing" RENAME CONSTRAINT "tavns_dosing_pkey" TO "tvns_dosing_pkey";
ALTER TABLE reference."tvns_dosing" RENAME CONSTRAINT "fk_tavns_dosing_condition_id" TO "fk_tvns_dosing_condition_id";
ALTER TABLE reference."tvns_dosing" RENAME CONSTRAINT "fk_tavns_dosing_device_id" TO "fk_tvns_dosing_device_id";
ALTER TABLE reference."tvns_dosing" RENAME CONSTRAINT "fk_tavns_dosing_tavns_placement_id" TO "fk_tvns_dosing_tvns_placement_id";
ALTER TABLE reference."tvns_dosing" RENAME CONSTRAINT "chk_tavns_dosing_evidence_level" TO "chk_tvns_dosing_evidence_level";

ALTER INDEX IF EXISTS reference."idx_tavns_dosing_placement" RENAME TO "idx_tvns_dosing_placement";

COMMENT ON TABLE reference."tvns_dosing" IS 'Prescribable tVNS dose per condition+placement -- carried over from taVNS, which this table replaces (see 90_tvns_device.sql). Retention: Bucket 3.';

-- 1a. Swap the dosing column set for tVNS's own parameter vocabulary.
-- taVNS's mA/duty-cycle columns don't match what product specified for tVNS
-- (wavelength, pattern, % strength, frequency, pulse width). No data has
-- ever been written to this table (never seeded), so this is a straight
-- drop+add, not a data migration.
ALTER TABLE reference."tvns_dosing" DROP COLUMN IF EXISTS "intensity_ma";
ALTER TABLE reference."tvns_dosing" DROP COLUMN IF EXISTS "duty_cycle_on_sec";
ALTER TABLE reference."tvns_dosing" DROP COLUMN IF EXISTS "duty_cycle_off_sec";

ALTER TABLE reference."tvns_dosing" ADD COLUMN IF NOT EXISTS "wavelength" TEXT;
ALTER TABLE reference."tvns_dosing" ADD COLUMN IF NOT EXISTS "pattern" TEXT;
ALTER TABLE reference."tvns_dosing" ADD COLUMN IF NOT EXISTS "strength_pct_min" SMALLINT;
ALTER TABLE reference."tvns_dosing" ADD COLUMN IF NOT EXISTS "strength_pct_max" SMALLINT;

ALTER TABLE reference."tvns_dosing" DROP CONSTRAINT IF EXISTS "chk_tvns_dosing_wavelength";
ALTER TABLE reference."tvns_dosing" ADD CONSTRAINT "chk_tvns_dosing_wavelength" CHECK
    ("wavelength" IS NULL OR "wavelength" IN ('alternant', 'biphasic'));

ALTER TABLE reference."tvns_dosing" DROP CONSTRAINT IF EXISTS "chk_tvns_dosing_pattern";
ALTER TABLE reference."tvns_dosing" ADD CONSTRAINT "chk_tvns_dosing_pattern" CHECK
    ("pattern" IS NULL OR "pattern" IN ('continuous', 'modulation', 'intermittent'));

ALTER TABLE reference."tvns_dosing" DROP CONSTRAINT IF EXISTS "chk_tvns_dosing_strength_pct_range";
ALTER TABLE reference."tvns_dosing" ADD CONSTRAINT "chk_tvns_dosing_strength_pct_range" CHECK (
    ("strength_pct_min" IS NULL OR "strength_pct_min" BETWEEN 0 AND 100)
    AND ("strength_pct_max" IS NULL OR "strength_pct_max" BETWEEN 0 AND 100)
    AND ("strength_pct_min" IS NULL OR "strength_pct_max" IS NULL OR "strength_pct_min" <= "strength_pct_max")
);

ALTER TABLE reference."tvns_dosing" DROP CONSTRAINT IF EXISTS "chk_tvns_dosing_frequency_hz_range";
ALTER TABLE reference."tvns_dosing" ADD CONSTRAINT "chk_tvns_dosing_frequency_hz_range" CHECK
    ("frequency_hz" IS NULL OR ("frequency_hz" >= 1 AND "frequency_hz" <= 1000));

COMMENT ON COLUMN reference."tvns_dosing"."wavelength" IS 'Stimulation waveform: alternant or biphasic.';
COMMENT ON COLUMN reference."tvns_dosing"."pattern" IS 'Delivery pattern: continuous, modulation, or intermittent.';
COMMENT ON COLUMN reference."tvns_dosing"."strength_pct_min" IS 'Prescribable strength range, 0-100%. Device reports strength as a percentage, not mA.';
COMMENT ON COLUMN reference."tvns_dosing"."pulse_width_us" IS 'Carried over from taVNS. Device allows 50-300us in 10us steps, then 300-500us in 50us steps -- range enforced in the application layer (schemas.py), not by a CHECK, since the step size differs above/below 300 and CHECK cannot express a two-piece step function against an open value set cleanly.';
COMMENT ON COLUMN reference."tvns_dosing"."frequency_hz" IS 'Carried over from taVNS. Device allows 1-100Hz in 1Hz steps, then 100-1000Hz in 100Hz steps -- range enforced in the application layer (schemas.py) for the same reason as pulse_width_us; the CHECK here only bounds the outer range.';

-- ---------------------------------------------------------------------------
-- 2. Swap the modality vocabulary: taVNS -> tVNS
-- ---------------------------------------------------------------------------
-- Text + CHECK, never a native enum (see chk_neuromod_devices_modality's own
-- comment in 32) specifically so this replace-in-one-statement is possible.

ALTER TABLE reference."neuromod_devices" DROP CONSTRAINT IF EXISTS "chk_neuromod_devices_modality";
ALTER TABLE reference."neuromod_devices" ADD CONSTRAINT "chk_neuromod_devices_modality" CHECK
    ("modality" IN ('tDCS', 'HD-tDCS', 'tVNS', 'TPS', 'rTMS', 'other'));

UPDATE reference."neuromod_devices" SET "modality" = 'tVNS' WHERE "modality" = 'taVNS';

-- core.treatment_protocols / core.protocol_plan FK column names still say
-- "tavns_*" (tavns_placement_id, tavns_dosing_id) -- renamed for consistency
-- with the tables they now point at.
ALTER TABLE core."protocol_plan" RENAME COLUMN "tavns_placement_id" TO "tvns_placement_id";
ALTER TABLE core."protocol_plan" RENAME COLUMN "tavns_dosing_id" TO "tvns_dosing_id";

ALTER TABLE core."protocol_plan" RENAME CONSTRAINT "fk_treatment_protocols_tavns_placement_id" TO "fk_protocol_plan_tvns_placement_id";
ALTER TABLE core."protocol_plan" RENAME CONSTRAINT "fk_treatment_protocols_tavns_dosing_id" TO "fk_protocol_plan_tvns_dosing_id";

-- ---------------------------------------------------------------------------
-- 3. Repoint the modality-consistency trigger function's literal argument
-- ---------------------------------------------------------------------------
-- fn_check_device_modality is generic (takes the expected modality as
-- TG_ARGV[0]); only the trigger registration's literal needs to change.
-- Re-running CREATE TRIGGER against the renamed tables re-establishes it.

DROP TRIGGER IF EXISTS trg_check_modality_tavns_placements ON reference."tvns_placements";
CREATE TRIGGER trg_check_modality_tvns_placements
    BEFORE INSERT OR UPDATE ON reference."tvns_placements"
    FOR EACH ROW EXECUTE FUNCTION reference.fn_check_device_modality('tVNS');

DROP TRIGGER IF EXISTS trg_check_modality_tavns_dosing ON reference."tvns_dosing";
CREATE TRIGGER trg_check_modality_tvns_dosing
    BEFORE INSERT OR UPDATE ON reference."tvns_dosing"
    FOR EACH ROW EXECUTE FUNCTION reference.fn_check_device_modality('tVNS');

-- fn_check_protocol_device_consistency (core, on protocol_plan) reads the
-- FK columns by name via COALESCE, not by modality literal, so it needs no
-- logic change -- but its body is opaque text to Postgres (function bodies
-- are not rewritten on column rename the way expression-tree CHECKs are),
-- so it must be re-created with tvns_placement_id/tvns_dosing_id in place
-- of tavns_*. Based on 55_custom_montage_device_trigger_fix.sql's version
-- (the live one -- it added the custom_montage_id early-return that 32's
-- original body doesn't have; that branch is preserved here unchanged).
CREATE OR REPLACE FUNCTION core.fn_check_protocol_device_consistency()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_placement_device UUID;
    v_dosing_device    UUID;
BEGIN
    -- A custom-montage row (54) has all six placement/dosing columns NULL
    -- by construction (chk_protocol_plan_one_placement /
    -- chk_protocol_plan_dosing_requires_catalogue_placement) - there is
    -- nothing in reference.*_placements or reference.*_dosing to read a
    -- device_id from, and the service layer already verified the montage's
    -- own device_id against NEW.device_id before this INSERT ran. Skip both
    -- lookups rather than let a COALESCE-over-nothing read as a mismatch.
    IF NEW.custom_montage_id IS NOT NULL THEN
        RETURN NEW;
    END IF;

    -- Exactly one of the six is non-null (chk_protocol_plan_one_placement),
    -- so COALESCE over all six scalar subqueries yields that one's device.
    SELECT COALESCE(
        (SELECT device_id FROM reference.tdcs_placements    WHERE tdcs_placement_id    = NEW.tdcs_placement_id),
        (SELECT device_id FROM reference.hd_tdcs_placements WHERE hd_tdcs_placement_id = NEW.hd_tdcs_placement_id),
        (SELECT device_id FROM reference.tvns_placements    WHERE tvns_placement_id    = NEW.tvns_placement_id),
        (SELECT device_id FROM reference.tps_placements     WHERE tps_placement_id     = NEW.tps_placement_id),
        (SELECT device_id FROM reference.rtms_placements    WHERE rtms_placement_id    = NEW.rtms_placement_id),
        (SELECT device_id FROM reference.other_placements   WHERE other_placement_id   = NEW.other_placement_id)
    ) INTO v_placement_device;

    SELECT COALESCE(
        (SELECT device_id FROM reference.tdcs_dosing    WHERE tdcs_dosing_id    = NEW.tdcs_dosing_id),
        (SELECT device_id FROM reference.hd_tdcs_dosing WHERE hd_tdcs_dosing_id = NEW.hd_tdcs_dosing_id),
        (SELECT device_id FROM reference.tvns_dosing    WHERE tvns_dosing_id    = NEW.tvns_dosing_id),
        (SELECT device_id FROM reference.tps_dosing     WHERE tps_dosing_id     = NEW.tps_dosing_id),
        (SELECT device_id FROM reference.rtms_dosing    WHERE rtms_dosing_id    = NEW.rtms_dosing_id),
        (SELECT device_id FROM reference.other_dosing   WHERE other_dosing_id   = NEW.other_dosing_id)
    ) INTO v_dosing_device;

    IF v_placement_device IS DISTINCT FROM NEW.device_id THEN
        RAISE EXCEPTION 'Protocol device_id % does not match the placement''s device %',
            NEW.device_id, v_placement_device;
    END IF;
    IF v_dosing_device IS DISTINCT FROM NEW.device_id THEN
        RAISE EXCEPTION 'Protocol device_id % does not match the dosing''s device %',
            NEW.device_id, v_dosing_device;
    END IF;

    RETURN NEW;
END;
$function$;

-- ---------------------------------------------------------------------------
-- 4. New child table: tvns_session_settings
-- ---------------------------------------------------------------------------
-- Session-time device settings, one row per device_sessions row that uses a
-- tVNS device. Mirrors device_session_scales' shape/placement in
-- 56_device_session_records.sql: PK, FK to device_sessions, index on the FK,
-- updated_at trigger, RLS matching the other core.device_session_* tables.
--
-- Value sets (wavelength/pattern) and outer ranges (strength/frequency) are
-- enforced by CHECK; the two-piece step functions for frequency_hz (1Hz
-- steps to 100, then 100Hz steps to 1000) and pulse_width_us (10us steps to
-- 300, then 50us steps to 500) are enforced in the application layer
-- (device_sessions schemas.py), same reasoning as tvns_dosing above.

CREATE TABLE IF NOT EXISTS core."tvns_session_settings" (
    "tvns_session_setting_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "device_session_record_id" UUID NOT NULL,
    "wavelength"               TEXT NOT NULL,
    "pattern"                  TEXT NOT NULL,
    "strength_pct"             SMALLINT NOT NULL,
    "frequency_hz"             NUMERIC(6,2) NOT NULL,
    "pulse_width_us"           INTEGER NOT NULL,
    "duration_min"             NUMERIC(6,2) NOT NULL,
    "created_at"               TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_tvns_session_settings_wavelength" CHECK ("wavelength" IN ('alternant', 'biphasic')),
    CONSTRAINT "chk_tvns_session_settings_pattern" CHECK ("pattern" IN ('continuous', 'modulation', 'intermittent')),
    CONSTRAINT "chk_tvns_session_settings_strength_pct" CHECK ("strength_pct" BETWEEN 0 AND 100),
    CONSTRAINT "chk_tvns_session_settings_frequency_hz" CHECK ("frequency_hz" >= 1 AND "frequency_hz" <= 1000),
    CONSTRAINT "chk_tvns_session_settings_pulse_width_us" CHECK ("pulse_width_us" BETWEEN 50 AND 500),
    CONSTRAINT "chk_tvns_session_settings_duration_min" CHECK ("duration_min" > 0 AND "duration_min" <= 240)
);
COMMENT ON TABLE core."tvns_session_settings" IS 'tVNS device settings actually used for one session: waveform, pattern, strength, frequency, pulse width, duration. One row per device_sessions row for a tVNS-modality protocol. Allowed value sets/step sizes: wavelength (alternant, biphasic); pattern (continuous, modulation, intermittent); strength_pct 0-100; frequency_hz 1-100 in 1Hz steps then 100-1000 in 100Hz steps (app-enforced); pulse_width_us 50-300 in 10us steps then 300-500 in 50us steps (app-enforced); duration_min one of a fixed list from 5 to 240 (4h) including the 10-minute-interval long sessions (70, 80, ... 135, 150, 165, 180, 195, 210, 225, 240) (app-enforced). Retention: clinical, Bucket 2 -- same tier as device_sessions.';
COMMENT ON COLUMN core."tvns_session_settings"."duration_min" IS 'Minutes, NUMERIC to hold the UI''s "1h10min" etc. as 70.00 exactly. Fixed allowed list (5,10,15,...,60,70,80,...,240) is app-enforced (device_sessions schemas.py), not a CHECK -- an explicit IN-list of ~26 values belongs in one place next to the UI copy that renders it, not duplicated into SQL.';

ALTER TABLE core."tvns_session_settings" DROP CONSTRAINT IF EXISTS "tvns_session_settings_pkey" CASCADE;
ALTER TABLE core."tvns_session_settings" ADD CONSTRAINT "tvns_session_settings_pkey" PRIMARY KEY ("tvns_session_setting_id");

ALTER TABLE core."tvns_session_settings" DROP CONSTRAINT IF EXISTS "uq_tvns_session_settings_session";
ALTER TABLE core."tvns_session_settings" ADD CONSTRAINT "uq_tvns_session_settings_session" UNIQUE ("device_session_record_id");

ALTER TABLE core."tvns_session_settings" DROP CONSTRAINT IF EXISTS "fk_tvns_session_settings_device_session";
ALTER TABLE core."tvns_session_settings" ADD CONSTRAINT "fk_tvns_session_settings_device_session"
    FOREIGN KEY ("device_session_record_id") REFERENCES core."device_sessions" ("device_session_record_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_tvns_session_settings_device_session ON core."tvns_session_settings" USING btree ("device_session_record_id");

DROP TRIGGER IF EXISTS trg_tvns_session_settings_updated_at ON core."tvns_session_settings";
CREATE TRIGGER trg_tvns_session_settings_updated_at
    BEFORE UPDATE ON core."tvns_session_settings"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();

ALTER TABLE core."tvns_session_settings" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."tvns_session_settings" FORCE  ROW LEVEL SECURITY;

-- Same access model as device_session_scales (56): staff on the appointment
-- (patient/clinic/doctor/CA) or assigned clinic staff can see it; only CA/
-- clinic_admin/super_admin/system can write it (settings are entered by the
-- CA running the session, not the patient).
DROP POLICY IF EXISTS "rls_tvns_session_settings_select" ON core."tvns_session_settings";
CREATE POLICY "rls_tvns_session_settings_select" ON core."tvns_session_settings" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR (device_session_record_id IN (
            SELECT ds.device_session_record_id FROM device_sessions ds
            JOIN appointments a ON a.appointment_id = ds.appointment_id
            WHERE a.patient_id = rls_user_id()
               OR a.clinic_id  = rls_clinic_id()
               OR a.doctor_id  = rls_user_id()
               OR a.ca_id      = rls_user_id()))
        OR (device_session_record_id IN (
            SELECT ds.device_session_record_id FROM device_sessions ds
            JOIN appointments a ON a.appointment_id = ds.appointment_id
            JOIN clinic_staff_assignments s ON s.clinic_id = a.clinic_id
            WHERE s.profile_id = rls_user_id() AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_tvns_session_settings_insert" ON core."tvns_session_settings";
CREATE POLICY "rls_tvns_session_settings_insert" ON core."tvns_session_settings" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'system'::text])
    );

DROP POLICY IF EXISTS "rls_tvns_session_settings_update" ON core."tvns_session_settings";
CREATE POLICY "rls_tvns_session_settings_update" ON core."tvns_session_settings" FOR UPDATE TO public
    USING (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'system'::text])
    );

-- ---------------------------------------------------------------------------
-- 5. Grants
-- ---------------------------------------------------------------------------
-- 18_grants.sql's ALTER DEFAULT PRIVILEGES only covers tables created by the
-- role that set it — every migration since has granted new tables
-- explicitly (see 32 Sec.11, 56 Sec.12). Same split as device_sessions and
-- its other children: app reads/inserts/updates, deletion belongs to the
-- purge worker.

GRANT SELECT, INSERT, UPDATE ON core."tvns_session_settings" TO anava_app;
REVOKE DELETE ON core."tvns_session_settings" FROM anava_app;
GRANT SELECT ON core."tvns_session_settings" TO anava_readonly;

-- Bucket 2 (patient data, anonymise never hard-delete): an erasure or
-- portability request has to be able to see what it is reporting on.
GRANT SELECT ON core."tvns_session_settings" TO anava_compliance;
