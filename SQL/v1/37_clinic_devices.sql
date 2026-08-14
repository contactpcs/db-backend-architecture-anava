-- 37_clinic_devices.sql
--
-- Hand-written net-new DDL (like 30-36).
--
-- SCOPE: which devices each clinic actually owns. Creates one new table and one
-- guard trigger on core.treatment_protocols. Does not change any protocol table
-- definition, column, or existing logic — the trigger only refuses a write that
-- was already wrong.
--
-- APPLY ORDER: after 32 (needs reference.neuromod_devices). Independent of 33-36.
--
-- NOT YET EXECUTED ANYWHERE.
--
--
-- ###########################################################################
-- THE GAP THIS CLOSES
-- ###########################################################################
--
-- Before this file, nothing recorded which devices a clinic has:
--
--   reference.device_companies    who MAKES devices
--   reference.neuromod_devices    which device TYPES can be prescribed
--   core.device_assignments       which PATIENT took a device home (store module)
--   core.clinic_device_schedules  HOW MANY sessions can run at once
--   -- missing --                 which devices this CLINIC owns
--
-- 32's OPEN ITEM 11 named the gap: "Device COMPANY is modelled; device UNIT is
-- not." The consequence was that a doctor at a clinic with only tDCS units could
-- prescribe an rTMS protocol, and nothing would notice until the patient turned
-- up for a session the clinic cannot run.
--
--
-- ###########################################################################
-- PER TYPE AND QUANTITY, NOT PER MACHINE
-- ###########################################################################
--
-- One row per (clinic, device type) carrying how many the clinic has — NOT one
-- row per serial-numbered machine.
--
-- That is enough for the job this table exists to do: filter the protocol
-- picker to what the clinic can actually run, and refuse anything else. It also
-- keeps the admin screen honest — pick a device, type a number — instead of
-- making someone enter three near-identical rows for three identical units.
--
-- What this deliberately does NOT model: serial numbers, service history,
-- calibration due dates, which treatment room a unit sits in. None of it is
-- needed to answer "can this clinic run this protocol", and a per-unit table is
-- a strictly larger thing that can be added later without changing this one —
-- core.device_units would carry a clinic_device_id and this table would become
-- its summary.
--
--
-- ###########################################################################
-- WHY quantity DOES NOT FEED capacity
-- ###########################################################################
--
-- It is tempting to derive clinic_device_schedules.capacity from the sum of
-- quantity here. Resist it.
--
-- Capacity is how many sessions can run AT ONCE, which is limited by devices
-- AND by clinical assistants on shift — three devices with two assistants is a
-- capacity of two. This table knows nothing about staffing, so a derived
-- capacity would silently overbook the clinic every time someone is on leave.
--
-- Capacity stays a number the clinic admin types in, judging both limits
-- together (36 header). These two tables answer different questions and are
-- deliberately not wired to each other.


BEGIN;


-- ###########################################################################
-- 1  LAYER 1 — Table
-- ###########################################################################

