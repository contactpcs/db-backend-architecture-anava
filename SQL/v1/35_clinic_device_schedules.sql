-- 35_clinic_device_schedules.sql
--
-- Hand-written net-new DDL (like 30-34).
--
-- SCOPE: clinic device CAPACITY — when a clinic can run device sessions on a
-- given device, and how many at once. Closes item A of 33's "STILL OPEN" list.
--
-- APPLY ORDER:  30 -> 31 -> 32 -> 33 -> 34 -> 35
--
-- NOT YET EXECUTED ANYWHERE. Written against the schema as recorded in
-- SQL/v1/00-34 and verified by reading those files, not by running this one.
--
--
-- THE PROBLEM
--
-- 32's fn_generate_protocol_sessions writes device_session rows in status
-- 'planned' with a DATE and no TIME. The patient later picks a time, moving the
-- row to 'selected'. Nothing in 30-34 says which times are pickable.
--
-- A device session is not booked against a doctor's calendar. 30 made
-- appointments.doctor_id nullable precisely so a CA-administered session does
-- not occupy the supervising doctor's diary, and excl_doctor_overlap drops those
-- rows on the NULL. So doctor_weekly_schedules cannot answer "when can this
-- patient come in for session 7" — it is the wrong resource entirely.
--
-- What actually constrains a device session is the clinic's hardware: how many
-- units of that device the clinic has, and the hours it runs them.
--
--
-- WHY CAPACITY IS NOT A CONSTRAINT
--
-- The rule is "at most N device_session rows may overlap this window for this
-- device at this clinic". PostgreSQL has no declarative form for that:
--
--   - CHECK cannot see other rows.
--   - UNIQUE handles N = 1 only.
--   - EXCLUDE also handles N = 1 only — it forbids ALL overlap, and a clinic
--     with three tDCS units must permit exactly three.
--
-- So capacity is enforced in application code, by counting under a lock. The
-- pattern is given at the bottom of this file. What the DATABASE guarantees
-- here is narrower and still worth having: the schedule rows are well-formed,
-- the windows are sane, and a device cannot be scheduled at a clinic that does
-- not have it.
--
-- SAFE TO APPLY: both tables are net-new and start empty. Nothing else in the
-- schema references them yet.


BEGIN;


-- ---------------------------------------------------------------------------
-- 1. Which devices a clinic actually has, and how many
-- ---------------------------------------------------------------------------
-- 32 registered device TYPES (manufacturer + model + modality). This says which
-- of those a given clinic owns and in what quantity — the N in the capacity
-- rule. Without it, "how many tDCS sessions can run at 10:00" has no answer.
--
-- This is NOT per-unit hardware tracking (serial numbers, service history,
-- calibration dates). That remains 32's OPEN ITEM 11. unit_count is a plain
-- integer on purpose: a clinic that needs to know WHICH of its three units ran
-- a session needs the per-unit table, and this column should then become a
-- derived count rather than a second source of truth.

CREATE TABLE IF NOT EXISTS core."clinic_devices" (
    "clinic_device_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_id"        UUID NOT NULL,
    "device_id"        UUID NOT NULL,
    "unit_count"       INTEGER NOT NULL DEFAULT 1,
    "is_active"        BOOLEAN NOT NULL DEFAULT true,
    "commissioned_on"  DATE,
    "notes"            TEXT,
    "created_by"       UUID,
    "created_at"       TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_clinic_devices_unit_count" CHECK ("unit_count" > 0)
);
COMMENT ON TABLE core."clinic_devices" IS 'Which device types a clinic holds and how many units of each. The N in the capacity rule: at most unit_count device sessions on this device may overlap at this clinic. Retention: operational, not clinical — but a historic session must still resolve the device it ran on, so rows are deactivated, never deleted.';
COMMENT ON COLUMN core."clinic_devices"."unit_count" IS 'Number of physical units. Two tDCS machines from the same manufacturer are unit_count = 2 on one row, not two rows — see the uq_clinic_devices unique.';
COMMENT ON COLUMN core."clinic_devices"."is_active" IS 'false = withdrawn from service. Existing sessions keep resolving; the booking path stops offering slots.';


