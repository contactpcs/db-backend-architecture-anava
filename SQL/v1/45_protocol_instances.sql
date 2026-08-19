-- 45_protocol_instances.sql
--
-- Separates the Treatment PROTOCOL from the Treatment PLAN.
--
-- SCOPE: one new table (core.protocol_instances), two columns and one relaxed
-- NOT NULL on core.treatment_protocols, and the RLS policy that has to change
-- with them. Nothing is dropped.
--
-- APPLY ORDER: after 44.
--
-- NOT YET EXECUTED ANYWHERE.
--
--
-- ###########################################################################
-- WHY
-- ###########################################################################
--
-- A treatment PLAN and a treatment PROTOCOL are different clinical objects:
--
--   Treatment Plan      the whole clinical picture — anamnesis, medical
--                       history, assessments, the doctor's reasoning.
--   Treatment Protocol  the device prescription — which machine, which
--                       montage, what dose, how many sessions.
--
-- The schema has never said so. core.treatment_plans currently carries
-- device_type, protocol_details, sessions_prescribed, standard_sessions and
-- extended_sessions — protocol facts living on the plan row — while
-- core.treatment_protocols carries device_id, session_count and the dosing
-- FKs. The same fact is representable in two places, and treatment_protocols
-- .plan_id NOT NULL forces a protocol to be created underneath a plan even
-- when no plan is what the doctor is authoring.
--
-- This file adds the missing middle object. A protocol instance is one course
-- of device treatment: it belongs to a CYCLE (the episode of care), knows the
-- patient, the doctor who created it, when, and which numbered attempt it is.
--
--   core.treatment_cycles      episode of care — one active per patient
--       └─ core.protocol_instances   ← NEW. one course of device treatment
--             └─ core.treatment_protocols   the prescription itself
--                   └─ core.appointments    device_session + protocol_followup
--
-- treatment_plans stays exactly as it is, still hanging off the cycle, now
-- free to mean what its name says. Its device columns become vestigial rather
-- than dropped — 05's treatment_sessions partitions FK into that table, and
-- dropping columns other modules still read is a separate, riskier change.
--
--
-- ###########################################################################
-- WHAT THIS FILE DELIBERATELY DOES NOT DO
-- ###########################################################################
--
-- It does not create core.protocol_sessions or core.protocol_followups. The
-- older ERD documents them as join tables between a protocol and an
-- appointment, but they were never built, and 30/31/41 replaced that design
-- with the appointments spine: a device session IS an appointment row with
-- appointment_type = 'device_session'. Four separate pieces of work already
-- depend on that spine —
--
--     core.device_session_prs_responses.appointment_id        (32)
--     core.appointments.clinic_device_id + capacity index     (41)
--     the hold sweeper's delete-vs-revert on plan_id IS NULL  (31)
--     core.fn_generate_protocol_sessions                      (41)
--
-- — so reintroducing the join tables would mean unwinding all of it to gain
-- nothing the instance concept does not already provide.
--
-- It also does not drop treatment_plans.device_type and friends. See above.


BEGIN;


-- ###########################################################################
-- 1  core.protocol_instances
-- ###########################################################################

