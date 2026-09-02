-- 73_device_units.sql
--
-- APPLY ORDER: after 72. Needs 37 (core.clinic_devices) and 47 (core.protocol_plan,
-- renamed from treatment_protocols).
--
-- THE GAP THIS CLOSES
--
-- 37's OPEN ITEM 2 named this exact extension: "Per-unit tracking (serial
-- number, service history, calibration due) is still not modelled,
-- deliberately. If it is ever needed, core.device_units would carry a
-- clinic_device_id and this table becomes its summary." This file is that
-- table.
--
-- Today a serial number is only ever a free-text note typed by a clinical
-- assistant at session start (device_sessions.device_serial_number — see
-- 56's comment: "No backing catalogue column exists ... this is a
-- session-scoped note, not inventory data"). Nothing links that string back
-- to a specific physical unit or to the protocol it was prescribed against,
-- so the CA re-types it, error-prone, every single session.
--
-- SCOPE: one row per serial-numbered physical unit, under the (clinic,
-- device type) row that already tracks quantity. Does not touch
-- clinic_devices.quantity (still the source of truth for "how many", per
-- 37's own header — this table does not feed it back). Adds one nullable
-- device_unit_id column to core.protocol_plan (renamed from
-- treatment_protocols by 47) so a clinic admin can optionally pin a specific
-- unit at prescribing time, letting device_sessions auto-fetch its serial
-- instead of the CA typing one.

BEGIN;


-- ###########################################################################
-- 1  LAYER 1 — Table
-- ###########################################################################

CREATE TABLE IF NOT EXISTS core."device_units" (
    "device_unit_id"    UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_device_id"  UUID NOT NULL,
    "serial_number"     TEXT NOT NULL,
    "status"            TEXT NOT NULL DEFAULT 'active',
    "notes"             TEXT,
    "created_by"        UUID,
    "created_at"        TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"        TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE core."device_units" IS 'One row per serial-numbered physical unit of a clinic_devices row (device type owned by a clinic). Optional — a clinic can keep using clinic_devices.quantity alone with no rows here. Lets a clinic admin pin a specific unit on a protocol (treatment_protocols.device_unit_id) so device_sessions can auto-fetch its serial at session start instead of a CA typing it.';
COMMENT ON COLUMN core."device_units"."status" IS 'active = in service. retired = taken out of service (kept, never deleted — may still be referenced by past sessions/protocols).';


-- ###########################################################################
-- 2  LAYER 2 — Keys and constraints
-- ###########################################################################

ALTER TABLE core."device_units" DROP CONSTRAINT IF EXISTS "device_units_pkey" CASCADE;
ALTER TABLE core."device_units" ADD CONSTRAINT "device_units_pkey" PRIMARY KEY ("device_unit_id");

ALTER TABLE core."device_units" DROP CONSTRAINT IF EXISTS "fk_device_units_clinic_device_id";
ALTER TABLE core."device_units"
    ADD CONSTRAINT "fk_device_units_clinic_device_id"
    FOREIGN KEY ("clinic_device_id") REFERENCES core."clinic_devices" ("clinic_device_id") ON DELETE CASCADE;

ALTER TABLE core."device_units" DROP CONSTRAINT IF EXISTS "fk_device_units_created_by";
ALTER TABLE core."device_units"
    ADD CONSTRAINT "fk_device_units_created_by"
    FOREIGN KEY ("created_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

ALTER TABLE core."device_units" DROP CONSTRAINT IF EXISTS "chk_device_units_status";
ALTER TABLE core."device_units"
    ADD CONSTRAINT "chk_device_units_status" CHECK ("status" IN ('active', 'retired'));

-- A serial number is unique within the clinic's stock of that device type,
-- not globally — two clinics (or two device types) may coincidentally log
-- the same manufacturer serial via a typo, and that is a data-quality issue
-- for the clinic to fix, not a cross-tenant constraint this table should own.
ALTER TABLE core."device_units" DROP CONSTRAINT IF EXISTS "device_units_clinic_device_id_serial_number_key";
ALTER TABLE core."device_units"
    ADD CONSTRAINT "device_units_clinic_device_id_serial_number_key"
    UNIQUE ("clinic_device_id", "serial_number");


-- ###########################################################################
-- 3  LAYER 2 — Indexes
-- ###########################################################################

CREATE INDEX IF NOT EXISTS idx_device_units_clinic_device
    ON core."device_units" USING btree ("clinic_device_id") WHERE "status" = 'active';


-- ###########################################################################
-- 4  LAYER 2 — Triggers
-- ###########################################################################

DROP TRIGGER IF EXISTS trg_updated_at_device_units ON core."device_units";
CREATE TRIGGER trg_updated_at_device_units
    BEFORE UPDATE ON core."device_units"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();

DROP TRIGGER IF EXISTS trg_audit_device_units ON core."device_units";
CREATE TRIGGER trg_audit_device_units
    AFTER INSERT OR DELETE OR UPDATE ON core."device_units"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('device_unit_id');


-- ###########################################################################
-- 5  LAYER 3 — RLS
-- ###########################################################################

ALTER TABLE core."device_units" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."device_units" FORCE  ROW LEVEL SECURITY;

-- SELECT wide, same reasoning as clinic_devices — the protocol picker and
-- CA session-start screen both need to read this, and it exposes no PHI.
DROP POLICY IF EXISTS "rls_device_units_select" ON core."device_units";
CREATE POLICY "rls_device_units_select" ON core."device_units" FOR SELECT TO public
    USING (true);

DROP POLICY IF EXISTS "rls_device_units_insert" ON core."device_units";
CREATE POLICY "rls_device_units_insert" ON core."device_units" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = 'super_admin'::text)
        OR (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'regional_admin'::text])
            AND clinic_device_id IN (
                SELECT cd.clinic_device_id FROM clinic_devices cd
                WHERE cd.clinic_id = rls_clinic_id()
                   OR cd.clinic_id IN (SELECT c.clinic_id FROM clinics c WHERE c.region_id = rls_region_id())
                   OR cd.clinic_id IN (SELECT s.clinic_id FROM clinic_staff_assignments s
                                        WHERE s.profile_id = rls_user_id() AND s.is_active)
            ))
    );