-- ---------------------------------------------------------------------------
-- 2. When those devices run
-- ---------------------------------------------------------------------------
-- Deliberately shaped like core.doctor_weekly_schedules (05_tables_core.sql):
-- same day_of_week + start/end + slot_duration + effective_from/until columns,
-- same override companion. A reader who knows one knows the other, and the
-- availability code can share its slot-expansion logic.
--
-- The difference is what it describes: a doctor schedule yields one bookable
-- slot per window, a device schedule yields unit_count of them.

CREATE TABLE IF NOT EXISTS core."clinic_device_schedules" (
    "schedule_id"           UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_device_id"      UUID NOT NULL,
    "day_of_week"           SMALLINT NOT NULL,
    "start_time"            TIME NOT NULL,
    "end_time"              TIME NOT NULL,
    "slot_duration_minutes" INTEGER NOT NULL DEFAULT 30,
    "break_start"           TIME,
    "break_end"             TIME,
    "is_active"             BOOLEAN NOT NULL DEFAULT true,
    "effective_from"        DATE,
    "effective_until"       DATE,
    "created_by"            UUID,
    "created_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- 0 = Sunday, matching core.doctor_weekly_schedules and PostgreSQL's
    -- EXTRACT(DOW). Stated as a CHECK because the source table has none, and a
    -- day_of_week of 7 silently yields a schedule that never matches anything.
    CONSTRAINT "chk_cds_day_of_week" CHECK ("day_of_week" BETWEEN 0 AND 6),
    CONSTRAINT "chk_cds_time_order"  CHECK ("end_time" > "start_time"),
    CONSTRAINT "chk_cds_slot_duration" CHECK ("slot_duration_minutes" > 0),
    -- A break is both bounds or neither, and must sit inside the window.
    CONSTRAINT "chk_cds_break_pair" CHECK (("break_start" IS NULL) = ("break_end" IS NULL)),
    CONSTRAINT "chk_cds_break_window" CHECK (
        "break_start" IS NULL
        OR ("break_end" > "break_start"
            AND "break_start" >= "start_time"
            AND "break_end"   <= "end_time")
    ),
    CONSTRAINT "chk_cds_effective_range" CHECK (
        "effective_from" IS NULL OR "effective_until" IS NULL
        OR "effective_until" >= "effective_from"
    )
);
COMMENT ON TABLE core."clinic_device_schedules" IS 'Recurring weekly hours during which a clinic runs a device. One row per (clinic device, day of week, window). Each window yields clinic_devices.unit_count concurrent slots, not one.';
COMMENT ON COLUMN core."clinic_device_schedules"."day_of_week" IS '0 = Sunday, matching core.doctor_weekly_schedules and EXTRACT(DOW).';
COMMENT ON COLUMN core."clinic_device_schedules"."effective_until" IS 'NULL = open-ended. Set it rather than deleting the row when hours change: a session booked last month must still be explicable by the schedule in force at the time.';


-- ---------------------------------------------------------------------------
-- 3. Exceptions to the weekly pattern
-- ---------------------------------------------------------------------------
-- Public holidays, servicing, a one-off extended day. Mirrors
-- core.doctor_schedule_overrides, including its is_available default of false —
-- the common case is a closure, not an extension.

CREATE TABLE IF NOT EXISTS core."clinic_device_schedule_overrides" (
    "override_id"           UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_device_id"      UUID NOT NULL,
    "override_date"         DATE NOT NULL,
    "is_available"          BOOLEAN NOT NULL DEFAULT false,
    "start_time"            TIME,
    "end_time"              TIME,
    "slot_duration_minutes" INTEGER,
    "unit_count"            INTEGER,
    "reason"                TEXT,
    "created_by"            UUID NOT NULL,
    "created_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- An availability override must say when; a closure must not.
    CONSTRAINT "chk_cdso_available_has_window" CHECK (
        ("is_available" = false AND "start_time" IS NULL AND "end_time" IS NULL)
        OR ("is_available" = true AND "start_time" IS NOT NULL AND "end_time" IS NOT NULL)
    ),
    CONSTRAINT "chk_cdso_time_order" CHECK (
        "start_time" IS NULL OR "end_time" > "start_time"
    ),
    CONSTRAINT "chk_cdso_slot_duration" CHECK (
        "slot_duration_minutes" IS NULL OR "slot_duration_minutes" > 0
    ),
    -- 0 is meaningful here and NOT a closure: it means the window is open but
    -- no units are free (all out for service). Closure is is_available = false.
    CONSTRAINT "chk_cdso_unit_count" CHECK ("unit_count" IS NULL OR "unit_count" >= 0)
);
COMMENT ON TABLE core."clinic_device_schedule_overrides" IS 'Per-date exceptions to the weekly device schedule: holidays, servicing, one-off extended hours. Takes precedence over clinic_device_schedules for that date.';
COMMENT ON COLUMN core."clinic_device_schedule_overrides"."unit_count" IS 'Overrides clinic_devices.unit_count for this date — e.g. 1 of 3 machines out for service. NULL = use the clinic_devices figure. 0 with is_available = true is legal and means open but nothing free.';


