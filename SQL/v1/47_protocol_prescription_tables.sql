-- 47_protocol_prescription_tables.sql
--
-- Renames core.treatment_protocols to core.protocol_plan and adds two new
-- tables — core.protocol_device_sessions and core.protocol_followup — that
-- record the doctor's session-by-session setup, each row pointing at the
-- appointments row it generated.
--
-- SCOPE: one RENAME (protocol_plan), two new tables, their PKs/FKs/uniques,
-- RLS and grants. Nothing on core.appointments changes — session_number,
-- clinic_device_id, protocol_id, plan_id and every constraint that reads them
-- (30/31/32/41/45) are untouched.
--
-- APPLY ORDER: after 46.
--
-- NOT YET EXECUTED ANYWHERE.
--
--
-- ###########################################################################
-- WHY
-- ###########################################################################
--
-- 45 gave the protocol a real parent — core.protocol_instances, one row per
-- course of device treatment — and made treatment_protocols.plan_id optional.
-- What 45 left as "still open" (its own §, item 1) is finished here: instance
-- ID is no longer one of two optional parents, it is THE parent.
--
--   core.protocol_instances        the course (5th protocol set by this
--                                   doctor for this patient, 6th, ...)
--         └─ core.protocol_plan    the prescription itself — device, montage,
--            (was treatment_protocols) dose, session plan. Renamed, not
--                                   recreated: same protocol_id PK, same
--                                   twelve nullable placement/dosing columns,
--                                   same session_count / follow_up_every_n.
--               ├─ core.protocol_device_sessions   NEW. one row per device
--               │                                   session the doctor set up
--               └─ core.protocol_followup          NEW. one row per follow-up
--                                                   the doctor set up
--
-- Renamed, not recreated: ALTER TABLE ... RENAME TO carries every column,
-- constraint, index, trigger, RLS policy and comment forward unchanged; only
-- the objects whose NAME embeds "treatment_protocols" are renamed in §2, for
-- readability, not correctness.
--
-- 45's header claimed the table was empty in every environment. That turned
-- out false — the environment this ran against had rows with instance_id
-- NULL, plan_id NOT NULL (the pre-47 plan-only creation path). §1a backfills
-- one protocol_instances row per such orphan before the NOT NULL lands, so no
-- existing protocol_plan row is left parentless.
--
-- protocol_device_sessions and protocol_followup are new because 45's own
-- table comment already promised them: "the parent of treatment_protocols,
-- and the object the Treatment Protocol UI actually lists." The prescription
-- (protocol_plan) says WHAT was prescribed; these two say WHEN, one row per
-- planned visit, each carrying the appointment_id of the calendar row it
-- produced.
--
--
-- ###########################################################################
-- WHAT THIS FILE DELIBERATELY DOES NOT DO
-- ###########################################################################
--
-- It does not touch core.appointments, and it does not make these two tables
-- the source of truth for anything mutable. 45's header already explains why
-- standalone session/followup tables were dropped once in this project's
-- history — the appointments spine (30) is what status, hold_expires_at,
-- start_time/end_time, and the hold sweeper (31/45) all key off, and four
-- separate pieces of work depend on that spine (45 §"WHAT THIS FILE
-- DELIBERATELY DOES NOT DO" lists them). Reversing that here would be the
-- same rework for no gain 45 already declined.
--
-- These two tables are a PRESCRIPTION record, not a second spine: they store
-- what the doctor set at authoring time (session_number / after_session_number,
-- planned_date, which device) and a foreign key to the appointments row that
-- carries the live, mutable state (status, start_time, hold_expires_at). If a
-- session is rescheduled, appointments.appointment_date moves; this table's
-- planned_date does not — it is the record of what was originally prescribed,
-- which is the whole reason it is a separate column rather than a duplicate
-- appointments.appointment_date. Reading "is this session done" still means
-- reading the appointments row, not this one — this table has no status
-- column on purpose, so there is exactly one place a session's live state can
-- live and no way for the two to disagree.
--
-- It does not touch fn_check_device_available_at_clinic's plan_id fallback
-- branch (45 §3b) or protocol_plan's plan_id column. Both stay: plan_id
-- remains a valid, optional pointer to the clinical plan a protocol
-- accompanies, and dropping the fallback branch now would be tidying code
-- this file has no reason to touch.
--
--
-- ###########################################################################
-- THE ONE THING THAT WOULD BITE IF IT WERE MISSED
-- ###########################################################################
--
-- Every raw-SQL caller in the Python layer that writes "FROM treatment_
-- protocols" / "treatment_protocols tp" against the live table name breaks the
-- moment this file's RENAME runs — silently, as a 500 from an undefined
-- relation, not as a readable error. This migration and the matching
-- backend/app/modules/treatment_protocols/*, backend/app/modules/scheduling/
-- repository.py (is_used_by_a_protocol) and backend/app/workers/
-- retention_purge.py edits ship together for exactly that reason.
--
--
BEGIN;


-- ###########################################################################
-- §1  Rename treatment_protocols -> protocol_plan; instance_id becomes real
-- ###########################################################################

ALTER TABLE core."treatment_protocols" RENAME TO "protocol_plan";

COMMENT ON TABLE core."protocol_plan" IS 'Treatment Protocol — device + montage + dosing + session plan, set once by a doctor to start a course of treatment. Formerly core.treatment_protocols (renamed 47); a CHILD of core.protocol_instances (45), which is the course, and optionally of core.treatment_plans, which is the clinical picture. Retention: clinical, Bucket 2 — anonymise with the patient profile, never hard-delete.';


-- ---------------------------------------------------------------------------
-- §1a  Backfill: give every plan-only row an instance before instance_id
--      becomes NOT NULL
-- ---------------------------------------------------------------------------
-- A row here has instance_id NULL only if it was written through the pre-47
-- plan-only path, which means chk_treatment_protocols_has_parent (45) already
-- guarantees plan_id IS NOT NULL on it — the JOIN below cannot silently drop
-- a row. One protocol_instances row is opened per orphan, on the orphan's own
-- plan's cycle, numbered after whatever instances that cycle already has.
--
-- Status is carried over rather than defaulted to 'draft': an orphan that is
-- already 'active' or 'completed' opening a fresh 'draft' instance would
-- collide with uq_protocol_instances_one_active the moment a second orphan
-- shares a cycle, and would misrepresent a course that already ran as one
-- that has not started.
DO $$
DECLARE
    r RECORD;
    v_instance_id UUID;
    v_number      INTEGER;
    v_status      TEXT;
BEGIN
    FOR r IN
        SELECT tp.protocol_id, tp.status AS protocol_status, tp.set_by,
               pl.cycle_id, pl.patient_id
        FROM core."protocol_plan" tp
        JOIN core."treatment_plans" pl ON pl.plan_id = tp.plan_id
        WHERE tp.instance_id IS NULL
    LOOP
        SELECT COALESCE(MAX(instance_number), 0) + 1 INTO v_number
        FROM core."protocol_instances" WHERE cycle_id = r.cycle_id;

        v_status := CASE WHEN r.protocol_status IN ('draft', 'active') THEN r.protocol_status ELSE 'completed' END;

        BEGIN
            INSERT INTO core."protocol_instances" (cycle_id, patient_id, created_by, instance_number, status)
            VALUES (r.cycle_id, r.patient_id, r.set_by, v_number, v_status)
            RETURNING instance_id INTO v_instance_id;
        EXCEPTION WHEN unique_violation THEN
            -- uq_protocol_instances_one_active: two orphans on the same
            -- cycle both carrying 'draft'/'active' status. Rather than fail
            -- the whole backfill, the second orphan attaches to the cycle's
            -- existing open instance instead of opening a second one.
            SELECT instance_id INTO v_instance_id
            FROM core."protocol_instances"
            WHERE cycle_id = r.cycle_id AND status IN ('draft', 'active')
            LIMIT 1;
        END;

        UPDATE core."protocol_plan" SET instance_id = v_instance_id WHERE protocol_id = r.protocol_id;
    END LOOP;
END $$;

-- instance_id was nullable in 45 "only so [45] can run against existing
-- rows; new rows must set it." §1a just backfilled every existing row, so the
-- promise 45 deferred is kept here: a protocol_plan row with no instance is
-- no longer representable.
ALTER TABLE core."protocol_plan" ALTER COLUMN "instance_id" SET NOT NULL;

-- num_nonnulls(instance_id, plan_id) >= 1 is now implied by instance_id being
-- NOT NULL — the CHECK can never fail, so it is dead weight, not a guard.
ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_treatment_protocols_has_parent";


-- ---------------------------------------------------------------------------
-- Renamed constraints, indexes, triggers, policies
-- ---------------------------------------------------------------------------
-- Cosmetic, not functional — a RENAME carries every one of these forward
-- working correctly under its old name whether this section runs or not.
-- Renamed anyway so `\d protocol_plan` and every future migration's comments
-- describe the table they are actually looking at.
--
-- §1a already proved the live database does not fully match what 32/39/45's
-- own text claims (instance_id had nulls the header said couldn't exist) —
-- so these ~20 hardcoded old names are a best-effort guess, not a certainty.
-- None of them gates anything functional (§2 onward does not depend on any of
-- these new names), so a miss here must not abort the whole migration the
-- way a bare RENAME CONSTRAINT would: one failed statement poisons the
-- transaction and silently skips every statement after it, including the two
-- new tables. Each rename below is therefore its own subtransaction that
-- swallows "does not exist" and moves on.

DO $$
DECLARE
    stmts TEXT[] := ARRAY[
        'ALTER TABLE core."protocol_plan" RENAME CONSTRAINT "treatment_protocols_pkey" TO "protocol_plan_pkey"',
        'ALTER TABLE core."protocol_plan" RENAME CONSTRAINT "chk_treatment_protocols_session_count" TO "chk_protocol_plan_session_count"',
        'ALTER TABLE core."protocol_plan" RENAME CONSTRAINT "chk_treatment_protocols_status" TO "chk_protocol_plan_status"',
        'ALTER TABLE core."protocol_plan" RENAME CONSTRAINT "chk_treatment_protocols_follow_up_every_n" TO "chk_protocol_plan_follow_up_every_n"',
        'ALTER TABLE core."protocol_plan" RENAME CONSTRAINT "chk_treatment_protocols_one_placement" TO "chk_protocol_plan_one_placement"',
        'ALTER TABLE core."protocol_plan" RENAME CONSTRAINT "chk_treatment_protocols_one_dosing" TO "chk_protocol_plan_one_dosing"',
        'ALTER TABLE core."protocol_plan" RENAME CONSTRAINT "fk_treatment_protocols_instance_id" TO "fk_protocol_plan_instance_id"',
        'ALTER TABLE core."protocol_plan" RENAME CONSTRAINT "fk_treatment_protocols_authored_in_appointment_id" TO "fk_protocol_plan_authored_in_appointment_id"',
        'ALTER TABLE core."protocol_plan" RENAME CONSTRAINT "chk_treatment_protocols_current_ma" TO "chk_protocol_plan_current_ma"',
        'ALTER TABLE core."protocol_plan" RENAME CONSTRAINT "chk_treatment_protocols_duration_min" TO "chk_protocol_plan_duration_min"',
        'ALTER TABLE core."protocol_plan" RENAME CONSTRAINT "chk_treatment_protocols_ramp" TO "chk_protocol_plan_ramp"',
        'ALTER TABLE core."protocol_plan" RENAME CONSTRAINT "chk_treatment_protocols_sessions_per_week" TO "chk_protocol_plan_sessions_per_week"',
        'ALTER TABLE core."protocol_plan" RENAME CONSTRAINT "fk_treatment_protocols_supersedes" TO "fk_protocol_plan_supersedes"',
        'ALTER TABLE core."protocol_plan" RENAME CONSTRAINT "chk_treatment_protocols_no_self_supersede" TO "chk_protocol_plan_no_self_supersede"',
        'ALTER TRIGGER "trg_updated_at_treatment_protocols" ON core."protocol_plan" RENAME TO "trg_updated_at_protocol_plan"',
        'ALTER TRIGGER "trg_audit_treatment_protocols" ON core."protocol_plan" RENAME TO "trg_audit_protocol_plan"',
        'ALTER POLICY "rls_treatment_protocols_select" ON core."protocol_plan" RENAME TO "rls_protocol_plan_select"',
        'ALTER POLICY "rls_treatment_protocols_insert" ON core."protocol_plan" RENAME TO "rls_protocol_plan_insert"',
        'ALTER POLICY "rls_treatment_protocols_update" ON core."protocol_plan" RENAME TO "rls_protocol_plan_update"'
    ];
    stmt TEXT;
BEGIN
    FOREACH stmt IN ARRAY stmts LOOP
        BEGIN
            EXECUTE stmt;
        EXCEPTION WHEN undefined_object THEN
            RAISE NOTICE 'skipped (name not found live): %', stmt;
        END;
    END LOOP;
END $$;

-- ALTER INDEX ... IF EXISTS is natively safe, no wrapper needed.
ALTER INDEX IF EXISTS core."idx_treatment_protocols_plan_id" RENAME TO "idx_protocol_plan_plan_id";
ALTER INDEX IF EXISTS core."idx_treatment_protocols_status" RENAME TO "idx_protocol_plan_status";
ALTER INDEX IF EXISTS core."idx_treatment_protocols_plan_active" RENAME TO "idx_protocol_plan_plan_active";
ALTER INDEX IF EXISTS core."idx_treatment_protocols_instance" RENAME TO "idx_protocol_plan_instance";
ALTER INDEX IF EXISTS core."idx_treatment_protocols_authored_in" RENAME TO "idx_protocol_plan_authored_in";
ALTER INDEX IF EXISTS core."uq_treatment_protocols_supersedes" RENAME TO "uq_protocol_plan_supersedes";


-- ###########################################################################
-- §2  core.protocol_device_sessions
-- ###########################################################################

CREATE TABLE IF NOT EXISTS core."protocol_device_sessions" (
    "device_session_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "instance_id"        UUID NOT NULL,
    "protocol_id"        UUID NOT NULL,
    "appointment_id"      UUID NOT NULL,
    "clinic_device_id"   UUID NOT NULL,
    "session_number"     INTEGER NOT NULL,
    "planned_date"       DATE NOT NULL,
    "created_at"         TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_protocol_device_sessions_number" CHECK ("session_number" > 0)
);
COMMENT ON TABLE core."protocol_device_sessions" IS 'One device session as the doctor set it up: which ordinal, which unit, what date was prescribed. Points at the appointments row that carries the live bookable state (status, start_time, hold_expires_at) — this table never mirrors that state, so there is one place it can live. Retention: clinical, Bucket 2.';
COMMENT ON COLUMN core."protocol_device_sessions"."instance_id" IS 'The course this session belongs to. Denormalised from protocol_id (which already implies it through protocol_plan.instance_id) so a course''s full schedule is one query without joining through protocol_plan.';
COMMENT ON COLUMN core."protocol_device_sessions"."planned_date" IS 'The date prescribed at authoring time. Does not move if the patient reschedules — appointments.appointment_date is the current date; this is what was originally set. Read the appointments row for anything that can change after setup.';
COMMENT ON COLUMN core."protocol_device_sessions"."clinic_device_id" IS 'Denormalised from the appointments row created alongside it (41 made this mandatory there). Lets "which unit was session 7 planned on" be answered without joining appointments.';

ALTER TABLE core."protocol_device_sessions" DROP CONSTRAINT IF EXISTS "protocol_device_sessions_pkey" CASCADE;
ALTER TABLE core."protocol_device_sessions" ADD CONSTRAINT "protocol_device_sessions_pkey" PRIMARY KEY ("device_session_id");

ALTER TABLE core."protocol_device_sessions" DROP CONSTRAINT IF EXISTS "uq_protocol_device_sessions_appointment_id";
ALTER TABLE core."protocol_device_sessions" ADD CONSTRAINT "uq_protocol_device_sessions_appointment_id" UNIQUE ("appointment_id");

-- Session 7 of a given prescription exists once. Keyed on protocol_id, not
-- instance_id: an amendment (a new protocol_plan row superseding an earlier
-- one, per ProtocolInstanceService's own docstring) legitimately restarts
-- numbering under the same instance.
ALTER TABLE core."protocol_device_sessions" DROP CONSTRAINT IF EXISTS "uq_protocol_device_sessions_protocol_number";
ALTER TABLE core."protocol_device_sessions" ADD CONSTRAINT "uq_protocol_device_sessions_protocol_number" UNIQUE ("protocol_id", "session_number");

ALTER TABLE core."protocol_device_sessions" DROP CONSTRAINT IF EXISTS "fk_protocol_device_sessions_instance_id";
ALTER TABLE core."protocol_device_sessions" ADD CONSTRAINT "fk_protocol_device_sessions_instance_id"
    FOREIGN KEY ("instance_id") REFERENCES core."protocol_instances" ("instance_id") ON DELETE RESTRICT;

ALTER TABLE core."protocol_device_sessions" DROP CONSTRAINT IF EXISTS "fk_protocol_device_sessions_protocol_id";
ALTER TABLE core."protocol_device_sessions" ADD CONSTRAINT "fk_protocol_device_sessions_protocol_id"
    FOREIGN KEY ("protocol_id") REFERENCES core."protocol_plan" ("protocol_id") ON DELETE RESTRICT;

ALTER TABLE core."protocol_device_sessions" DROP CONSTRAINT IF EXISTS "fk_protocol_device_sessions_appointment_id";
ALTER TABLE core."protocol_device_sessions" ADD CONSTRAINT "fk_protocol_device_sessions_appointment_id"
    FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT;

ALTER TABLE core."protocol_device_sessions" DROP CONSTRAINT IF EXISTS "fk_protocol_device_sessions_clinic_device_id";
ALTER TABLE core."protocol_device_sessions" ADD CONSTRAINT "fk_protocol_device_sessions_clinic_device_id"
    FOREIGN KEY ("clinic_device_id") REFERENCES core."clinic_devices" ("clinic_device_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_protocol_device_sessions_instance ON core."protocol_device_sessions" USING btree ("instance_id");
CREATE INDEX IF NOT EXISTS idx_protocol_device_sessions_protocol ON core."protocol_device_sessions" USING btree ("protocol_id");

DROP TRIGGER IF EXISTS trg_protocol_device_sessions_updated_at ON core."protocol_device_sessions";
CREATE TRIGGER trg_protocol_device_sessions_updated_at
    BEFORE UPDATE ON core."protocol_device_sessions"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();


-- ###########################################################################
-- §3  core.protocol_followup
-- ###########################################################################

CREATE TABLE IF NOT EXISTS core."protocol_followup" (
    "followup_id"          UUID NOT NULL DEFAULT gen_random_uuid(),
    "instance_id"          UUID NOT NULL,
    "protocol_id"          UUID NOT NULL,
    "appointment_id"       UUID NOT NULL,
    "after_session_number" INTEGER NOT NULL,
    "planned_date"         DATE NOT NULL,
    "created_at"           TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_protocol_followup_after_session_number" CHECK ("after_session_number" > 0)
);
COMMENT ON TABLE core."protocol_followup" IS 'One follow-up consultation as the doctor set it up: after which device session, what date was prescribed. Points at the appointments row that carries the live bookable state, the same split protocol_device_sessions makes and for the same reason. Retention: clinical, Bucket 2.';
COMMENT ON COLUMN core."protocol_followup"."after_session_number" IS 'Which device session triggers this follow-up (5, 10, 15, 20 for follow_up_every_n = 5) — mirrors appointments.session_number and followup_prs_responses.after_session_number for the same row.';
COMMENT ON COLUMN core."protocol_followup"."planned_date" IS 'The date prescribed at authoring time; does not move on reschedule. See protocol_device_sessions.planned_date for why this is not appointments.appointment_date duplicated.';

ALTER TABLE core."protocol_followup" DROP CONSTRAINT IF EXISTS "protocol_followup_pkey" CASCADE;
ALTER TABLE core."protocol_followup" ADD CONSTRAINT "protocol_followup_pkey" PRIMARY KEY ("followup_id");

ALTER TABLE core."protocol_followup" DROP CONSTRAINT IF EXISTS "uq_protocol_followup_appointment_id";
ALTER TABLE core."protocol_followup" ADD CONSTRAINT "uq_protocol_followup_appointment_id" UNIQUE ("appointment_id");

ALTER TABLE core."protocol_followup" DROP CONSTRAINT IF EXISTS "uq_protocol_followup_protocol_after";
ALTER TABLE core."protocol_followup" ADD CONSTRAINT "uq_protocol_followup_protocol_after" UNIQUE ("protocol_id", "after_session_number");

ALTER TABLE core."protocol_followup" DROP CONSTRAINT IF EXISTS "fk_protocol_followup_instance_id";
ALTER TABLE core."protocol_followup" ADD CONSTRAINT "fk_protocol_followup_instance_id"
    FOREIGN KEY ("instance_id") REFERENCES core."protocol_instances" ("instance_id") ON DELETE RESTRICT;

ALTER TABLE core."protocol_followup" DROP CONSTRAINT IF EXISTS "fk_protocol_followup_protocol_id";
ALTER TABLE core."protocol_followup" ADD CONSTRAINT "fk_protocol_followup_protocol_id"
    FOREIGN KEY ("protocol_id") REFERENCES core."protocol_plan" ("protocol_id") ON DELETE RESTRICT;

ALTER TABLE core."protocol_followup" DROP CONSTRAINT IF EXISTS "fk_protocol_followup_appointment_id";
ALTER TABLE core."protocol_followup" ADD CONSTRAINT "fk_protocol_followup_appointment_id"
    FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_protocol_followup_instance ON core."protocol_followup" USING btree ("instance_id");
CREATE INDEX IF NOT EXISTS idx_protocol_followup_protocol ON core."protocol_followup" USING btree ("protocol_id");

DROP TRIGGER IF EXISTS trg_protocol_followup_updated_at ON core."protocol_followup";
CREATE TRIGGER trg_protocol_followup_updated_at
    BEFORE UPDATE ON core."protocol_followup"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();


-- ###########################################################################
-- §4  RLS
-- ###########################################################################
-- Same shape as 32's device_session_prs_responses / followup_prs_responses:
-- reachable through the appointment (clinic staff, the assigned doctor/CA) or
-- as the patient it belongs to. Write access matches protocol_plan's own
-- insert policy — a clinical assistant runs a device session but does not set
-- the protocol, so is deliberately not a writer here, mirroring _PRESCRIBERS
-- in treatment_protocols/router.py.

ALTER TABLE core."protocol_device_sessions" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."protocol_device_sessions" FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."protocol_followup"        ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."protocol_followup"        FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_protocol_device_sessions_select" ON core."protocol_device_sessions";
CREATE POLICY "rls_protocol_device_sessions_select" ON core."protocol_device_sessions" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR (appointment_id IN (
            SELECT a.appointment_id FROM appointments a
            WHERE a.patient_id = rls_user_id()
               OR a.clinic_id  = rls_clinic_id()
               OR a.doctor_id  = rls_user_id()
               OR a.ca_id      = rls_user_id()))
        OR (appointment_id IN (
            SELECT a.appointment_id FROM appointments a
            JOIN clinic_staff_assignments s ON s.clinic_id = a.clinic_id
            WHERE s.profile_id = rls_user_id() AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_protocol_device_sessions_insert" ON core."protocol_device_sessions";
CREATE POLICY "rls_protocol_device_sessions_insert" ON core."protocol_device_sessions" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'system'::text])
    );

DROP POLICY IF EXISTS "rls_protocol_device_sessions_update" ON core."protocol_device_sessions";
CREATE POLICY "rls_protocol_device_sessions_update" ON core."protocol_device_sessions" FOR UPDATE TO public
    USING (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'system'::text])
    );

DROP POLICY IF EXISTS "rls_protocol_followup_select" ON core."protocol_followup";
CREATE POLICY "rls_protocol_followup_select" ON core."protocol_followup" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR (appointment_id IN (
            SELECT a.appointment_id FROM appointments a
            WHERE a.patient_id = rls_user_id()
               OR a.clinic_id  = rls_clinic_id()
               OR a.doctor_id  = rls_user_id()
               OR a.ca_id      = rls_user_id()))
        OR (appointment_id IN (
            SELECT a.appointment_id FROM appointments a
            JOIN clinic_staff_assignments s ON s.clinic_id = a.clinic_id
            WHERE s.profile_id = rls_user_id() AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_protocol_followup_insert" ON core."protocol_followup";
CREATE POLICY "rls_protocol_followup_insert" ON core."protocol_followup" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'system'::text])
    );

DROP POLICY IF EXISTS "rls_protocol_followup_update" ON core."protocol_followup";
CREATE POLICY "rls_protocol_followup_update" ON core."protocol_followup" FOR UPDATE TO public
    USING (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'system'::text])
    );


-- ###########################################################################
-- §5  Grants
-- ###########################################################################
-- Same split every clinical table in this schema uses: the app reads, inserts
-- and updates; deletion belongs to the purge worker, never to a request.

GRANT SELECT, INSERT, UPDATE ON core."protocol_device_sessions", core."protocol_followup" TO anava_app;
REVOKE DELETE ON core."protocol_device_sessions", core."protocol_followup" FROM anava_app;
GRANT SELECT ON core."protocol_device_sessions", core."protocol_followup" TO anava_readonly;
-- Bucket 2 (patient data, anonymise never hard-delete): an erasure or
-- portability request has to be able to see what it is reporting on.
GRANT SELECT ON core."protocol_device_sessions", core."protocol_followup" TO anava_compliance;


COMMIT;


-- ###########################################################################
-- VERIFY — run after COMMIT
-- ###########################################################################
--
-- SELECT to_regclass('core.protocol_plan');              -- not null
-- SELECT to_regclass('core.treatment_protocols');         -- NULL (renamed away)
-- SELECT is_nullable FROM information_schema.columns
--  WHERE table_schema='core' AND table_name='protocol_plan'
--    AND column_name='instance_id';                       -- NO
-- SELECT count(*) FROM core.protocol_plan WHERE instance_id IS NULL; -- 0
-- SELECT count(*) FROM core.protocol_device_sessions;      -- unchanged by this file
-- SELECT count(*) FROM core.protocol_followup;             -- unchanged by this file
--
--
-- ###########################################################################
-- STILL OPEN AFTER THIS FILE
-- ###########################################################################
--
--   1. ProtocolService._generate_appointments (treatment_protocols/service.py)
--      writes appointments rows only. It must also insert one
--      protocol_device_sessions row and one protocol_followup row per
--      appointment it creates, in the same transaction — see the matching
--      backend commit.
--
--   2. Every raw-SQL "FROM treatment_protocols" / "treatment_protocols tp" in
--      the Python layer (repository.py's _PROTOCOL_SELECT and CRUD helpers,
--      scheduling/repository.py's is_used_by_a_protocol,
--      workers/retention_purge.py's DATA_CATEGORIES entry and its join) must
--      move to protocol_plan, and the plan_id-only joins those last two use
--      should move to the instance_id path now that it is guaranteed present
--      — see the matching backend commit for both.
-- ###########################################################################
