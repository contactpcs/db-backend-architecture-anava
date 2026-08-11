-- 30_appointments_spine.sql
--
-- Hand-written net-new DDL (NOT generated from introspection — unlike files
-- 00-29, which are verbatim from the live schema). Applied 2026-08-11.
--
-- WHAT THIS DOES
-- Makes core.appointments the single spine for every clinic visit: initial
-- appointment, follow-up appointment, and device session (protocol session)
-- all become rows in one table. Payments attach to appointments and nothing
-- else. Clinical detail stays in child tables that point UP at the spine.
--
--   appointments  ← every clinic visit, one row, one FSM, one payment path
--        ▲
--        ├─ payments.appointment_id            (money)
--        ├─ treatment_sessions.appointment_id  (device-session clinical detail)
--        ├─ doctor_session_notes.appointment_id
--        ├─ patient_eeg_files.appointment_id
--        └─ prs_assessment_instances.appointment_id
--
-- EXPAND ONLY — nothing is dropped here.
-- New columns are added alongside the existing session_id columns; core.sessions
-- and every existing FK stay exactly as they are, so the running application
-- keeps working unchanged. The contract step (drop the session_id columns and
-- retire core.sessions) is a separate file, applied only after the application
-- code has moved over. See "DELIBERATELY NOT DONE HERE" at the bottom.
--
-- SAFE TO APPLY: verified 2026-08-11 against Anava_App_v1 — appointments,
-- sessions, treatment_sessions, treatment_plans, payments, doctor_session_notes,
-- patient_eeg_files, treatment_cycles, appointment_requests all hold 0 rows;
-- prs_assessment_instances holds 28 rows, all with session_id IS NULL. No
-- backfill required, no data at risk.


-- ---------------------------------------------------------------------------
-- 1. appointments becomes the spine
-- ---------------------------------------------------------------------------

-- A device session is run by a clinical assistant. It has no doctor of its own
-- — the supervising doctor is reachable through plan_id -> treatment_plans.doctor_id.
-- Leaving doctor_id NULL is also what keeps such rows out of the doctor overlap
-- guard below (NULL never compares equal in a GIST exclusion).
ALTER TABLE core."appointments" ALTER COLUMN "doctor_id" DROP NOT NULL;

-- A protocol prescribes sessions on planned DATES; the patient picks the TIME
-- later when they book a slot. Those rows exist with a date and no time.
ALTER TABLE core."appointments" ALTER COLUMN "start_time" DROP NOT NULL;
ALTER TABLE core."appointments" ALTER COLUMN "end_time" DROP NOT NULL;

-- Which protocol this visit belongs to. NULL for an initial appointment (no
-- protocol exists yet at that point); set for follow-ups and device sessions.
ALTER TABLE core."appointments" ADD COLUMN IF NOT EXISTS "plan_id" UUID;

ALTER TABLE core."appointments"
    DROP CONSTRAINT IF EXISTS "fk_appointments_plan_id";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "fk_appointments_plan_id"
    FOREIGN KEY ("plan_id") REFERENCES core."treatment_plans" ("plan_id") ON DELETE RESTRICT;

-- start_time and end_time are meaningful only as a pair.
ALTER TABLE core."appointments"
    DROP CONSTRAINT IF EXISTS "chk_appointments_time_pair";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_time_pair"
    CHECK (("start_time" IS NULL) = ("end_time" IS NULL));

-- Only a not-yet-booked ('planned') or abandoned ('cancelled') visit may lack a
-- time. Anything that occupies a slot must carry one.
ALTER TABLE core."appointments"
    DROP CONSTRAINT IF EXISTS "chk_appointments_slotted_has_time";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_slotted_has_time"
    CHECK ("start_time" IS NOT NULL OR "status" IN ('planned', 'cancelled'));