-- ---------------------------------------------------------------------------
-- 4. Keys
-- ---------------------------------------------------------------------------

ALTER TABLE core."clinic_devices"                   DROP CONSTRAINT IF EXISTS "clinic_devices_pkey" CASCADE;
ALTER TABLE core."clinic_devices"                   ADD CONSTRAINT "clinic_devices_pkey"                   PRIMARY KEY ("clinic_device_id");
ALTER TABLE core."clinic_device_schedules"          DROP CONSTRAINT IF EXISTS "clinic_device_schedules_pkey" CASCADE;
ALTER TABLE core."clinic_device_schedules"          ADD CONSTRAINT "clinic_device_schedules_pkey"          PRIMARY KEY ("schedule_id");
ALTER TABLE core."clinic_device_schedule_overrides" DROP CONSTRAINT IF EXISTS "clinic_device_schedule_overrides_pkey" CASCADE;
ALTER TABLE core."clinic_device_schedule_overrides" ADD CONSTRAINT "clinic_device_schedule_overrides_pkey" PRIMARY KEY ("override_id");

-- One row per (clinic, device). Quantity is unit_count, not row count — two
-- rows for the same pair would make "how many units" ambiguous and silently
-- halve or double capacity depending on which the code reads.
ALTER TABLE core."clinic_devices" DROP CONSTRAINT IF EXISTS "uq_clinic_devices";
ALTER TABLE core."clinic_devices" ADD CONSTRAINT "uq_clinic_devices" UNIQUE ("clinic_id", "device_id");

-- One override per device per date.
ALTER TABLE core."clinic_device_schedule_overrides" DROP CONSTRAINT IF EXISTS "uq_cdso_device_date";
ALTER TABLE core."clinic_device_schedule_overrides" ADD CONSTRAINT "uq_cdso_device_date" UNIQUE ("clinic_device_id", "override_date");

-- Two ACTIVE schedule rows for the same device, day and start time are a
-- duplicate, not two windows. Partial so superseded rows (is_active = false)
-- stay for the audit trail without colliding.
DROP INDEX IF EXISTS core.uq_cds_active_window;
CREATE UNIQUE INDEX uq_cds_active_window
    ON core."clinic_device_schedules" ("clinic_device_id", "day_of_week", "start_time")
    WHERE "is_active";


-- ---------------------------------------------------------------------------
-- 5. Foreign keys — all ON DELETE RESTRICT
-- ---------------------------------------------------------------------------

ALTER TABLE core."clinic_devices" DROP CONSTRAINT IF EXISTS "fk_clinic_devices_clinic_id";
ALTER TABLE core."clinic_devices"
    ADD CONSTRAINT "fk_clinic_devices_clinic_id"
    FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;

ALTER TABLE core."clinic_devices" DROP CONSTRAINT IF EXISTS "fk_clinic_devices_device_id";
ALTER TABLE core."clinic_devices"
    ADD CONSTRAINT "fk_clinic_devices_device_id"
    FOREIGN KEY ("device_id") REFERENCES reference."neuromod_devices" ("device_id") ON DELETE RESTRICT;

