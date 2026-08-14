-- 36_clinic_device_schedules.sql
--
-- Hand-written net-new DDL (like 30-35).
--
-- SCOPE: the pool of bookable time a DEVICE SESSION books against. Nothing here
-- touches core.appointments or any protocol table.
--
-- APPLY ORDER: after 32 (needs nothing from it, but the concept only makes sense
-- once device sessions exist). Independent of 33-35.
--
-- NOT YET EXECUTED ANYWHERE.
--
--
-- ###########################################################################
-- WHY THIS EXISTS
-- ###########################################################################
--
-- Three of the four appointment types book against a DOCTOR's calendar:
-- 'initial', 'follow_up' and 'protocol_followup' are all consultations, and
-- doctor_weekly_schedules / doctor_schedule_overrides already serve them.
--
-- 'device_session' is different. It is administered by a clinical assistant on
-- a device, and consumes no doctor's time at all — which is exactly why
-- 30_appointments_spine.sql made appointments.doctor_id nullable and why the
-- protocol module writes NULL there. A device session that booked a doctor's
-- calendar would be refused whenever the supervising doctor happened to be busy,
-- for a visit that doctor is not attending.
--
-- So device sessions need their own pool, and it is shaped differently:
--
--   DOCTOR SCHEDULE                  CLINIC DEVICE SCHEDULE
--   ---------------                  ----------------------
--   owned by each doctor             owned by the CLINIC ADMIN
--   scarce thing: that doctor's time scarce thing: the clinic's devices
--   EXCLUSIVE  - one visit at a time CAPACITY - N visits at a time
--   enforced by a GIST exclusion     enforced by a counted check under a lock
--
--
-- ###########################################################################
-- CAPACITY IS NOT A CONSTRAINT, AND CANNOT BE
-- ###########################################################################
--
-- A GIST exclusion answers a yes/no question: "does this overlap anything?" It
-- cannot express "are there fewer than three of these?" Applied to device
-- sessions it would cap a clinic with three devices at ONE booking per slot.
--
-- PostgreSQL has no constraint form meaning "at most N rows matching a
-- predicate". So capacity is enforced in application code:
--
--     BEGIN
--       SELECT capacity FROM core.clinic_device_schedules
--        WHERE clinic_id = :c AND day_of_week = :d
--          FOR UPDATE;                      -- serialises concurrent bookings
--       SELECT count(*) FROM core.appointments
--        WHERE clinic_id = :c AND appointment_type = 'device_session'
--          AND appointment_date = :date AND start_time = :start
--          AND status IN ('selected','paid','checked_in','in_progress');
--       -- count >= capacity -> reject; else INSERT/UPDATE
--     COMMIT
--
-- The FOR UPDATE is the whole correctness argument. Without it two patients
-- booking simultaneously both count 2-of-3, both proceed, and the clinic is
-- overbooked with no error raised anywhere. It must not be removed as an
-- optimisation.
--
-- This is the ONE rule in the appointment design the database does not enforce
-- by itself. Stated here plainly so nobody assumes otherwise.
--
--
-- ###########################################################################
-- WHAT CAPACITY MEANS
-- ###########################################################################
--
-- A number the CLINIC ADMIN types in, exactly as a doctor types their working
-- hours. They set it having judged both limits together: how many devices the
-- clinic has, and how many clinical assistants are on shift to run them. Three
-- devices but two assistants means capacity 2.
--
-- The system deliberately does NOT compute it. Deriving it would need a device
-- inventory and a staff rota, neither of which exists, to answer a question one
-- person already knows. The override table carries its own optional capacity for
-- the days that number differs — a device out for maintenance, an assistant on
-- leave.
--
-- This also explains why no per-assistant availability check exists at booking:
-- capacity already accounts for staffing, and no individual assistant is
-- reserved in advance. An assistant is bound late — ca_id stays NULL until one
-- STARTS the session.


BEGIN;