-- A device session always belongs to a protocol. Vacuously true for today's
-- appointment_type vocabulary; enforced from the moment the new one lands.
ALTER TABLE core."appointments"
    DROP CONSTRAINT IF EXISTS "chk_appointments_device_session_has_plan";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_device_session_has_plan"
    CHECK ("appointment_type" <> 'device_session' OR "plan_id" IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_appointments_plan_id ON core."appointments" USING btree ("plan_id");
-- Drives "what is this patient's next visit" — the single query that replaces
-- reading appointments and sessions separately.
CREATE INDEX IF NOT EXISTS idx_appointments_patient_date_status
    ON core."appointments" USING btree ("patient_id", "appointment_date", "status");


-- ---------------------------------------------------------------------------
-- 2. Overlap guards — one per resource, not one for doctors only
-- ---------------------------------------------------------------------------
-- The original excl_doctor_overlap (12b) keyed on doctor_id alone. Once device
-- sessions live in this table, that guard would refuse to book a CA-run device
-- session whenever the supervising doctor happened to be busy at the same hour
-- — a doctor who is not involved in that visit at all. Split into one guard per
-- resource that actually gets occupied.
--
-- Both guards skip 'planned' rows: those have no time yet, so there is nothing
-- to overlap. NULL doctor_id / ca_id rows drop out on their own (NULL is never
-- equal to NULL in a GIST exclusion).

ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "excl_doctor_overlap";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "excl_doctor_overlap"
    EXCLUDE USING gist (
        "doctor_id" WITH =,
        tsrange(("appointment_date" + "start_time"), ("appointment_date" + "end_time")) WITH &&
    )
    WHERE ("status" <> ALL (ARRAY['cancelled'::text, 'rescheduled'::text, 'planned'::text])
           AND "start_time" IS NOT NULL);

ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "excl_ca_overlap";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "excl_ca_overlap"
    EXCLUDE USING gist (
        "ca_id" WITH =,
        tsrange(("appointment_date" + "start_time"), ("appointment_date" + "end_time")) WITH &&
    )
    WHERE ("status" <> ALL (ARRAY['cancelled'::text, 'rescheduled'::text, 'planned'::text])
           AND "start_time" IS NOT NULL);


-- ---------------------------------------------------------------------------
-- 3. Child tables point UP at the spine
-- ---------------------------------------------------------------------------
-- Direction matters: treatment_sessions is partitioned yearly, so its PK is
-- composite (ts_id, created_at) and nothing can cleanly reference it. Children
-- carry the FK; the spine never points down.
--
-- Nullable for now — the existing session_id columns are still in place and
-- still populated by the current code. These become NOT NULL in the contract
-- step, once the application writes them.

ALTER TABLE core."treatment_sessions" ADD COLUMN IF NOT EXISTS "appointment_id" UUID;
ALTER TABLE core."treatment_sessions" DROP CONSTRAINT IF EXISTS "fk_treatment_sessions_appointment_id";
ALTER TABLE core."treatment_sessions"
    ADD CONSTRAINT "fk_treatment_sessions_appointment_id"
    FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT;

ALTER TABLE core."doctor_session_notes" ADD COLUMN IF NOT EXISTS "appointment_id" UUID;
ALTER TABLE core."doctor_session_notes" DROP CONSTRAINT IF EXISTS "fk_doctor_session_notes_appointment_id";
ALTER TABLE core."doctor_session_notes"
    ADD CONSTRAINT "fk_doctor_session_notes_appointment_id"
    FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT;

ALTER TABLE core."patient_eeg_files" ADD COLUMN IF NOT EXISTS "appointment_id" UUID;
ALTER TABLE core."patient_eeg_files" DROP CONSTRAINT IF EXISTS "fk_patient_eeg_files_appointment_id";
ALTER TABLE core."patient_eeg_files"
    ADD CONSTRAINT "fk_patient_eeg_files_appointment_id"
    FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT;

ALTER TABLE core."prs_assessment_instances" ADD COLUMN IF NOT EXISTS "appointment_id" UUID;
ALTER TABLE core."prs_assessment_instances" DROP CONSTRAINT IF EXISTS "fk_prs_assessment_instances_appointment_id";
ALTER TABLE core."prs_assessment_instances"
    ADD CONSTRAINT "fk_prs_assessment_instances_appointment_id"
    FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_ts_appointment_id ON core."treatment_sessions" USING btree ("appointment_id");
CREATE INDEX IF NOT EXISTS idx_dsn_appointment_id ON core."doctor_session_notes" USING btree ("appointment_id");
CREATE INDEX IF NOT EXISTS idx_eeg_appointment_id ON core."patient_eeg_files" USING btree ("appointment_id");
CREATE INDEX IF NOT EXISTS idx_prs_inst_appointment_id ON core."prs_assessment_instances" USING btree ("appointment_id");


-- ---------------------------------------------------------------------------
-- 4. Payments attach to the spine
-- ---------------------------------------------------------------------------
-- Before this, core.payments could only reference sessions or store_orders —
-- there was literally no way to record payment for a consultation. The initial
-- appointment, the first step of the whole patient journey, was unpayable.

ALTER TABLE core."payments" ADD COLUMN IF NOT EXISTS "appointment_id" UUID;
ALTER TABLE core."payments" DROP CONSTRAINT IF EXISTS "fk_payments_appointment_id";
ALTER TABLE core."payments"
    ADD CONSTRAINT "fk_payments_appointment_id"
    FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT;

-- A payment pays for exactly one thing. Previously all three targets were
-- nullable with no constraint, so a payment attached to nothing — or to two
-- things at once — was a valid row.
ALTER TABLE core."payments" DROP CONSTRAINT IF EXISTS "chk_payments_single_target";
ALTER TABLE core."payments"
    ADD CONSTRAINT "chk_payments_single_target"
    CHECK (num_nonnulls("appointment_id", "session_id", "order_id") = 1);

CREATE INDEX IF NOT EXISTS idx_payments_appointment_id ON core."payments" USING btree ("appointment_id");


-- ---------------------------------------------------------------------------
-- 5. reference.billable_items — the superadmin-owned price catalog
-- ---------------------------------------------------------------------------
-- One table covers both billable things: appointments (priced per type) and
-- device sessions (priced per device). Superadmin adds, removes, renames and
-- reprices rows; every other role reads. Adding a new appointment type or a new
-- device is an INSERT, never a migration.
--
-- Deliberately NOT built: per-clinic / per-doctor / per-region price scoping,
-- seasonal price lists, bulk packages. One price per item until a clinic
-- actually needs to differ — the earlier payments plan specced all of it, none
-- of it is required by the agreed model.
--
-- Placed in `reference` (alongside products and the PRS catalogue) rather than
-- a new `billing` schema: this is catalogue data, and reference already carries
-- the right grant defaults.

CREATE TABLE IF NOT EXISTS reference."billable_items" (
    "item_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "item_code" TEXT NOT NULL,
    "category" TEXT NOT NULL,
    -- exactly one of these two is set, per chk_billable_items_category_shape
    "appointment_type" TEXT,   -- joins core.appointments.appointment_type
    "device_type" TEXT,        -- joins core.treatment_plans.device_type
    "name" TEXT NOT NULL,
    "description" TEXT,
    "price" NUMERIC(10,2) NOT NULL,
    "currency" TEXT NOT NULL DEFAULT 'INR'::text,
    "duration_minutes" INTEGER,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "created_by" UUID,
    "updated_by" UUID,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE reference."billable_items" DROP CONSTRAINT IF EXISTS "billable_items_pkey" CASCADE;
ALTER TABLE reference."billable_items" ADD CONSTRAINT "billable_items_pkey" PRIMARY KEY ("item_id");

ALTER TABLE reference."billable_items" DROP CONSTRAINT IF EXISTS "billable_items_item_code_key";
ALTER TABLE reference."billable_items" ADD CONSTRAINT "billable_items_item_code_key" UNIQUE ("item_code");

ALTER TABLE reference."billable_items" DROP CONSTRAINT IF EXISTS "chk_billable_items_category";
ALTER TABLE reference."billable_items"
    ADD CONSTRAINT "chk_billable_items_category"
    CHECK ("category" IN ('appointment', 'device_session'));

-- An appointment item is keyed by appointment_type; a device-session item by
-- device_type. Never both, never neither.
ALTER TABLE reference."billable_items" DROP CONSTRAINT IF EXISTS "chk_billable_items_category_shape";
ALTER TABLE reference."billable_items"
    ADD CONSTRAINT "chk_billable_items_category_shape"
    CHECK (
        ("category" = 'appointment'    AND "appointment_type" IS NOT NULL AND "device_type" IS NULL)
        OR
        ("category" = 'device_session' AND "device_type" IS NOT NULL AND "appointment_type" IS NULL)
    );

ALTER TABLE reference."billable_items" DROP CONSTRAINT IF EXISTS "chk_billable_items_price_non_negative";
ALTER TABLE reference."billable_items"
    ADD CONSTRAINT "chk_billable_items_price_non_negative"
    CHECK ("price" >= 0);

ALTER TABLE reference."billable_items" DROP CONSTRAINT IF EXISTS "fk_billable_items_created_by";
ALTER TABLE reference."billable_items"
    ADD CONSTRAINT "fk_billable_items_created_by"
    FOREIGN KEY ("created_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

ALTER TABLE reference."billable_items" DROP CONSTRAINT IF EXISTS "fk_billable_items_updated_by";
ALTER TABLE reference."billable_items"
    ADD CONSTRAINT "fk_billable_items_updated_by"
    FOREIGN KEY ("updated_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

-- Only one live price per billable thing at a time. Archived/superseded rows
-- keep their history by going is_active = false.
DROP INDEX IF EXISTS reference.uq_billable_items_active_appointment_type;
CREATE UNIQUE INDEX uq_billable_items_active_appointment_type
    ON reference."billable_items" ("appointment_type")
    WHERE "is_active" AND "category" = 'appointment';

DROP INDEX IF EXISTS reference.uq_billable_items_active_device_type;
CREATE UNIQUE INDEX uq_billable_items_active_device_type
    ON reference."billable_items" ("device_type")
    WHERE "is_active" AND "category" = 'device_session';

CREATE INDEX IF NOT EXISTS idx_billable_items_active ON reference."billable_items" USING btree ("is_active");

DROP TRIGGER IF EXISTS trg_updated_at_billable_items ON reference."billable_items";
CREATE TRIGGER trg_updated_at_billable_items
    BEFORE UPDATE ON reference."billable_items"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();

-- RLS — mirrors reference.products exactly: everyone reads the catalogue,
-- only super_admin writes it. Enabling RLS without writing policies is what
-- silently locked four tables out of the app earlier (see NOTES.md); policies
-- go in the same file as the ENABLE, always.
ALTER TABLE reference."billable_items" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."billable_items" FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_billable_items_select" ON reference."billable_items";
CREATE POLICY "rls_billable_items_select" ON reference."billable_items" FOR SELECT TO public
    USING (true);

DROP POLICY IF EXISTS "rls_billable_items_insert" ON reference."billable_items";
CREATE POLICY "rls_billable_items_insert" ON reference."billable_items" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_billable_items_update" ON reference."billable_items";
CREATE POLICY "rls_billable_items_update" ON reference."billable_items" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

-- 18_grants.sql's GRANT ... ON ALL TABLES ran before this table existed, and
-- ALTER DEFAULT PRIVILEGES only covers tables created by the role that set it.
-- Granting explicitly so this table can't be the next silent lockout.
GRANT SELECT ON reference."billable_items" TO anava_app;
GRANT INSERT, UPDATE ON reference."billable_items" TO anava_app;
GRANT SELECT ON reference."billable_items" TO anava_readonly;

-- No seed rows on purpose. Prices are the superadmin's to set, and a placeholder
-- price is worse than no price: pricing a real consultation at a made-up number
-- is a silent error, whereas an empty catalogue fails loudly and visibly the
-- first time someone tries to book.


-- ---------------------------------------------------------------------------
-- DELIBERATELY NOT DONE HERE — each needs the application code to move first
-- ---------------------------------------------------------------------------
-- 1. CHECK on appointments.appointment_type IN ('initial','follow_up','device_session').
--    The running code still writes seven older values ('initial_assessment',
--    'doctor_consultation', 'ca_session', 'treatment_session', 'follow_up',
--    'demo_visit', 'teleconsult' — scheduling/schemas.py) and has two different
--    defaults for the same field (scheduling/service.py:393 vs :488). Adding the
--    constraint now would break every booking. Lands with the code change.
--
-- 2. CHECK on appointments.status. Needs 'planned' and 'pending_payment' added to
--    the existing eight-value FSM in scheduling/service.py first.
--
-- 3. Dropping core.sessions and the five session_id columns that reference it.
--    Contract step of expand-contract: only after the code reads and writes
--    appointment_id everywhere. Nothing is lost by waiting; a great deal is lost
--    by dropping a table the running app still queries.
--
-- 4. Reservation/hold expiry, refunds, packages, ledger. Not modelled at all yet
--    — the agreed model is one payment per visit, and none of that is needed for it.