ALTER TABLE core."clinic_devices" DROP CONSTRAINT IF EXISTS "fk_clinic_devices_created_by";
ALTER TABLE core."clinic_devices"
    ADD CONSTRAINT "fk_clinic_devices_created_by"
    FOREIGN KEY ("created_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

ALTER TABLE core."clinic_device_schedules" DROP CONSTRAINT IF EXISTS "fk_cds_clinic_device_id";
ALTER TABLE core."clinic_device_schedules"
    ADD CONSTRAINT "fk_cds_clinic_device_id"
    FOREIGN KEY ("clinic_device_id") REFERENCES core."clinic_devices" ("clinic_device_id") ON DELETE RESTRICT;

ALTER TABLE core."clinic_device_schedules" DROP CONSTRAINT IF EXISTS "fk_cds_created_by";
ALTER TABLE core."clinic_device_schedules"
    ADD CONSTRAINT "fk_cds_created_by"
    FOREIGN KEY ("created_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

ALTER TABLE core."clinic_device_schedule_overrides" DROP CONSTRAINT IF EXISTS "fk_cdso_clinic_device_id";
ALTER TABLE core."clinic_device_schedule_overrides"
    ADD CONSTRAINT "fk_cdso_clinic_device_id"
    FOREIGN KEY ("clinic_device_id") REFERENCES core."clinic_devices" ("clinic_device_id") ON DELETE RESTRICT;

ALTER TABLE core."clinic_device_schedule_overrides" DROP CONSTRAINT IF EXISTS "fk_cdso_created_by";
ALTER TABLE core."clinic_device_schedule_overrides"
    ADD CONSTRAINT "fk_cdso_created_by"
    FOREIGN KEY ("created_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;


-- ---------------------------------------------------------------------------
-- 6. Indexes
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_clinic_devices_clinic ON core."clinic_devices" USING btree ("clinic_id") WHERE "is_active";
CREATE INDEX IF NOT EXISTS idx_clinic_devices_device ON core."clinic_devices" USING btree ("device_id");

-- The availability query: given a device and a weekday, which windows apply.
CREATE INDEX IF NOT EXISTS idx_cds_device_day
    ON core."clinic_device_schedules" USING btree ("clinic_device_id", "day_of_week") WHERE "is_active";

CREATE INDEX IF NOT EXISTS idx_cdso_device_date
    ON core."clinic_device_schedule_overrides" USING btree ("clinic_device_id", "override_date");

-- The capacity count itself. Every slot-availability check runs this shape:
-- device sessions at a clinic on a date that currently hold a slot. Partial on
-- the statuses that occupy capacity — 'planned' has no time and cancelled rows
-- have released theirs, so neither belongs in the count.
CREATE INDEX IF NOT EXISTS idx_appointments_device_capacity
    ON core."appointments" USING btree ("clinic_id", "appointment_date", "protocol_id")
    WHERE "appointment_type" = 'device_session'
      AND "status" IN ('selected', 'paid', 'checked_in', 'in_progress');


-- ---------------------------------------------------------------------------
-- 7. A device schedule may only be set for a device the clinic holds
-- ---------------------------------------------------------------------------
-- clinic_device_id already forces that structurally. What it cannot force is
-- that an OVERRIDE's unit_count stays within the clinic's actual unit count —
-- an override claiming 5 units at a 3-unit clinic would let the booking code
-- overbook by two every day it applies. A CHECK cannot read clinic_devices, so
-- this is a trigger.

CREATE OR REPLACE FUNCTION core.fn_check_device_override_units()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_units INTEGER;
BEGIN
    IF NEW.unit_count IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT unit_count INTO v_units
    FROM core.clinic_devices WHERE clinic_device_id = NEW.clinic_device_id;

    IF NEW.unit_count > v_units THEN
        RAISE EXCEPTION 'Override claims % units but clinic device % has only %',
            NEW.unit_count, NEW.clinic_device_id, v_units;
    END IF;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_check_device_override_units ON core."clinic_device_schedule_overrides";
CREATE TRIGGER trg_check_device_override_units
    BEFORE INSERT OR UPDATE ON core."clinic_device_schedule_overrides"
    FOR EACH ROW EXECUTE FUNCTION core.fn_check_device_override_units();


-- ---------------------------------------------------------------------------
-- 8. updated_at
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS trg_updated_at_clinic_devices ON core."clinic_devices";
CREATE TRIGGER trg_updated_at_clinic_devices
    BEFORE UPDATE ON core."clinic_devices"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();

DROP TRIGGER IF EXISTS trg_updated_at_clinic_device_schedules ON core."clinic_device_schedules";
CREATE TRIGGER trg_updated_at_clinic_device_schedules
    BEFORE UPDATE ON core."clinic_device_schedules"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();


-- ---------------------------------------------------------------------------
-- 9. Audit
-- ---------------------------------------------------------------------------
-- Capacity changes are worth auditing: "why could this patient not book on the
-- 14th" is answered by who changed what, and when.

DROP TRIGGER IF EXISTS trg_audit_clinic_devices ON core."clinic_devices";
CREATE TRIGGER trg_audit_clinic_devices
    AFTER INSERT OR DELETE OR UPDATE ON core."clinic_devices"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('clinic_device_id');

DROP TRIGGER IF EXISTS trg_audit_clinic_device_schedules ON core."clinic_device_schedules";
CREATE TRIGGER trg_audit_clinic_device_schedules
    AFTER INSERT OR DELETE OR UPDATE ON core."clinic_device_schedules"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('schedule_id');

DROP TRIGGER IF EXISTS trg_audit_clinic_device_schedule_overrides ON core."clinic_device_schedule_overrides";
CREATE TRIGGER trg_audit_clinic_device_schedule_overrides
    AFTER INSERT OR DELETE OR UPDATE ON core."clinic_device_schedule_overrides"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('override_id');


-- ---------------------------------------------------------------------------
-- 10. RLS
-- ---------------------------------------------------------------------------
-- Read: any authenticated user — the patient booking screen must see which
-- slots exist. Write: clinic_admin and above. Same split 30 used for
-- reference.billable_items, and for the same reason: a booking-handler bug must
-- not be able to reschedule a clinic's hardware.
--
-- Staff scoping goes through clinic_staff_assignments, not rls_clinic_id(),
-- matching what 32's policies do — see 33's open item D: rls_clinic_id()
-- returns NULL for every caller because the GUC is never set.

ALTER TABLE core."clinic_devices"                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."clinic_devices"                   FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."clinic_device_schedules"          ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."clinic_device_schedules"          FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."clinic_device_schedule_overrides" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."clinic_device_schedule_overrides" FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_clinic_devices_select" ON core."clinic_devices";
CREATE POLICY "rls_clinic_devices_select" ON core."clinic_devices" FOR SELECT TO public
    USING (true);

DROP POLICY IF EXISTS "rls_clinic_devices_insert" ON core."clinic_devices";
CREATE POLICY "rls_clinic_devices_insert" ON core."clinic_devices" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (rls_user_role() = 'clinic_admin'::text
            AND clinic_id IN (SELECT s.clinic_id FROM clinic_staff_assignments s
                              WHERE s.profile_id = rls_user_id() AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_clinic_devices_update" ON core."clinic_devices";
CREATE POLICY "rls_clinic_devices_update" ON core."clinic_devices" FOR UPDATE TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (rls_user_role() = 'clinic_admin'::text
            AND clinic_id IN (SELECT s.clinic_id FROM clinic_staff_assignments s
                              WHERE s.profile_id = rls_user_id() AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_cds_select" ON core."clinic_device_schedules";
CREATE POLICY "rls_cds_select" ON core."clinic_device_schedules" FOR SELECT TO public
    USING (true);

DROP POLICY IF EXISTS "rls_cds_insert" ON core."clinic_device_schedules";
CREATE POLICY "rls_cds_insert" ON core."clinic_device_schedules" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (rls_user_role() = 'clinic_admin'::text
            AND clinic_device_id IN (
                SELECT cd.clinic_device_id FROM clinic_devices cd
                JOIN clinic_staff_assignments s ON s.clinic_id = cd.clinic_id
                WHERE s.profile_id = rls_user_id() AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_cds_update" ON core."clinic_device_schedules";
CREATE POLICY "rls_cds_update" ON core."clinic_device_schedules" FOR UPDATE TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (rls_user_role() = 'clinic_admin'::text
            AND clinic_device_id IN (
                SELECT cd.clinic_device_id FROM clinic_devices cd
                JOIN clinic_staff_assignments s ON s.clinic_id = cd.clinic_id
                WHERE s.profile_id = rls_user_id() AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_cdso_select" ON core."clinic_device_schedule_overrides";
CREATE POLICY "rls_cdso_select" ON core."clinic_device_schedule_overrides" FOR SELECT TO public
    USING (true);

DROP POLICY IF EXISTS "rls_cdso_insert" ON core."clinic_device_schedule_overrides";
CREATE POLICY "rls_cdso_insert" ON core."clinic_device_schedule_overrides" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'receptionist'::text])
            AND clinic_device_id IN (
                SELECT cd.clinic_device_id FROM clinic_devices cd
                JOIN clinic_staff_assignments s ON s.clinic_id = cd.clinic_id
                WHERE s.profile_id = rls_user_id() AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_cdso_update" ON core."clinic_device_schedule_overrides";
CREATE POLICY "rls_cdso_update" ON core."clinic_device_schedule_overrides" FOR UPDATE TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'receptionist'::text])
            AND clinic_device_id IN (
                SELECT cd.clinic_device_id FROM clinic_devices cd
                JOIN clinic_staff_assignments s ON s.clinic_id = cd.clinic_id
                WHERE s.profile_id = rls_user_id() AND s.is_active))
    );


-- ---------------------------------------------------------------------------
-- 11. Grants
-- ---------------------------------------------------------------------------
-- 18_grants.sql's GRANT ... ON ALL TABLES ran before these existed, and ALTER
-- DEFAULT PRIVILEGES only covers tables created by the role that set it.

GRANT SELECT, INSERT, UPDATE ON
    core."clinic_devices", core."clinic_device_schedules", core."clinic_device_schedule_overrides"
    TO anava_app;

-- Schedule history explains why a past booking was possible. Deletion belongs
-- to the purge worker, as everywhere else.
REVOKE DELETE ON
    core."clinic_devices", core."clinic_device_schedules", core."clinic_device_schedule_overrides"
    FROM anava_app;

GRANT SELECT ON
    core."clinic_devices", core."clinic_device_schedules", core."clinic_device_schedule_overrides"
    TO anava_readonly;


COMMIT;


-- ###########################################################################
-- HOW CAPACITY IS ENFORCED — application contract, not DDL
-- ###########################################################################
--
-- The database cannot express "at most N overlapping rows". The booking path
-- must therefore count under a lock, inside the same transaction that inserts
-- the appointment:
--
--   BEGIN;
--
--   -- 1. Lock the capacity row. Every booking for this (clinic, device)
--   --    serialises here, so two patients cannot both read "2 of 3 taken".
--   SELECT unit_count INTO n
--   FROM core.clinic_devices
--   WHERE clinic_id = :clinic AND device_id = :device AND is_active
--   FOR UPDATE;
--
--   -- 2. Apply the override for this date, if any.
--   --    is_available = false  -> closed, reject.
--   --    unit_count IS NOT NULL -> use it instead of n.
--
--   -- 3. Count what already occupies the window.
--   SELECT count(*) INTO taken
--   FROM core.appointments
--   WHERE clinic_id = :clinic
--     AND appointment_date = :date
--     AND appointment_type = 'device_session'
--     AND status IN ('selected','paid','checked_in','in_progress')
--     AND tsrange(appointment_date + start_time, appointment_date + end_time)
--         && tsrange(:date + :start, :date + :end);
--
--   IF taken >= n THEN RAISE 'no capacity'; END IF;
--
--   -- 4. Insert, still holding the lock.
--   COMMIT;
--
-- The FOR UPDATE in step 1 is what makes this correct at READ COMMITTED.
-- Without it two concurrent bookings both read taken = 2 against n = 3 and both
-- insert, giving 4 sessions on 3 machines. Counting without locking is the
-- classic version of this bug and it only shows up under load.
--
-- Note the asymmetry with appointments: a doctor's double-booking is blocked
-- declaratively by excl_doctor_overlap (31), because that rule is N = 1 and
-- EXCLUDE can express it. Device capacity is N > 1 and cannot be. Two different
-- mechanisms because the two rules are genuinely different shapes — the same
-- point 30 makes about excl_doctor_overlap versus the partitioned session
-- slot-grid.
--
--
-- ###########################################################################
-- STILL OPEN
-- ###########################################################################
--
--   1. Nothing yet ASSIGNS a time to a generated device_session row. This file
--      supplies the slot grid; walking it and offering slots to the patient is
--      application work in the booking path.
--
--   2. appointments.ca_id is set late (decision D15), so excl_ca_overlap does
--      not protect a device session at booking time — only once an assistant
--      claims it. A clinic with 3 machines and 1 assistant is limited by the
--      assistant, and nothing here models assistant capacity. Separate concern
--      from device capacity; worth stating so it is not assumed covered.
--
--   3. clinic_devices.unit_count duplicates what a per-unit hardware table
--      would derive (32's OPEN ITEM 11). If that table is ever built, this
--      column should become a view over it rather than a second source of
--      truth that can disagree.
--
--   4. Not executed anywhere. Restore a snapshot, apply 30-35 in order, then
--      exercise the capacity path under concurrency before it goes near
--      Anava_App_v1 — the FOR UPDATE contract above is exactly the kind of
--      thing that passes a single-threaded test and fails in production.
-- ###########################################################################