-- ###########################################################################
-- 1  LAYER 1 — Tables
-- ###########################################################################
-- Shapes deliberately mirror core.doctor_weekly_schedules and
-- core.doctor_schedule_overrides, so the clinic-admin screen is the doctor
-- schedule screen the admin already understands, plus one field. The
-- slot-building logic in AvailabilityService is reused rather than rewritten —
-- it takes a schedule and emits slots, and does not care whose schedule it was.

CREATE TABLE IF NOT EXISTS core."clinic_device_schedules" (
    "schedule_id"           UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_id"             UUID NOT NULL,
    "day_of_week"           SMALLINT NOT NULL,
    "start_time"            TIME NOT NULL,
    "end_time"              TIME NOT NULL,
    "slot_duration_minutes" INTEGER NOT NULL DEFAULT 30,
    "break_start"           TIME,
    "break_end"             TIME,
    "capacity"              INTEGER NOT NULL,
    "is_active"             BOOLEAN NOT NULL DEFAULT true,
    "effective_from"        DATE,
    "effective_until"       DATE,
    "created_by"            UUID,
    "created_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"            TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE core."clinic_device_schedules" IS 'Clinic-admin-owned weekly template for when device sessions can run and how many at once. The pool a device_session books against — device sessions never book a doctor calendar. Retention: operational config, not patient data.';
COMMENT ON COLUMN core."clinic_device_schedules"."day_of_week" IS 'Postgres EXTRACT(DOW) convention: 0=Sunday .. 6=Saturday. Matches doctor_weekly_schedules so the same slot-builder serves both.';
COMMENT ON COLUMN core."clinic_device_schedules"."capacity" IS 'How many device sessions may run simultaneously in one slot. Set by the clinic admin judging devices AND assistants on shift together; never computed by the system. Enforced by a counted check under SELECT ... FOR UPDATE, not by a constraint — PostgreSQL cannot express "at most N rows matching a predicate".';
COMMENT ON COLUMN core."clinic_device_schedules"."effective_from" IS 'Optional validity window, mirroring doctor_weekly_schedules. NULL means always in effect.';

CREATE TABLE IF NOT EXISTS core."clinic_device_schedule_overrides" (
    "override_id"    UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_id"      UUID NOT NULL,
    "override_date"  DATE NOT NULL,
    "is_available"   BOOLEAN NOT NULL DEFAULT false,
    "start_time"     TIME,
    "end_time"       TIME,
    "capacity"       INTEGER,
    "reason"         TEXT,
    "created_by"     UUID,
    "created_at"     TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE core."clinic_device_schedule_overrides" IS 'Single-day exceptions to the weekly device template — a public holiday, a service visit, a day with fewer assistants. Mirrors doctor_schedule_overrides.';
COMMENT ON COLUMN core."clinic_device_schedule_overrides"."is_available" IS 'false = no device sessions at all that day (the common case: closure). true = open, but with the hours and/or capacity below replacing the weekly template.';
COMMENT ON COLUMN core."clinic_device_schedule_overrides"."capacity" IS 'Optional per-day capacity. NULL means inherit the weekly template. Set it when a device is out for maintenance or an assistant is on leave.';


-- ###########################################################################
-- 2  LAYER 2 — Primary keys
-- ###########################################################################

ALTER TABLE core."clinic_device_schedules" DROP CONSTRAINT IF EXISTS "clinic_device_schedules_pkey" CASCADE;
ALTER TABLE core."clinic_device_schedules" ADD CONSTRAINT "clinic_device_schedules_pkey" PRIMARY KEY ("schedule_id");

ALTER TABLE core."clinic_device_schedule_overrides" DROP CONSTRAINT IF EXISTS "clinic_device_schedule_overrides_pkey" CASCADE;
ALTER TABLE core."clinic_device_schedule_overrides" ADD CONSTRAINT "clinic_device_schedule_overrides_pkey" PRIMARY KEY ("override_id");


-- ###########################################################################
-- 3  LAYER 2 — Unique constraints
-- ###########################################################################
-- One weekly rule per clinic per weekday, and one override per clinic per date.
-- Two Tuesday templates for one clinic is a data-entry duplicate, not a variant,
-- and the capacity lookup takes FOR UPDATE on exactly one row — it must be able
-- to assume there is exactly one.

ALTER TABLE core."clinic_device_schedules" DROP CONSTRAINT IF EXISTS "clinic_device_schedules_clinic_id_day_of_week_key";
ALTER TABLE core."clinic_device_schedules"
    ADD CONSTRAINT "clinic_device_schedules_clinic_id_day_of_week_key"
    UNIQUE ("clinic_id", "day_of_week");

ALTER TABLE core."clinic_device_schedule_overrides" DROP CONSTRAINT IF EXISTS "clinic_device_schedule_overrides_clinic_id_override_date_key";
ALTER TABLE core."clinic_device_schedule_overrides"
    ADD CONSTRAINT "clinic_device_schedule_overrides_clinic_id_override_date_key"
    UNIQUE ("clinic_id", "override_date");


-- ###########################################################################
-- 4  LAYER 2 — CHECK constraints
-- ###########################################################################

-- Capacity 0 would mean "open but nothing can be booked", which is what
-- is_active = false and an override already say more clearly.
ALTER TABLE core."clinic_device_schedules" DROP CONSTRAINT IF EXISTS "chk_cds_capacity_positive";
ALTER TABLE core."clinic_device_schedules"
    ADD CONSTRAINT "chk_cds_capacity_positive" CHECK ("capacity" > 0);

ALTER TABLE core."clinic_device_schedules" DROP CONSTRAINT IF EXISTS "chk_cds_day_of_week";
ALTER TABLE core."clinic_device_schedules"
    ADD CONSTRAINT "chk_cds_day_of_week" CHECK ("day_of_week" BETWEEN 0 AND 6);

ALTER TABLE core."clinic_device_schedules" DROP CONSTRAINT IF EXISTS "chk_cds_time_order";
ALTER TABLE core."clinic_device_schedules"
    ADD CONSTRAINT "chk_cds_time_order" CHECK ("end_time" > "start_time");

ALTER TABLE core."clinic_device_schedules" DROP CONSTRAINT IF EXISTS "chk_cds_slot_duration";
ALTER TABLE core."clinic_device_schedules"
    ADD CONSTRAINT "chk_cds_slot_duration" CHECK ("slot_duration_minutes" > 0);

-- A break is meaningful only as a pair, and must sit inside the working window.
ALTER TABLE core."clinic_device_schedules" DROP CONSTRAINT IF EXISTS "chk_cds_break_pair";
ALTER TABLE core."clinic_device_schedules"
    ADD CONSTRAINT "chk_cds_break_pair"
    CHECK (("break_start" IS NULL) = ("break_end" IS NULL));

ALTER TABLE core."clinic_device_schedules" DROP CONSTRAINT IF EXISTS "chk_cds_break_window";
ALTER TABLE core."clinic_device_schedules"
    ADD CONSTRAINT "chk_cds_break_window"
    CHECK ("break_start" IS NULL
           OR ("break_end" > "break_start"
               AND "break_start" >= "start_time"
               AND "break_end"   <= "end_time"));

-- Overrides: an available override must say when; an unavailable one must not
-- pretend to. Same pairing logic as the spine's chk_appointments_time_pair.
ALTER TABLE core."clinic_device_schedule_overrides" DROP CONSTRAINT IF EXISTS "chk_cdso_time_pair";
ALTER TABLE core."clinic_device_schedule_overrides"
    ADD CONSTRAINT "chk_cdso_time_pair"
    CHECK (("start_time" IS NULL) = ("end_time" IS NULL));

ALTER TABLE core."clinic_device_schedule_overrides" DROP CONSTRAINT IF EXISTS "chk_cdso_time_order";
ALTER TABLE core."clinic_device_schedule_overrides"
    ADD CONSTRAINT "chk_cdso_time_order"
    CHECK ("start_time" IS NULL OR "end_time" > "start_time");

ALTER TABLE core."clinic_device_schedule_overrides" DROP CONSTRAINT IF EXISTS "chk_cdso_closed_has_no_hours";
ALTER TABLE core."clinic_device_schedule_overrides"
    ADD CONSTRAINT "chk_cdso_closed_has_no_hours"
    CHECK ("is_available" OR ("start_time" IS NULL AND "capacity" IS NULL));

ALTER TABLE core."clinic_device_schedule_overrides" DROP CONSTRAINT IF EXISTS "chk_cdso_capacity_positive";
ALTER TABLE core."clinic_device_schedule_overrides"
    ADD CONSTRAINT "chk_cdso_capacity_positive"
    CHECK ("capacity" IS NULL OR "capacity" > 0);


-- ###########################################################################
-- 5  LAYER 2 — Foreign keys
-- ###########################################################################
-- ON DELETE RESTRICT throughout, matching every other FK in this schema.

ALTER TABLE core."clinic_device_schedules" DROP CONSTRAINT IF EXISTS "fk_clinic_device_schedules_clinic_id";
ALTER TABLE core."clinic_device_schedules"
    ADD CONSTRAINT "fk_clinic_device_schedules_clinic_id"
    FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;

ALTER TABLE core."clinic_device_schedules" DROP CONSTRAINT IF EXISTS "fk_clinic_device_schedules_created_by";
ALTER TABLE core."clinic_device_schedules"
    ADD CONSTRAINT "fk_clinic_device_schedules_created_by"
    FOREIGN KEY ("created_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

ALTER TABLE core."clinic_device_schedule_overrides" DROP CONSTRAINT IF EXISTS "fk_clinic_device_schedule_overrides_clinic_id";
ALTER TABLE core."clinic_device_schedule_overrides"
    ADD CONSTRAINT "fk_clinic_device_schedule_overrides_clinic_id"
    FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;

ALTER TABLE core."clinic_device_schedule_overrides" DROP CONSTRAINT IF EXISTS "fk_clinic_device_schedule_overrides_created_by";
ALTER TABLE core."clinic_device_schedule_overrides"
    ADD CONSTRAINT "fk_clinic_device_schedule_overrides_created_by"
    FOREIGN KEY ("created_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;


-- ###########################################################################
-- 6  LAYER 2 — Indexes
-- ###########################################################################

-- The availability query: this clinic's active weekly rules.
CREATE INDEX IF NOT EXISTS idx_cds_clinic_active
    ON core."clinic_device_schedules" USING btree ("clinic_id") WHERE "is_active";

-- The override lookup for a date range.
CREATE INDEX IF NOT EXISTS idx_cdso_clinic_date
    ON core."clinic_device_schedule_overrides" USING btree ("clinic_id", "override_date");

-- The capacity count: device sessions already booked into one clinic slot.
-- Partial, because the count only ever considers slot-occupying statuses.
CREATE INDEX IF NOT EXISTS idx_appointments_device_capacity
    ON core."appointments" USING btree ("clinic_id", "appointment_date", "start_time")
    WHERE "appointment_type" = 'device_session'
      AND "status" IN ('selected', 'paid', 'checked_in', 'in_progress');


-- ###########################################################################
-- 7  LAYER 2 — Triggers
-- ###########################################################################

DROP TRIGGER IF EXISTS trg_updated_at_clinic_device_schedules ON core."clinic_device_schedules";
CREATE TRIGGER trg_updated_at_clinic_device_schedules
    BEFORE UPDATE ON core."clinic_device_schedules"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();

-- Audited: capacity governs how many patients a clinic can treat in a day, and
-- changing it retroactively affects what was bookable. Who changed it matters.
DROP TRIGGER IF EXISTS trg_audit_clinic_device_schedules ON core."clinic_device_schedules";
CREATE TRIGGER trg_audit_clinic_device_schedules
    AFTER INSERT OR DELETE OR UPDATE ON core."clinic_device_schedules"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('schedule_id');

DROP TRIGGER IF EXISTS trg_audit_clinic_device_schedule_overrides ON core."clinic_device_schedule_overrides";
CREATE TRIGGER trg_audit_clinic_device_schedule_overrides
    AFTER INSERT OR DELETE OR UPDATE ON core."clinic_device_schedule_overrides"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('override_id');


-- ###########################################################################
-- 8  LAYER 3 — RLS
-- ###########################################################################
-- ENABLE + FORCE with policies in the same file. Enabling without policies is
-- what silently locked tables out of the app twice before (NOTES.md, and 26).

ALTER TABLE core."clinic_device_schedules"          ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."clinic_device_schedules"          FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."clinic_device_schedule_overrides" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."clinic_device_schedule_overrides" FORCE  ROW LEVEL SECURITY;

-- SELECT is deliberately wide: a PATIENT must be able to read this to see which
-- device slots are open before claiming one. It reveals opening hours and a
-- capacity number, no patient data. Restricting it would break booking.
DROP POLICY IF EXISTS "rls_cds_select" ON core."clinic_device_schedules";
CREATE POLICY "rls_cds_select" ON core."clinic_device_schedules" FOR SELECT TO public
    USING (true);

-- Writes: clinic admin for their own clinic, regional admin for clinics in their
-- region, super_admin anywhere. Two paths to clinic scope for the same reason
-- 32's protocol policies carry two: rls_clinic_id() covers a staff member whose
-- context carries a clinic, clinic_staff_assignments covers one whose does not.
DROP POLICY IF EXISTS "rls_cds_insert" ON core."clinic_device_schedules";
CREATE POLICY "rls_cds_insert" ON core."clinic_device_schedules" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = 'super_admin'::text)
        OR (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'regional_admin'::text])
            AND (clinic_id = rls_clinic_id()
                 OR clinic_id IN (SELECT c.clinic_id FROM clinics c WHERE c.region_id = rls_region_id())
                 OR clinic_id IN (SELECT s.clinic_id FROM clinic_staff_assignments s
                                  WHERE s.profile_id = rls_user_id() AND s.is_active)))
    );