CREATE TABLE IF NOT EXISTS core."clinic_devices" (
    "clinic_device_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_id"        UUID NOT NULL,
    "device_id"        UUID NOT NULL,
    "quantity"         INTEGER NOT NULL DEFAULT 1,
    "is_active"        BOOLEAN NOT NULL DEFAULT true,
    "acquired_on"      DATE,
    "notes"            TEXT,
    "created_by"       UUID,
    "created_at"       TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"       TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE core."clinic_devices" IS 'Which device types each clinic owns, and how many. Gates the protocol device picker: a doctor may only prescribe on a device their clinic actually has. Per TYPE with a quantity, not per serial-numbered machine — see the file header for what that deliberately excludes.';
COMMENT ON COLUMN core."clinic_devices"."quantity" IS 'How many units of this device type the clinic owns. Used for display and for the availability test (quantity > 0). Deliberately NOT the source of clinic_device_schedules.capacity, which also depends on assistants on shift and stays admin-set.';
COMMENT ON COLUMN core."clinic_devices"."is_active" IS 'false = the clinic no longer runs this device (sold, withdrawn, out of service indefinitely). Existing protocols on it keep working — the trigger below only guards NEW prescriptions — but the picker stops offering it.';
COMMENT ON COLUMN core."clinic_devices"."acquired_on" IS 'Optional, informational. Not used by any rule.';


-- ###########################################################################
-- 2  LAYER 2 — Keys and constraints
-- ###########################################################################

ALTER TABLE core."clinic_devices" DROP CONSTRAINT IF EXISTS "clinic_devices_pkey" CASCADE;
ALTER TABLE core."clinic_devices" ADD CONSTRAINT "clinic_devices_pkey" PRIMARY KEY ("clinic_device_id");

-- One row per clinic per device type. Two rows for the same pair is a
-- data-entry duplicate, not two stocks — the quantity column is what says
-- "more than one". This also lets the availability test be a plain EXISTS.
ALTER TABLE core."clinic_devices" DROP CONSTRAINT IF EXISTS "clinic_devices_clinic_id_device_id_key";
ALTER TABLE core."clinic_devices"
    ADD CONSTRAINT "clinic_devices_clinic_id_device_id_key"
    UNIQUE ("clinic_id", "device_id");

-- Zero would mean "listed but unusable", which is what is_active = false
-- already says, more clearly. Removing the last unit is a deactivation or a
-- delete, not a quantity of nothing.
ALTER TABLE core."clinic_devices" DROP CONSTRAINT IF EXISTS "chk_clinic_devices_quantity_positive";
ALTER TABLE core."clinic_devices"
    ADD CONSTRAINT "chk_clinic_devices_quantity_positive" CHECK ("quantity" > 0);

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


-- ###########################################################################
-- 3  LAYER 2 — Indexes
-- ###########################################################################

-- The picker query: what can this clinic prescribe on?
CREATE INDEX IF NOT EXISTS idx_clinic_devices_clinic
    ON core."clinic_devices" USING btree ("clinic_id") WHERE "is_active";

-- The reverse: which clinics have this device? Used when a device is retired
-- from the catalogue and someone needs to know who is affected.
CREATE INDEX IF NOT EXISTS idx_clinic_devices_device
    ON core."clinic_devices" USING btree ("device_id");


-- ###########################################################################
-- 4  LAYER 2 — Triggers
-- ###########################################################################

DROP TRIGGER IF EXISTS trg_updated_at_clinic_devices ON core."clinic_devices";
CREATE TRIGGER trg_updated_at_clinic_devices
    BEFORE UPDATE ON core."clinic_devices"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();

-- Audited: which devices a clinic holds determines what can be prescribed
-- there, so a change to it changes what treatment was possible on a given date.
DROP TRIGGER IF EXISTS trg_audit_clinic_devices ON core."clinic_devices";
CREATE TRIGGER trg_audit_clinic_devices
    AFTER INSERT OR DELETE OR UPDATE ON core."clinic_devices"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('clinic_device_id');


-- ###########################################################################
-- 5  THE GUARD — a protocol may only name a device its clinic has
-- ###########################################################################
-- Filtering the picker is not enough on its own. A stale browser tab, a direct
-- API call, or a device removed between page load and submit would all still
-- produce a protocol the clinic cannot deliver. This refuses it at the source.
--
-- A trigger and not a CHECK, for the usual reason: a CHECK cannot read another
-- table, and this needs three of them. The clinic is not on the protocol — it is
-- reached through plan_id -> treatment_plans -> cycle_id -> treatment_cycles,
-- which is the same path the protocol module's own generator walks to resolve
-- clinic_id.
--
-- FIRES ONLY WHEN device_id OR plan_id CHANGES. That restriction matters: an
-- ordinary status update (activate, complete, cancel) must keep working long
-- after a clinic has retired the device, otherwise a decommissioned machine
-- would freeze every protocol that ever used it. Existing prescriptions are
-- never invalidated retroactively; only new ones are checked.

CREATE OR REPLACE FUNCTION core.fn_check_device_available_at_clinic()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_clinic_id UUID;
    v_available BOOLEAN;
BEGIN
    SELECT tc.clinic_id INTO v_clinic_id
    FROM core.treatment_plans tp
    JOIN core.treatment_cycles tc ON tc.cycle_id = tp.cycle_id
    WHERE tp.plan_id = NEW.plan_id;

    IF v_clinic_id IS NULL THEN
        RAISE EXCEPTION 'Cannot resolve a clinic for plan % — protocol cannot be validated', NEW.plan_id
            USING ERRCODE = '23514';
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM core.clinic_devices cd
        WHERE cd.clinic_id = v_clinic_id
          AND cd.device_id = NEW.device_id
          AND cd.is_active
          AND cd.quantity > 0
    ) INTO v_available;

    IF NOT v_available THEN
        -- 23514 (check_violation) so the application layer sees this as an
        -- integrity error and can map it to a clean message, rather than a
        -- generic internal error.
        RAISE EXCEPTION 'Device % is not available at clinic %', NEW.device_id, v_clinic_id
            USING ERRCODE = '23514',
                  HINT = 'Add the device to this clinic''s inventory, or prescribe one the clinic has.';
    END IF;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_check_device_available_at_clinic ON core."treatment_protocols";
CREATE TRIGGER trg_check_device_available_at_clinic
    BEFORE INSERT OR UPDATE OF "device_id", "plan_id" ON core."treatment_protocols"
    FOR EACH ROW EXECUTE FUNCTION core.fn_check_device_available_at_clinic();


-- ###########################################################################
-- 6  LAYER 3 — RLS
-- ###########################################################################
-- ENABLE + FORCE with policies in the same file (the lesson from 16/26).

ALTER TABLE core."clinic_devices" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."clinic_devices" FORCE  ROW LEVEL SECURITY;

