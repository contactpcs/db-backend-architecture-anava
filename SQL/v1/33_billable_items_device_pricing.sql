-- 33_billable_items_device_pricing.sql
--
-- Hand-written net-new DDL (like 30, 31 and 32).
--
-- SCOPE: one seam, between 30_appointments_spine.sql and 32_treatment_protocol.sql.
-- Neither file owned it, and 32 flags it as its OPEN ITEM 7.
--
-- APPLY ORDER:  30 -> 31 -> 32 -> 33 -> 34
--
-- NOT YET EXECUTED ANYWHERE. Written against the schema as recorded in
-- SQL/v1/00-32 and verified by reading those files, not by running this one.
--
--
-- THE PROBLEM
--
-- 30 §5 created reference.billable_items to price two kinds of billable thing:
-- an appointment (keyed by appointment_type) and a device session (keyed by
-- device_type, a free-text column copied from treatment_plans.device_type).
-- Free text was the only option at the time — no device registry existed.
--
-- 32 created one: reference.neuromod_devices, one row per (manufacturer, model),
-- with a real device_id. The two vocabularies never met, so a device session has
-- no resolvable price.
--
-- That is not cosmetic. A device session moves planned -> selected -> paid, and
-- the payment step has no amount to charge without a price. An unpriceable visit
-- can never leave 'selected', so it expires on the hold timeout and reverts to
-- 'planned' forever. The whole device-session payment path is blocked until
-- these two agree.
--
-- SAFE TO APPLY: reference.billable_items holds 0 rows. 30 §5 seeds nothing on
-- purpose ("a placeholder price is worse than no price"), and no endpoint writes
-- it. The restructure below therefore touches no data.


BEGIN;


-- ---------------------------------------------------------------------------
-- 1. Price a device session by device, not by a free-text label
-- ---------------------------------------------------------------------------

ALTER TABLE reference."billable_items" ADD COLUMN IF NOT EXISTS "device_id" UUID;

ALTER TABLE reference."billable_items" DROP CONSTRAINT IF EXISTS "fk_billable_items_device_id";
ALTER TABLE reference."billable_items"
    ADD CONSTRAINT "fk_billable_items_device_id"
    FOREIGN KEY ("device_id") REFERENCES reference."neuromod_devices" ("device_id") ON DELETE RESTRICT;

COMMENT ON COLUMN reference."billable_items"."device_id" IS 'Which device this price is for, when category = device_session. A real FK to reference.neuromod_devices, replacing the free-text device_type 30 used before a device registry existed. Two tDCS units from different manufacturers are two devices and may carry two prices.';

-- The old text column is replaced rather than kept alongside: two ways to say
-- which device a price belongs to is exactly the ambiguity this file exists to
-- remove, and a nullable leftover would silently accept rows keyed the old way.
-- Its dependent CHECK and unique index must go first — DROP COLUMN would take
-- them anyway, but naming them here makes the change reviewable.
ALTER TABLE reference."billable_items" DROP CONSTRAINT IF EXISTS "chk_billable_items_category_shape";
DROP INDEX IF EXISTS reference.uq_billable_items_active_device_type;
ALTER TABLE reference."billable_items" DROP COLUMN IF EXISTS "device_type";

-- Restated from 30 §5 with device_id in place of device_type. An appointment
-- item is keyed by appointment_type; a device-session item by a real device.
-- Never both, never neither.
ALTER TABLE reference."billable_items"
    ADD CONSTRAINT "chk_billable_items_category_shape"
    CHECK (
        ("category" = 'appointment'    AND "appointment_type" IS NOT NULL AND "device_id" IS NULL)
        OR
        ("category" = 'device_session' AND "device_id" IS NOT NULL AND "appointment_type" IS NULL)
    );

-- One live price per device, mirroring uq_billable_items_active_appointment_type.
-- Archived prices keep their history by going is_active = false.
DROP INDEX IF EXISTS reference.uq_billable_items_active_device_id;
CREATE UNIQUE INDEX uq_billable_items_active_device_id
    ON reference."billable_items" ("device_id")
    WHERE "is_active" AND "category" = 'device_session';

CREATE INDEX IF NOT EXISTS idx_billable_items_device_id
    ON reference."billable_items" USING btree ("device_id");


COMMIT;


-- ---------------------------------------------------------------------------
-- STILL OPEN AFTER THIS FILE
-- ---------------------------------------------------------------------------
--
--  A. CLOSED by 35_clinic_device_schedules.sql — core.clinic_devices,
--     core.clinic_device_schedules and core.clinic_device_schedule_overrides.
--     Capacity itself still cannot be a constraint (PostgreSQL has no "at most
--     N rows matching a predicate" form); 35's footer specifies the counted
--     check under SELECT ... FOR UPDATE that the booking path must implement.
--
--  B. CHECK on appointments.appointment_type. The value set is now settled —
--     'initial', 'follow_up', 'device_session', 'protocol_followup' — but the
--     staff booking path still writes seven legacy values. Lands with that move.
--
--  C. CHECK on appointments.status. Likewise settled — 'planned', 'selected',
--     'paid', 'cancelled', 'rescheduled', 'checked_in', 'in_progress',
--     'completed', 'no_show' — and likewise blocked, because 'confirmed' is
--     still reachable through PATCH /appointments/{id}/status.
--
--  D. ops.rls_clinic_id() returns NULL for every caller, everywhere, because
--     app.current_clinic_id is never set by AuthContextMiddleware. 32's policies
--     now route around it via clinic_staff_assignments; the schema-wide fix is
--     either to set that GUC in the middleware or to retire the function. It is
--     currently harmless only because the application's DB role is a superuser
--     and bypasses RLS entirely.
--
--  E. core.sessions and core.treatment_sessions are confirmed legacy. 32 settled
--     the question 31 had to leave open: device sessions live on the appointments
--     spine and no new table duplicates them. Retiring both is a contract step
--     blocked on the clinical module, which still reads and writes core.sessions.
--
--  F. CLOSED in 32_treatment_protocol.sql — fn_generate_protocol_sessions now
--     reads the author's role from core.profiles (the row set_by already points
--     at) and writes that into booked_by_role, instead of hardcoding 'doctor'.
--     It raises if the role cannot be resolved rather than guessing.
-- ---------------------------------------------------------------------------