DROP POLICY IF EXISTS "rls_cds_update" ON core."clinic_device_schedules";
CREATE POLICY "rls_cds_update" ON core."clinic_device_schedules" FOR UPDATE TO public
    USING (
        (rls_user_role() = 'super_admin'::text)
        OR (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'regional_admin'::text])
            AND (clinic_id = rls_clinic_id()
                 OR clinic_id IN (SELECT c.clinic_id FROM clinics c WHERE c.region_id = rls_region_id())
                 OR clinic_id IN (SELECT s.clinic_id FROM clinic_staff_assignments s
                                  WHERE s.profile_id = rls_user_id() AND s.is_active)))
    );

-- DELETE is granted here, unlike most tables: a weekly template is operational
-- config, not a clinical record, and the admin screen replaces the whole week
-- atomically (delete-then-insert), matching how a doctor redraws their own.
DROP POLICY IF EXISTS "rls_cds_delete" ON core."clinic_device_schedules";
CREATE POLICY "rls_cds_delete" ON core."clinic_device_schedules" FOR DELETE TO public
    USING (
        (rls_user_role() = 'super_admin'::text)
        OR (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'regional_admin'::text])
            AND (clinic_id = rls_clinic_id()
                 OR clinic_id IN (SELECT c.clinic_id FROM clinics c WHERE c.region_id = rls_region_id())
                 OR clinic_id IN (SELECT s.clinic_id FROM clinic_staff_assignments s
                                  WHERE s.profile_id = rls_user_id() AND s.is_active)))
    );