-- SELECT is wide: the protocol picker runs as the prescribing DOCTOR, and the
-- trigger above runs as whoever is inserting the protocol. Both must be able to
-- read this table or the feature cannot work. It exposes an inventory count, no
-- patient data.
DROP POLICY IF EXISTS "rls_clinic_devices_select" ON core."clinic_devices";
CREATE POLICY "rls_clinic_devices_select" ON core."clinic_devices" FOR SELECT TO public
    USING (true);

-- Writes: clinic admin for their own clinic, regional admin for their region,
-- super_admin anywhere. Two paths to clinic scope for the same reason 32 and 36
-- carry two — rls_clinic_id() covers a staff member whose context carries a
-- clinic, clinic_staff_assignments covers one whose does not.
DROP POLICY IF EXISTS "rls_clinic_devices_insert" ON core."clinic_devices";
CREATE POLICY "rls_clinic_devices_insert" ON core."clinic_devices" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = 'super_admin'::text)
        OR (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'regional_admin'::text])
            AND (clinic_id = rls_clinic_id()
                 OR clinic_id IN (SELECT c.clinic_id FROM clinics c WHERE c.region_id = rls_region_id())
                 OR clinic_id IN (SELECT s.clinic_id FROM clinic_staff_assignments s
                                  WHERE s.profile_id = rls_user_id() AND s.is_active)))
    );

DROP POLICY IF EXISTS "rls_clinic_devices_update" ON core."clinic_devices";
CREATE POLICY "rls_clinic_devices_update" ON core."clinic_devices" FOR UPDATE TO public
    USING (
        (rls_user_role() = 'super_admin'::text)
        OR (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'regional_admin'::text])
            AND (clinic_id = rls_clinic_id()
                 OR clinic_id IN (SELECT c.clinic_id FROM clinics c WHERE c.region_id = rls_region_id())
                 OR clinic_id IN (SELECT s.clinic_id FROM clinic_staff_assignments s
                                  WHERE s.profile_id = rls_user_id() AND s.is_active)))
    );

-- DELETE is allowed: an inventory row is operational config, not a clinical
-- record. Removing a device the clinic never actually had is a correction, not
-- a falsification. Retiring one that HAS been prescribed on should be
-- is_active = false instead — the FK to neuromod_devices is RESTRICT, and past
-- protocols reference the device, not this row.
DROP POLICY IF EXISTS "rls_clinic_devices_delete" ON core."clinic_devices";
CREATE POLICY "rls_clinic_devices_delete" ON core."clinic_devices" FOR DELETE TO public
    USING (
        (rls_user_role() = 'super_admin'::text)
        OR (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'regional_admin'::text])
            AND (clinic_id = rls_clinic_id()
                 OR clinic_id IN (SELECT c.clinic_id FROM clinics c WHERE c.region_id = rls_region_id())
                 OR clinic_id IN (SELECT s.clinic_id FROM clinic_staff_assignments s
                                  WHERE s.profile_id = rls_user_id() AND s.is_active)))
    );


-- ###########################################################################
-- 7  LAYER 3 — Grants
-- ###########################################################################
-- Explicit, because 18_grants.sql ran long before this table existed.

GRANT SELECT, INSERT, UPDATE, DELETE ON core."clinic_devices" TO anava_app;
GRANT SELECT ON core."clinic_devices" TO anava_readonly;


COMMIT;


-- ###########################################################################
-- NO SEED DATA
-- ###########################################################################
-- What hardware a clinic owns is a fact about the real world that only the
-- clinic knows. An empty inventory fails loudly and immediately — the picker is
-- empty and the trigger refuses every protocol — which is the correct behaviour
-- for "nobody has told us what this clinic has yet". A guessed inventory would
-- let doctors prescribe on machines that are not in the building.


-- ###########################################################################
-- OPEN ITEMS
-- ###########################################################################
--
--  1. ORDER OF OPERATIONS. The guard trigger means clinic_devices must be
--     populated BEFORE any protocol can be created at that clinic. On a
--     database with existing protocols this file is still safe to apply — the
--     trigger fires only on INSERT or on an UPDATE of device_id/plan_id, so
--     existing rows are untouched — but the next new protocol will be refused
--     until the clinic's inventory is entered.
--
--  2. Per-unit tracking (serial number, service history, calibration due) is
--     still not modelled, deliberately. If it is ever needed, core.device_units
--     would carry a clinic_device_id and this table becomes its summary; the
--     picker and the trigger would not have to change.
--
--  3. quantity is not decremented when a device breaks mid-day. There is no
--     "2 of 3 working" state — that is what a per-day capacity override on
--     clinic_device_schedule_overrides is for, and it is the number that
--     actually governs bookings.
--
--  4. No cross-clinic transfer record. Moving a device from clinic A to B is
--     two edits, and nothing links them. core.stock_transfers exists for the
--     store's products and could be generalised if this is ever needed.
