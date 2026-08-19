-- 53_billable_items_clinic_pricing.sql
--
-- APPLY ORDER: after 33 (billable_items.device_id). Independent of 34-52.
--
-- THE PROBLEM
--
-- reference.billable_items (30 §5, restructured by 33) prices one thing —
-- an appointment_type or a device_id — exactly once, platform-wide. Super
-- admin wants clinic A and clinic B to charge different amounts for the
-- same appointment_type (e.g. initial assessment ₹500 at clinic A, ₹1000
-- at clinic B). There is no column to hang that override on.
--
-- THE FIX
--
-- Add nullable clinic_id. NULL = platform default, used whenever no
-- clinic-specific row exists. Non-null = an override for that one clinic
-- only. resolve_price() (app/modules/admin/repository.py) prefers a
-- clinic-specific row over the default when both exist.
--
-- The old single-column unique indexes (30 §5, 33) assumed one active price
-- platform-wide and can't just gain a clinic_id column — a single unique
-- index treats every NULL as distinct, so it would silently accept
-- unlimited duplicate default (clinic_id IS NULL) rows for the same
-- appointment_type. Split into two indexes each: one scoped to the default
-- row, one scoped to per-clinic overrides.
--
-- SAFE TO APPLY: reference.billable_items holds 0 rows in every environment
-- this has run against — 30 §5 seeded nothing on purpose, and the admin CRUD
-- screen that can write to it (shipped this session) has not been used yet.
-- No data migration needed.

BEGIN;

ALTER TABLE reference."billable_items" ADD COLUMN IF NOT EXISTS "clinic_id" UUID;

ALTER TABLE reference."billable_items" DROP CONSTRAINT IF EXISTS "fk_billable_items_clinic_id";
ALTER TABLE reference."billable_items"
    ADD CONSTRAINT "fk_billable_items_clinic_id"
    FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE CASCADE;

COMMENT ON COLUMN reference."billable_items"."clinic_id" IS 'NULL = platform default price, used when no clinic-specific override exists for this appointment_type/device_id. Non-null = an override for that one clinic only. resolve_price() prefers the clinic-specific row.';

-- Replaces uq_billable_items_active_appointment_type (30 §5) — same intent,
-- split on clinic_id IS NULL vs NOT NULL so NULL-clinic default rows and
-- per-clinic override rows each get their own one-active-row guarantee.
DROP INDEX IF EXISTS reference.uq_billable_items_active_appointment_type;
CREATE UNIQUE INDEX uq_billable_items_active_appt_type_default
    ON reference."billable_items" ("appointment_type")
    WHERE "is_active" AND "category" = 'appointment' AND "clinic_id" IS NULL;
CREATE UNIQUE INDEX uq_billable_items_active_appt_type_clinic
    ON reference."billable_items" ("clinic_id", "appointment_type")
    WHERE "is_active" AND "category" = 'appointment' AND "clinic_id" IS NOT NULL;

-- Replaces uq_billable_items_active_device_id (33). Same split.
DROP INDEX IF EXISTS reference.uq_billable_items_active_device_id;
CREATE UNIQUE INDEX uq_billable_items_active_device_id_default
    ON reference."billable_items" ("device_id")
    WHERE "is_active" AND "category" = 'device_session' AND "clinic_id" IS NULL;
CREATE UNIQUE INDEX uq_billable_items_active_device_id_clinic
    ON reference."billable_items" ("clinic_id", "device_id")
    WHERE "is_active" AND "category" = 'device_session' AND "clinic_id" IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_billable_items_clinic_id ON reference."billable_items" USING btree ("clinic_id");

COMMIT;