CREATE TABLE IF NOT EXISTS core."protocol_instances" (
    "instance_id"      UUID NOT NULL DEFAULT gen_random_uuid(),
    "cycle_id"         UUID NOT NULL,
    "patient_id"       UUID NOT NULL,
    "created_by"       UUID NOT NULL,
    -- Which numbered course this is within the cycle. Not a global counter:
    -- "this patient's 2nd protocol" is only meaningful inside one episode of
    -- care, and a new cycle legitimately starts again at 1.
    "instance_number"  INTEGER NOT NULL DEFAULT 1,
    "status"           TEXT NOT NULL DEFAULT 'draft',
    "notes"            TEXT,
    "created_at"       TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"       TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE core."protocol_instances" IS
    'One course of device treatment within a treatment cycle. The parent of treatment_protocols, and the object the Treatment Protocol UI actually lists. Distinct from treatment_plans, which is the clinical picture (anamnesis, history, assessments) rather than the device prescription. Retention: Bucket 3 — part of the prescribing record, deactivate rather than delete.';
COMMENT ON COLUMN core."protocol_instances"."cycle_id" IS
    'The episode of care this course belongs to. Instances hang off the cycle, not off treatment_plans — a protocol can be authored without a plan existing.';
COMMENT ON COLUMN core."protocol_instances"."patient_id" IS
    'References core.profiles(id), matching the convention in NOTES.md: patient_id-shaped FK columns resolve to profiles.id, not patients.patient_id. Denormalised from the cycle so RLS and the patient timeline do not join through treatment_cycles on every read.';
COMMENT ON COLUMN core."protocol_instances"."created_by" IS
    'The doctor who opened this course. Distinct from treatment_protocols.set_by, which records who set each individual prescription — an amendment may be authored by a different doctor than the one who opened the instance.';
COMMENT ON COLUMN core."protocol_instances"."instance_number" IS
    'Sequential within the cycle, starting at 1. Unique per (cycle_id, instance_number) so two concurrent requests cannot both claim the same number.';

ALTER TABLE core."protocol_instances" DROP CONSTRAINT IF EXISTS "protocol_instances_pkey" CASCADE;
ALTER TABLE core."protocol_instances" ADD CONSTRAINT "protocol_instances_pkey" PRIMARY KEY ("instance_id");

ALTER TABLE core."protocol_instances" DROP CONSTRAINT IF EXISTS "uq_protocol_instances_cycle_number";
ALTER TABLE core."protocol_instances"
    ADD CONSTRAINT "uq_protocol_instances_cycle_number" UNIQUE ("cycle_id", "instance_number");

ALTER TABLE core."protocol_instances" DROP CONSTRAINT IF EXISTS "chk_protocol_instances_number";
ALTER TABLE core."protocol_instances"
    ADD CONSTRAINT "chk_protocol_instances_number" CHECK ("instance_number" >= 1);

-- Text + CHECK, never a native enum (v2 Layer 2): ALTER TYPE can never remove
-- a value. Same five-state vocabulary treatment_protocols already uses, so the
-- instance and its prescription can be reasoned about together.
ALTER TABLE core."protocol_instances" DROP CONSTRAINT IF EXISTS "chk_protocol_instances_status";
ALTER TABLE core."protocol_instances"
    ADD CONSTRAINT "chk_protocol_instances_status"
    CHECK ("status" IN ('draft', 'active', 'completed', 'cancelled', 'superseded'));

ALTER TABLE core."protocol_instances" DROP CONSTRAINT IF EXISTS "fk_protocol_instances_cycle_id";
ALTER TABLE core."protocol_instances" ADD CONSTRAINT "fk_protocol_instances_cycle_id"
    FOREIGN KEY ("cycle_id") REFERENCES core."treatment_cycles" ("cycle_id") ON DELETE RESTRICT;

ALTER TABLE core."protocol_instances" DROP CONSTRAINT IF EXISTS "fk_protocol_instances_patient_id";
ALTER TABLE core."protocol_instances" ADD CONSTRAINT "fk_protocol_instances_patient_id"
    FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

ALTER TABLE core."protocol_instances" DROP CONSTRAINT IF EXISTS "fk_protocol_instances_created_by";
ALTER TABLE core."protocol_instances" ADD CONSTRAINT "fk_protocol_instances_created_by"
    FOREIGN KEY ("created_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_protocol_instances_cycle
    ON core."protocol_instances" USING btree ("cycle_id");
CREATE INDEX IF NOT EXISTS idx_protocol_instances_patient
    ON core."protocol_instances" USING btree ("patient_id", "created_at" DESC);

-- One live course per cycle. A second concurrent instance would generate a
-- second set of device sessions against the same episode of care, which is
-- the calendar equivalent of prescribing twice.
DROP INDEX IF EXISTS core.uq_protocol_instances_one_active;
CREATE UNIQUE INDEX uq_protocol_instances_one_active
    ON core."protocol_instances" ("cycle_id")
    WHERE "status" IN ('draft', 'active');

DROP TRIGGER IF EXISTS trg_protocol_instances_updated_at ON core."protocol_instances";
CREATE TRIGGER trg_protocol_instances_updated_at
    BEFORE UPDATE ON core."protocol_instances"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();


-- ###########################################################################
-- 2  Re-parent core.treatment_protocols
-- ###########################################################################
-- instance_id becomes the real parent. plan_id stays — a protocol authored
-- alongside a plan should still record which one — but stops being mandatory,
-- which is the whole point: a device prescription no longer requires a
-- clinical plan row to exist first.

ALTER TABLE core."treatment_protocols"
    ADD COLUMN IF NOT EXISTS "instance_id" UUID;

COMMENT ON COLUMN core."treatment_protocols"."instance_id" IS
    'The protocol instance (course of device treatment) this prescription belongs to. Nullable only so this migration can run against existing rows; see §3 — new rows must set it.';
COMMENT ON COLUMN core."treatment_protocols"."plan_id" IS
    'The clinical treatment plan this prescription accompanies, when there is one. No longer required: a protocol hangs off protocol_instances, and a plan is the anamnesis/history record rather than the device prescription. Still load-bearing for appointments — every protocol-born appointment row copies plan_id, and 31''s hold sweeper decides delete-vs-revert on plan_id IS NULL.';

ALTER TABLE core."treatment_protocols"
    DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_instance_id";
ALTER TABLE core."treatment_protocols"
    ADD CONSTRAINT "fk_treatment_protocols_instance_id"
    FOREIGN KEY ("instance_id") REFERENCES core."protocol_instances" ("instance_id") ON DELETE RESTRICT
    NOT VALID;

CREATE INDEX IF NOT EXISTS idx_treatment_protocols_instance
    ON core."treatment_protocols" USING btree ("instance_id")
    WHERE "instance_id" IS NOT NULL;

-- treatment_protocols is empty in every environment as of this file (the
-- create path has never completed successfully), so relaxing NOT NULL costs
-- nothing and rewrites no rows.
ALTER TABLE core."treatment_protocols" ALTER COLUMN "plan_id" DROP NOT NULL;

-- A protocol must hang off SOMETHING. Both nullable columns with no rule
-- would allow an orphan prescription belonging to no course and no plan.
ALTER TABLE core."treatment_protocols"
    DROP CONSTRAINT IF EXISTS "chk_treatment_protocols_has_parent";
ALTER TABLE core."treatment_protocols"
    ADD CONSTRAINT "chk_treatment_protocols_has_parent"
    CHECK (num_nonnulls("instance_id", "plan_id") >= 1)
    NOT VALID;


-- ###########################################################################
-- 3  RLS — the part that would fail silently if it were missed
-- ###########################################################################
-- rls_treatment_protocols_select (32) scopes ENTIRELY through plan_id:
-- every branch is `plan_id IN (SELECT ... FROM treatment_plans ...)`. The
-- moment plan_id is nullable, a protocol carrying only instance_id matches no
-- branch and is invisible to every role — including the doctor who wrote it.
--
-- Under RLS that is not an error. SELECT returns zero rows and the UI shows an
-- empty state, which is indistinguishable from "no protocol exists" — exactly
-- the failure this module has already spent a day chasing. The policy is
-- rewritten here, in the same transaction that makes the column nullable.

DROP POLICY IF EXISTS "rls_treatment_protocols_select" ON core."treatment_protocols";
CREATE POLICY "rls_treatment_protocols_select" ON core."treatment_protocols" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        -- Reachable through the instance: the new path.
        OR (instance_id IN (
            SELECT i.instance_id
            FROM protocol_instances i
            JOIN treatment_cycles c ON c.cycle_id = i.cycle_id
            WHERE c.clinic_id = rls_clinic_id()
               OR i.patient_id = rls_user_id()
               OR i.created_by = rls_user_id()
               OR c.doctor_id  = rls_user_id()))
        OR (instance_id IN (
            SELECT i.instance_id
            FROM protocol_instances i
            JOIN treatment_cycles c          ON c.cycle_id = i.cycle_id
            JOIN clinic_staff_assignments s  ON s.clinic_id = c.clinic_id
            WHERE s.profile_id = rls_user_id()
              AND s.is_active))
        -- Reachable through the plan: preserved verbatim from 32, so a
        -- protocol created before this file stays visible to exactly whom it
        -- was visible to before.
        OR (plan_id IN (
            SELECT p.plan_id FROM treatment_plans p
            JOIN treatment_cycles c ON c.cycle_id = p.cycle_id
            WHERE c.clinic_id = rls_clinic_id()
               OR p.patient_id = rls_user_id()
               OR p.doctor_id  = rls_user_id()))
        OR (plan_id IN (
            SELECT p.plan_id
            FROM treatment_plans p
            JOIN treatment_cycles c         ON c.cycle_id  = p.cycle_id
            JOIN clinic_staff_assignments s ON s.clinic_id = c.clinic_id
            WHERE s.profile_id = rls_user_id()
              AND s.is_active))
    );


-- ###########################################################################
-- 3b  The device-availability trigger also resolves clinic through plan_id
-- ###########################################################################
-- Same class of breakage as the RLS policy in §3, and found the same way:
-- fn_check_device_available_at_clinic (37) resolves the clinic by joining
-- treatment_plans -> treatment_cycles on NEW.plan_id, then RAISEs if that
-- comes back NULL. With plan_id nullable, every instance-parented protocol
-- fails at INSERT with
--
--     Cannot resolve a clinic for plan <NULL> - protocol cannot be validated
--
-- Unlike the RLS case this one is loud, but it blocks the write entirely.
-- Replaced here so the clinic is resolved from whichever parent the protocol
-- actually has: the instance first (the new path), the plan second (rows
-- written before this file). Everything after the resolution step is
-- unchanged from 37.

CREATE OR REPLACE FUNCTION core.fn_check_device_available_at_clinic()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_clinic_id UUID;
    v_available BOOLEAN;
BEGIN
    IF NEW.instance_id IS NOT NULL THEN
        SELECT tc.clinic_id INTO v_clinic_id
        FROM core.protocol_instances pi
        JOIN core.treatment_cycles tc ON tc.cycle_id = pi.cycle_id
        WHERE pi.instance_id = NEW.instance_id;
    END IF;

    IF v_clinic_id IS NULL AND NEW.plan_id IS NOT NULL THEN
        SELECT tc.clinic_id INTO v_clinic_id
        FROM core.treatment_plans tp
        JOIN core.treatment_cycles tc ON tc.cycle_id = tp.cycle_id
        WHERE tp.plan_id = NEW.plan_id;
    END IF;

    IF v_clinic_id IS NULL THEN
        RAISE EXCEPTION 'Cannot resolve a clinic for protocol (instance %, plan %) — protocol cannot be validated',
            NEW.instance_id, NEW.plan_id
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
        RAISE EXCEPTION 'Device % is not available at clinic %', NEW.device_id, v_clinic_id
            USING ERRCODE = '23514',
                  HINT = 'Add the device to this clinic''s inventory, or prescribe one the clinic has.';
    END IF;

    RETURN NEW;
END;
$function$;


-- ###########################################################################
-- 3c  chk_appointments_device_session_has_plan says plan, means protocol
-- ###########################################################################
-- 30 wrote:
--
--     -- A device session always belongs to a protocol.
--     CHECK ("appointment_type" <> 'device_session' OR "plan_id" IS NOT NULL)
--
-- The comment says PROTOCOL; the predicate tests plan_id, because when 30 was
-- written the plan was the only parent a protocol could have, so the two were
-- the same statement. 45 separates them, and the constraint now blocks every
-- device session generated for an instance-parented protocol:
--
--     new row for relation "appointments" violates check constraint
--     "chk_appointments_device_session_has_plan"
--
-- Rewritten to test what the comment always meant. protocol_id is the honest
-- column: a device session is generated by a protocol, and 32's
-- chk_appointments_protocol_pair already requires protocol_id and
-- session_number to arrive together.
--
-- ⚠ CONSEQUENCE FOR THE HOLD SWEEPER. 31's sweeper decides delete-vs-revert on
-- plan_id IS NULL: rows with a plan are reverted to 'planned', rows without
-- are DELETED as abandoned self-service bookings. A protocol-born row with no
-- plan_id would now be deleted on a payment timeout, destroying the doctor's
-- prescribed date. The sweeper's predicate is widened here to match: a row
-- carrying protocol_id is prescribed work regardless of plan_id, and is
-- reverted, never deleted.

ALTER TABLE core."appointments"
    DROP CONSTRAINT IF EXISTS "chk_appointments_device_session_has_plan";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_device_session_has_protocol"
    CHECK ("appointment_type" <> 'device_session' OR "protocol_id" IS NOT NULL)
    NOT VALID;

-- 32 added the same rule again, one level up, for protocol-born follow-ups as
-- well as sessions:
--
--     -- a protocol-born row must carry plan_id, or an expired hold deletes
--     -- the doctor's prescribed date instead of releasing the slot.
--     CHECK ((protocol_id IS NULL) OR (plan_id IS NOT NULL))
--
-- That reasoning is exactly right, and plan_id was the correct proxy for it
-- while a protocol could not exist without a plan. It is not a rule about
-- plans at all — it is "the sweeper must never delete prescribed work". The
-- sweeper now tests that property directly (protocol_id IS NOT NULL reverts,
-- never deletes; see app/workers/hold_sweeper.py and
-- scheduling/repository.py), so the proxy can go without weakening anything.
--
-- Dropped rather than rewritten: chk_appointments_device_session_has_protocol
-- above already requires protocol_id on every device session, and 32's
-- chk_appointments_protocol_pair still pairs protocol_id with session_number.
ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "chk_appointments_protocol_has_plan";

-- ###########################################################################
-- 4  RLS for the new table
-- ###########################################################################
-- Mirrors treatment_protocols: staff of the clinic and the patient may read,
-- only prescribing roles may write.

ALTER TABLE core."protocol_instances" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."protocol_instances" FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_protocol_instances_select" ON core."protocol_instances";
CREATE POLICY "rls_protocol_instances_select" ON core."protocol_instances" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR (patient_id = rls_user_id())
        OR (created_by = rls_user_id())
        OR (cycle_id IN (
            SELECT c.cycle_id FROM treatment_cycles c
            WHERE c.clinic_id = rls_clinic_id()
               OR c.doctor_id = rls_user_id()))
        OR (cycle_id IN (
            SELECT c.cycle_id
            FROM treatment_cycles c
            JOIN clinic_staff_assignments s ON s.clinic_id = c.clinic_id
            WHERE s.profile_id = rls_user_id()
              AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_protocol_instances_insert" ON core."protocol_instances";
CREATE POLICY "rls_protocol_instances_insert" ON core."protocol_instances" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text])
    );