DROP POLICY IF EXISTS "rls_cdso_select" ON core."clinic_device_schedule_overrides";
CREATE POLICY "rls_cdso_select" ON core."clinic_device_schedule_overrides" FOR SELECT TO public
    USING (true);

DROP POLICY IF EXISTS "rls_cdso_insert" ON core."clinic_device_schedule_overrides";
CREATE POLICY "rls_cdso_insert" ON core."clinic_device_schedule_overrides" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = 'super_admin'::text)
        OR (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'regional_admin'::text])
            AND (clinic_id = rls_clinic_id()
                 OR clinic_id IN (SELECT c.clinic_id FROM clinics c WHERE c.region_id = rls_region_id())
                 OR clinic_id IN (SELECT s.clinic_id FROM clinic_staff_assignments s
                                  WHERE s.profile_id = rls_user_id() AND s.is_active)))
    );

DROP POLICY IF EXISTS "rls_cdso_update" ON core."clinic_device_schedule_overrides";
CREATE POLICY "rls_cdso_update" ON core."clinic_device_schedule_overrides" FOR UPDATE TO public
    USING (
        (rls_user_role() = 'super_admin'::text)
        OR (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'regional_admin'::text])
            AND (clinic_id = rls_clinic_id()
                 OR clinic_id IN (SELECT c.clinic_id FROM clinics c WHERE c.region_id = rls_region_id())
                 OR clinic_id IN (SELECT s.clinic_id FROM clinic_staff_assignments s
                                  WHERE s.profile_id = rls_user_id() AND s.is_active)))
    );

