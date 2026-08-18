-- 41_device_capacity_per_device.sql
--
-- Re-scopes device-session capacity from ONE number per clinic to one pool per
-- DEVICE the clinic owns (core.clinic_devices.clinic_device_id).
--
-- SCOPE: clinic_device_schedules, clinic_device_schedule_overrides, appointments,
-- clinic_devices (one added constraint), and core.fn_generate_protocol_sessions.
-- Nothing else.
--
-- APPLY ORDER: after 37 (needs clinic_devices) and 40. Independent of 38-39.
--
-- ⚠ NOT a "not yet executed anywhere" file like 30-40 were at the time they were
-- written. Those files WERE applied by hand to Anava_App_v1 (2026-08-13/14, per
-- 0031/0032's own docstrings) before this one existed. This file only ever ALTERs
-- what they created — it does not, and must not, edit 36 or 37 in place. Same
-- convention 33 already used to alter what 30 created.
--
-- If clinic_device_schedules / clinic_device_schedule_overrides already hold rows
-- on the target database (unlikely — the admin screen that writes them shipped in
-- the same round of work as this file), the new NOT NULL clinic_device_id backfill
-- below will fail loudly rather than silently guessing a device. Delete those rows
-- first; nothing before this file could have produced a correct value for them.
--
--
-- ###########################################################################
-- WHY
-- ###########################################################################
--
-- Before this file, clinic_device_schedules.capacity was one admin-typed number
-- per (clinic_id, day_of_week) — shared by every device the clinic owns,
-- regardless of modality. A clinic with 2 tDCS units and 1 rTMS unit had ONE
-- pool, not two. Nothing tied capacity to core.clinic_devices.quantity, which
-- already records exactly how many of each device the clinic has (37, deliberate
-- at the time — capacity also depends on staffing, not device count alone).
--
-- This file re-keys both schedule tables to clinic_devices.clinic_device_id, so
-- each device the clinic owns gets its own weekly hours and its own capacity.
-- Capacity STAYS an admin-typed number (37's staffing argument still holds) — the
-- application layer now shows quantity x hours / slot_duration as a suggested
-- default, computed client-side, never enforced here.
--
-- The harder gap: core.appointments never carried a device or modality column at
-- all. A device_session row was only reachable to its device via
-- protocol_id -> treatment_protocols.device_id, and even that link was known but
-- discarded at the moment fn_generate_protocol_sessions created the row. This
-- file adds appointments.clinic_device_id and makes the generator populate it,
-- so the per-device capacity count (idx_appointments_device_capacity) can filter
-- on it directly instead of joining through protocol_id on every check.
--
--
-- ###########################################################################
-- CROSS-CLINIC GUARD
-- ###########################################################################
--
-- A schedule row or an appointment pointing clinic_device_id at a device that
-- belongs to a DIFFERENT clinic than its own clinic_id would silently book
-- capacity nobody administers. Rather than trust the application to keep the two
-- consistent, clinic_devices gets a UNIQUE(clinic_device_id, clinic_id) — trivial,
-- since clinic_device_id is already its PK — so every table below can carry a
-- COMPOSITE FK against that pair. Postgres then refuses, at the constraint level,
-- any row whose clinic_id disagrees with the device's actual clinic. Same spirit
-- as 32's protocol/placement/dosing device-match trigger, done here with a
-- constraint instead of a trigger because there is no third table's worth of
-- branching to justify PL/pgSQL.


BEGIN;


-- ###########################################################################
-- 1  core.clinic_devices — the guard's parent constraint
-- ###########################################################################

ALTER TABLE core."clinic_devices" DROP CONSTRAINT IF EXISTS "uq_clinic_devices_id_clinic";
ALTER TABLE core."clinic_devices"
    ADD CONSTRAINT "uq_clinic_devices_id_clinic"
    UNIQUE ("clinic_device_id", "clinic_id");


-- ###########################################################################
-- 2  core.clinic_device_schedules — re-key clinic_id + day_of_week
--                                    -> clinic_device_id + day_of_week
-- ###########################################################################

ALTER TABLE core."clinic_device_schedules" ADD COLUMN IF NOT EXISTS "clinic_device_id" UUID;
COMMENT ON COLUMN core."clinic_device_schedules"."clinic_device_id" IS 'Which device this weekly rule belongs to (core.clinic_devices). The pool this row describes is one device''s, not the whole clinic''s.';

-- Backfill would need a real device to assign each existing row to, and no
-- earlier file recorded one. Failing here is correct — see the file header.
ALTER TABLE core."clinic_device_schedules" ALTER COLUMN "clinic_device_id" SET NOT NULL;

ALTER TABLE core."clinic_device_schedules" DROP CONSTRAINT IF EXISTS "clinic_device_schedules_clinic_id_day_of_week_key";
ALTER TABLE core."clinic_device_schedules"
    ADD CONSTRAINT "clinic_device_schedules_clinic_device_id_day_of_week_key"
    UNIQUE ("clinic_device_id", "day_of_week");

ALTER TABLE core."clinic_device_schedules" DROP CONSTRAINT IF EXISTS "fk_clinic_device_schedules_device";
ALTER TABLE core."clinic_device_schedules"
    ADD CONSTRAINT "fk_clinic_device_schedules_device"
    FOREIGN KEY ("clinic_device_id", "clinic_id")
    REFERENCES core."clinic_devices" ("clinic_device_id", "clinic_id") ON DELETE RESTRICT;

-- The per-device lookup (lock_capacity_for_day, list_for_device) is now the hot
-- path; the clinic-wide overview (every device's week, for the admin landing
-- screen) still uses idx_cds_clinic_active, kept as-is.
CREATE INDEX IF NOT EXISTS idx_cds_device_active
    ON core."clinic_device_schedules" USING btree ("clinic_device_id") WHERE "is_active";


-- ###########################################################################
-- 3  core.clinic_device_schedule_overrides — same re-key
-- ###########################################################################

ALTER TABLE core."clinic_device_schedule_overrides" ADD COLUMN IF NOT EXISTS "clinic_device_id" UUID;
COMMENT ON COLUMN core."clinic_device_schedule_overrides"."clinic_device_id" IS 'Which device this single-day exception applies to. Same re-scoping as clinic_device_schedules.clinic_device_id.';

ALTER TABLE core."clinic_device_schedule_overrides" ALTER COLUMN "clinic_device_id" SET NOT NULL;

ALTER TABLE core."clinic_device_schedule_overrides" DROP CONSTRAINT IF EXISTS "clinic_device_schedule_overrides_clinic_id_override_date_key";
ALTER TABLE core."clinic_device_schedule_overrides"
    ADD CONSTRAINT "clinic_device_schedule_overrides_device_id_override_date_key"
    UNIQUE ("clinic_device_id", "override_date");

ALTER TABLE core."clinic_device_schedule_overrides" DROP CONSTRAINT IF EXISTS "fk_clinic_device_schedule_overrides_device";
ALTER TABLE core."clinic_device_schedule_overrides"
    ADD CONSTRAINT "fk_clinic_device_schedule_overrides_device"
    FOREIGN KEY ("clinic_device_id", "clinic_id")
    REFERENCES core."clinic_devices" ("clinic_device_id", "clinic_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_cdso_device_date
    ON core."clinic_device_schedule_overrides" USING btree ("clinic_device_id", "override_date");


-- ###########################################################################
-- 4  core.appointments — the missing link
-- ###########################################################################
-- Nullable at the column level: only a device_session row uses it, and the CHECK
-- below is what actually enforces "device_session rows must have one, nothing
-- else may". Every device_session row is created by fn_generate_protocol_sessions
-- (32) with protocol_id already set, so the device is always resolvable at INSERT
-- time — there is no in-between state where a device_session row legitimately
-- has no device yet.

ALTER TABLE core."appointments" ADD COLUMN IF NOT EXISTS "clinic_device_id" UUID;
COMMENT ON COLUMN core."appointments"."clinic_device_id" IS 'Which of the clinic''s devices this session runs on. Set only for appointment_type = device_session (chk_appointments_device_session_has_device); NULL for every other type, which books a doctor''s calendar instead. The per-device capacity count filters on this column directly rather than joining through protocol_id -> treatment_protocols.device_id on every check.';

ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "fk_appointments_clinic_device";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "fk_appointments_clinic_device"
    FOREIGN KEY ("clinic_device_id", "clinic_id")
    REFERENCES core."clinic_devices" ("clinic_device_id", "clinic_id") ON DELETE RESTRICT;

ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "chk_appointments_device_session_has_device";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_device_session_has_device"
    CHECK (("appointment_type" = 'device_session') = ("clinic_device_id" IS NOT NULL));

-- Replaces the clinic-wide version of this index (36 §6). Same partial predicate,
-- device_device_id in place of clinic_id — the capacity count and lock now filter
-- on one device's slot, not every device_session in the clinic at once.
DROP INDEX IF EXISTS idx_appointments_device_capacity;
CREATE INDEX IF NOT EXISTS idx_appointments_device_capacity
    ON core."appointments" USING btree ("clinic_device_id", "appointment_date", "start_time")
    WHERE "appointment_type" = 'device_session'
      AND "status" IN ('selected', 'paid', 'checked_in', 'in_progress');


-- ###########################################################################
-- 5  core.fn_generate_protocol_sessions — stop discarding the device
-- ###########################################################################
-- Identical to 32's version except for the one thing it now resolves and writes.
-- v_protocol.device_id was always in scope here; it just never reached the row.

CREATE OR REPLACE FUNCTION core.fn_generate_protocol_sessions(
    p_protocol_id   UUID,
    p_start_date    DATE,
    p_slot_days     INTEGER DEFAULT 2,
    p_followup_gap  INTEGER DEFAULT 1
)
RETURNS INTEGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_protocol         core.treatment_protocols%ROWTYPE;
    v_plan              core.treatment_plans%ROWTYPE;
    v_clinic_id         UUID;
    v_clinic_device_id  UUID;
    v_i                 INTEGER;
    v_date              DATE;
    v_created           INTEGER := 0;
BEGIN
    SELECT * INTO v_protocol FROM core.treatment_protocols WHERE protocol_id = p_protocol_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Protocol % not found', p_protocol_id;
    END IF;

    SELECT * INTO v_plan FROM core.treatment_plans WHERE plan_id = v_protocol.plan_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Plan % not found for protocol %', v_protocol.plan_id, p_protocol_id;
    END IF;

    -- treatment_plans carries no clinic_id of its own; it comes from the cycle.
    SELECT clinic_id INTO v_clinic_id FROM core.treatment_cycles WHERE cycle_id = v_plan.cycle_id;
    IF v_clinic_id IS NULL THEN
        RAISE EXCEPTION 'Cannot resolve clinic for plan % (cycle %)', v_plan.plan_id, v_plan.cycle_id;
    END IF;

    -- The device this protocol prescribes, as this clinic's inventory row.
    -- trg_check_device_available_at_clinic (37) already refused the protocol at
    -- prescribe-time if the clinic does not have this device; this is a
    -- belt-and-suspenders re-check at generation time, not the primary guard.
    SELECT clinic_device_id INTO v_clinic_device_id
    FROM core.clinic_devices
    WHERE clinic_id = v_clinic_id AND device_id = v_protocol.device_id;
    IF v_clinic_device_id IS NULL THEN
        RAISE EXCEPTION 'Clinic % does not have device % in its inventory', v_clinic_id, v_protocol.device_id;
    END IF;

    -- Refuse to double-generate rather than silently appending a second course.
    IF EXISTS (SELECT 1 FROM core.appointments WHERE protocol_id = p_protocol_id) THEN
        RAISE EXCEPTION 'Protocol % has already generated its appointments', p_protocol_id;
    END IF;

    FOR v_i IN 1..v_protocol.session_count LOOP
        v_date := p_start_date + ((v_i - 1) * p_slot_days);

        -- Device session. doctor_id NULL (CA-administered), no time yet.
        INSERT INTO core.appointments (
            clinic_id, patient_id, doctor_id, plan_id, protocol_id,
            appointment_date, appointment_type, session_number,
            status, booked_by, booked_by_role, clinic_device_id
        ) VALUES (
            v_clinic_id, v_plan.patient_id, NULL, v_plan.plan_id, p_protocol_id,
            v_date, 'device_session', v_i,
            'planned', v_protocol.set_by, 'doctor', v_clinic_device_id
        );
        v_created := v_created + 1;

        -- Follow-up consultation after every Nth session. Carries doctor_id:
        -- this one IS the doctor's visit. No device — untouched.
        IF v_protocol.follow_up_every_n IS NOT NULL
           AND v_i % v_protocol.follow_up_every_n = 0 THEN

            INSERT INTO core.appointments (
                clinic_id, patient_id, doctor_id, plan_id, protocol_id,
                appointment_date, appointment_type, session_number,
                status, booked_by, booked_by_role
            ) VALUES (
                v_clinic_id, v_plan.patient_id, v_plan.doctor_id, v_plan.plan_id, p_protocol_id,
                v_date + p_followup_gap, 'protocol_followup', v_i,
                'planned', v_protocol.set_by, 'doctor'
            );
            v_created := v_created + 1;
        END IF;
    END LOOP;

    RETURN v_created;
END;
$function$;

COMMENT ON FUNCTION core.fn_generate_protocol_sessions IS 'Generates every device-session and follow-up appointment for a protocol, all in status planned with a date and no time. Device sessions now carry clinic_device_id, resolved from the protocol''s device_id against this clinic''s inventory (41). Returns the number of rows created. Calendar placement is a fixed day-gap placeholder — real slotting needs doctor_weekly_schedules and doctor_schedule_overrides, which is application work. See 32 OPEN ITEM 1.';


COMMIT;


-- ###########################################################################
-- NOT CHANGED, ON PURPOSE
-- ###########################################################################
--
--  1. RLS policies on clinic_device_schedules / clinic_device_schedule_overrides
--     (36 §8) — untouched. clinic_id stays on both tables specifically so those
--     policies keep working unmodified; clinic_device_id is an added dimension,
--     not a replacement for the RLS scoping column.
--
--  2. Grants — untouched. No new table, only new columns on already-granted ones.
--
--  3. Capacity is still an admin-typed integer, not a generated column. 37's
--     staffing argument (devices owned is not the same number as devices staffed)
--     still holds per-device, not just per-clinic. The application layer computes
--     quantity x hours / slot_duration as a shown default; nothing here enforces
--     the arithmetic.
--
--
-- ###########################################################################
-- OPEN ITEMS
-- ###########################################################################
--
--  1. The admin capacity board (GET /clinics/{id}/device-availability) becomes a
--     loop over each active clinic_device rather than one query, now that the
--     pool is per-device. Fine at today's per-clinic device counts; revisit if a
--     clinic ever owns dozens of devices.
--
--  2. effective_from / effective_until (36 open item 3) are still carried but
--     still not read by the slot builder. Unchanged by this file.