DROP POLICY IF EXISTS "rls_protocol_instances_update" ON core."protocol_instances";
CREATE POLICY "rls_protocol_instances_update" ON core."protocol_instances" FOR UPDATE TO public
    USING (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'system'::text])
    );


-- ###########################################################################
-- 5  Grants
-- ###########################################################################
-- Same split 32 applies to every clinical table: the app reads, inserts and
-- updates; deletion belongs to the purge worker, never to a request.

GRANT SELECT, INSERT, UPDATE ON core."protocol_instances" TO anava_app;
REVOKE DELETE ON core."protocol_instances" FROM anava_app;
GRANT SELECT ON core."protocol_instances" TO anava_readonly;
-- Bucket 2 (patient data, anonymise never hard-delete): an erasure or
-- portability request has to be able to see what it is reporting on.
GRANT SELECT ON core."protocol_instances" TO anava_compliance;


-- ###########################################################################
-- 6  Validate
-- ###########################################################################
-- Both constraints on the pre-existing table landed NOT VALID (v2 Layer 6: no
-- ACCESS EXCLUSIVE lock plus full scan during clinic hours).
-- treatment_protocols is empty, so validating is instant; if it has grown by
-- apply time these two lines move to a low-traffic window on their own.

ALTER TABLE core."treatment_protocols" VALIDATE CONSTRAINT "fk_treatment_protocols_instance_id";
ALTER TABLE core."treatment_protocols" VALIDATE CONSTRAINT "chk_treatment_protocols_has_parent";