DROP POLICY IF EXISTS "rls_device_units_update" ON core."device_units";
CREATE POLICY "rls_device_units_update" ON core."device_units" FOR UPDATE TO public
    USING (
        (rls_user_role() = 'super_admin'::text)
        OR (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'regional_admin'::text])
            AND clinic_device_id IN (
                SELECT cd.clinic_device_id FROM clinic_devices cd
                WHERE cd.clinic_id = rls_clinic_id()
                   OR cd.clinic_id IN (SELECT c.clinic_id FROM clinics c WHERE c.region_id = rls_region_id())
                   OR cd.clinic_id IN (SELECT s.clinic_id FROM clinic_staff_assignments s
                                        WHERE s.profile_id = rls_user_id() AND s.is_active)
            ))
    );

DROP POLICY IF EXISTS "rls_device_units_delete" ON core."device_units";
CREATE POLICY "rls_device_units_delete" ON core."device_units" FOR DELETE TO public
    USING (
        (rls_user_role() = 'super_admin'::text)
        OR (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'regional_admin'::text])
            AND clinic_device_id IN (
                SELECT cd.clinic_device_id FROM clinic_devices cd
                WHERE cd.clinic_id = rls_clinic_id()
                   OR cd.clinic_id IN (SELECT c.clinic_id FROM clinics c WHERE c.region_id = rls_region_id())
                   OR cd.clinic_id IN (SELECT s.clinic_id FROM clinic_staff_assignments s
                                        WHERE s.profile_id = rls_user_id() AND s.is_active)
            ))
    );


-- ###########################################################################
-- 6  LAYER 3 — Grants
-- ###########################################################################

GRANT SELECT, INSERT, UPDATE, DELETE ON core."device_units" TO anava_app;
GRANT SELECT ON core."device_units" TO anava_readonly;


-- ###########################################################################
-- 7  protocol_plan — optional pinned unit
-- ###########################################################################
-- core.treatment_protocols was renamed to core.protocol_plan by 47
-- (47_protocol_prescription_tables.sql) — this targets the current name.
--
-- Nullable: a protocol may still be prescribed against a device TYPE only
-- (existing behaviour, unchanged) or against a specific serialized unit.
-- No trigger extension needed on top of 37's fn_check_device_available_at_
-- clinic — that guard already validates device_id/clinic membership; this
-- column just narrows WHICH unit of that already-validated type.

ALTER TABLE core."protocol_plan"
    ADD COLUMN IF NOT EXISTS "device_unit_id" UUID;

ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "fk_protocol_plan_device_unit_id";
ALTER TABLE core."protocol_plan"
    ADD CONSTRAINT "fk_protocol_plan_device_unit_id"
    FOREIGN KEY ("device_unit_id") REFERENCES core."device_units" ("device_unit_id") ON DELETE SET NULL;

COMMENT ON COLUMN core."protocol_plan"."device_unit_id" IS 'Optional pinned physical unit (core.device_units), narrowing device_id to one specific serialized machine. NULL means "any unit of this device type" — existing behaviour, unaffected. When set, device_sessions/service.py prefills device_serial_number from it at session start instead of the CA typing one.';

CREATE INDEX IF NOT EXISTS idx_protocol_plan_device_unit
    ON core."protocol_plan" USING btree ("device_unit_id") WHERE "device_unit_id" IS NOT NULL;


COMMIT;


-- ###########################################################################
-- NO SEED DATA
-- ###########################################################################
-- Same reasoning as 37 — which serial numbers a clinic's units carry is a
-- fact only the clinic knows. An empty device_units table is not a problem:
-- clinic_devices quantity-only tracking keeps working exactly as before.