DROP POLICY IF EXISTS "rls_cdso_delete" ON core."clinic_device_schedule_overrides";
CREATE POLICY "rls_cdso_delete" ON core."clinic_device_schedule_overrides" FOR DELETE TO public
    USING (
        (rls_user_role() = 'super_admin'::text)
        OR (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'regional_admin'::text])
            AND (clinic_id = rls_clinic_id()
                 OR clinic_id IN (SELECT c.clinic_id FROM clinics c WHERE c.region_id = rls_region_id())
                 OR clinic_id IN (SELECT s.clinic_id FROM clinic_staff_assignments s
                                  WHERE s.profile_id = rls_user_id() AND s.is_active)))
    );


-- ###########################################################################
-- 9  LAYER 3 — Grants
-- ###########################################################################
-- 18_grants.sql's GRANT ... ON ALL TABLES ran before these existed, and
-- ALTER DEFAULT PRIVILEGES only covers tables created by the role that set it.
-- Granting explicitly so neither becomes the next silent lockout.
--
-- DELETE is granted here (unlike the protocol tables, where it is revoked):
-- these hold operational config, not Bucket-2 clinical data, and the weekly
-- template is edited by atomic replace.

GRANT SELECT, INSERT, UPDATE, DELETE ON
    core."clinic_device_schedules", core."clinic_device_schedule_overrides"
    TO anava_app;