COMMIT;


-- ###########################################################################
-- VERIFY — run after COMMIT
-- ###########################################################################
--
-- SELECT count(*) FROM core.protocol_instances;                      -- 0
-- SELECT is_nullable FROM information_schema.columns
--  WHERE table_schema='core' AND table_name='treatment_protocols'
--    AND column_name='plan_id';                                      -- YES
-- SELECT column_name FROM information_schema.columns
--  WHERE table_schema='core' AND table_name='treatment_protocols'
--    AND column_name='instance_id';                                  -- 1 row
--
-- The RLS rewrite is the one that cannot be checked by counting rows. As
-- anava_app with a doctor context set, a protocol carrying only instance_id
-- must be SELECTable; if §3 were skipped it would silently return nothing.
--
--
-- ###########################################################################
-- STILL OPEN AFTER THIS FILE
-- ###########################################################################
--
--   1. The backend writes none of this yet. ProtocolService.create still
--      requires plan_id and sets no instance_id, and nothing creates a
--      protocol_instances row. That wiring is the next task.
--
--   2. ProtocolService._generate_appointments does not set
--      appointments.clinic_device_id, which 41 made mandatory for every
--      device_session row (chk_appointments_device_session_has_device). The
--      protocol row inserts, the first session insert fails, and the whole
--      transaction rolls back — which is why treatment_protocols is empty.
--      41's own core.fn_generate_protocol_sessions already shows the fix:
--      resolve clinic_device_id from clinic_id + protocol.device_id.
--
--   3. treatment_plans.device_type / protocol_details / sessions_prescribed /
--      standard_sessions / extended_sessions are now vestigial. Left in place
--      deliberately: core.treatment_sessions (partitioned) FKs into that
--      table, and clearing columns other modules may still read is a separate
--      change with its own blast radius.
-- ###########################################################################