GRANT SELECT ON
    core."clinic_device_schedules", core."clinic_device_schedule_overrides"
    TO anava_readonly;


COMMIT;


-- ###########################################################################
-- NO SEED DATA
-- ###########################################################################
-- A clinic's device hours and capacity are the clinic admin's to set. An empty
-- schedule fails loudly and visibly the first time a patient tries to claim a
-- device slot; a guessed one silently books patients into hours the clinic is
-- shut, or beyond the number of devices it owns.


-- ###########################################################################
-- OPEN ITEMS
-- ###########################################################################
--
--  1. Capacity is enforced in application code, not by a constraint (see the
--     header). The SELECT ... FOR UPDATE in DeviceCapacityService is the whole
--     correctness argument and must not be removed.
--
--  2. No per-assistant availability check exists, by design — capacity already
--     accounts for staffing and no individual assistant is reserved in advance
--     (ca_id binds late, when one starts the session). excl_ca_overlap remains
--     as a second line: once an assistant IS named on a session, it stops that
--     same assistant being claimed onto two overlapping ones.
--
--  3. effective_from / effective_until are carried for parity with
--     doctor_weekly_schedules but are not yet read by the slot builder. Wire
--     them in the same place both schedules are read, or drop them — a column
--     nothing consults is worse than no column.
--
--  4. No cross-clinic view. A patient claims device slots at the clinic on
--     their appointment row; there is no "find me any clinic with a free
--     device" search. Add only if the clinic actually needs it.
