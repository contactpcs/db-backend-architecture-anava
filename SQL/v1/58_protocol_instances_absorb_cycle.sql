-- 58_protocol_instances_absorb_cycle.sql
--
-- Retires core.treatment_cycles, core.treatment_plans and
-- core.treatment_sessions. Their one still-load-bearing fact — patient,
-- doctor, CA, clinic, and "which numbered course is this" — moves onto
-- core.protocol_instances directly. protocol_instances stops being "a course
-- within a cycle" and becomes the episode of care itself: the single object
-- a doctor opens, prescribes against, and closes out. No more two-step
-- open-a-cycle-then-open-an-instance; one row, one API call.
--
-- SCOPE: protocol_instances gains doctor_id/ca_id/clinic_id/instance_type and
-- loses cycle_id. Every other table that pointed at treatment_cycles either
-- gets its cycle_id column dropped (four tables where it was legacy/nullable
-- and unused — anamnesis_assessments, assessment_protocol_requests,
-- prs_assessment_instances, patient_medical_history_files; a fifth,
-- appointment_requests, would have been in this list too but 34 already
-- dropped that whole table on 2026-08-13) or repointed at protocol_instances
-- (appointments, patient_clinic_transfers, device_assignments, store_orders).
-- sessions / doctor_session_notes / patient_eeg_files are dead tables (0 rows,
-- no code paths — see commit b341817) that also FK into treatment_cycles;
-- this file strips just that dangling FK from each so treatment_cycles can be
-- dropped, and otherwise leaves those three tables untouched — dropping them
-- is a separate decision, not part of this one.
--
-- APPLY ORDER: after 57.
--
--

-- ⚠ If your session already failed a statement from an earlier attempt at
-- this file, Postgres is holding an aborted transaction and will answer
-- every command below with
--     ERROR: current transaction is aborted, commands ignored until end of
--     transaction block  (SQLSTATE 25P02)
-- That is not this file failing — nothing here has run yet. Issue ROLLBACK;
-- once, then run this file. (Same note as 50_protocol_child_select_rls.sql.)
ROLLBACK;

BEGIN;


-- ###########################################################################
-- 1  protocol_instances absorbs the episode fields
-- ###########################################################################

ALTER TABLE core."protocol_instances" ADD COLUMN IF NOT EXISTS "doctor_id" UUID;
ALTER TABLE core."protocol_instances" ADD COLUMN IF NOT EXISTS "ca_id" UUID;
ALTER TABLE core."protocol_instances" ADD COLUMN IF NOT EXISTS "clinic_id" UUID;
ALTER TABLE core."protocol_instances" ADD COLUMN IF NOT EXISTS "instance_type" TEXT NOT NULL DEFAULT 'initial';

-- Defensive backfill in case any environment already has rows (every prior
-- file's own comments say protocol_instances/protocol_plan are empty
-- everywhere as of 45/48 — this costs nothing if that still holds).
UPDATE core."protocol_instances" pi
SET "doctor_id"     = tc."doctor_id",
    "ca_id"          = tc."ca_id",
    "clinic_id"      = tc."clinic_id",
    "instance_type"  = tc."cycle_type"
FROM core."treatment_cycles" tc
WHERE tc."cycle_id" = pi."cycle_id"
  AND pi."doctor_id" IS NULL;

ALTER TABLE core."protocol_instances" ALTER COLUMN "doctor_id" SET NOT NULL;
ALTER TABLE core."protocol_instances" ALTER COLUMN "clinic_id" SET NOT NULL;

COMMENT ON COLUMN core."protocol_instances"."doctor_id" IS
    'References core.profiles(id) (NOTES.md convention). The doctor responsible for this episode of care — absorbed from treatment_cycles.doctor_id, which no longer exists.';
COMMENT ON COLUMN core."protocol_instances"."ca_id" IS
    'References core.profiles(id). The clinical assistant supporting this episode, if any — absorbed from treatment_cycles.ca_id.';
COMMENT ON COLUMN core."protocol_instances"."clinic_id" IS
    'References core.clinics(clinic_id). Absorbed from treatment_cycles.clinic_id — every RLS policy and trigger that used to resolve clinic by joining through treatment_cycles now reads this column directly.';
COMMENT ON COLUMN core."protocol_instances"."instance_type" IS
    'initial | followup. Absorbed from treatment_cycles.cycle_type. Unconstrained TEXT like every other status/type column in this schema (v2 Layer 2: app-enforced vocab, no CHECK, so the value set can grow without a migration).';

-- instance_number was "which course within this cycle" (resets to 1 per
-- cycle). With no cycle, it becomes "which episode this patient has ever had"
-- — a flat, patient-global sequence. Nothing about the column itself changes,
-- only what it counts.
ALTER TABLE core."protocol_instances" DROP CONSTRAINT IF EXISTS "uq_protocol_instances_cycle_number";
ALTER TABLE core."protocol_instances" DROP CONSTRAINT IF EXISTS "uq_protocol_instances_patient_number";
ALTER TABLE core."protocol_instances"
    ADD CONSTRAINT "uq_protocol_instances_patient_number" UNIQUE ("patient_id", "instance_number");

COMMENT ON COLUMN core."protocol_instances"."instance_number" IS
    'Sequential per patient, starting at 1 — this patient''s 1st, 2nd, 3rd... episode of care. No longer scoped to a cycle (58): a new episode does not restart the count.';

-- "One active episode per patient" replaces "one active course per cycle" —
-- the same rule treatment_cycles used to enforce (one active cycle per
-- patient) and protocol_instances used to enforce separately (one open
-- instance per cycle). Folding the two objects into one folds the two rules
-- into one too.
DROP INDEX IF EXISTS core.uq_protocol_instances_one_active;
CREATE UNIQUE INDEX uq_protocol_instances_one_active
    ON core."protocol_instances" ("patient_id")
    WHERE "status" IN ('draft', 'active');

-- cycle_id itself is NOT dropped here yet — rls_protocol_instances_select and
-- rls_protocol_plan_select (§2 below) still reference it via the old policy
-- bodies from 45/49, and DROP COLUMN on a column a policy depends on fails
-- outright ("cannot drop column ... because other objects depend on it").
-- The column drop moves to the end of §2, once those policies no longer
-- mention it.

ALTER TABLE core."protocol_instances" DROP CONSTRAINT IF EXISTS "fk_protocol_instances_doctor_id";
ALTER TABLE core."protocol_instances" ADD CONSTRAINT "fk_protocol_instances_doctor_id"
    FOREIGN KEY ("doctor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

ALTER TABLE core."protocol_instances" DROP CONSTRAINT IF EXISTS "fk_protocol_instances_ca_id";
ALTER TABLE core."protocol_instances" ADD CONSTRAINT "fk_protocol_instances_ca_id"
    FOREIGN KEY ("ca_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

ALTER TABLE core."protocol_instances" DROP CONSTRAINT IF EXISTS "fk_protocol_instances_clinic_id";
ALTER TABLE core."protocol_instances" ADD CONSTRAINT "fk_protocol_instances_clinic_id"
    FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_protocol_instances_clinic ON core."protocol_instances" USING btree ("clinic_id");
CREATE INDEX IF NOT EXISTS idx_protocol_instances_doctor ON core."protocol_instances" USING btree ("doctor_id");

COMMENT ON TABLE core."protocol_instances" IS
    'One episode of care, opened by a doctor for a patient at a clinic. The parent of core.protocol_plan (one or more prescriptions, amendments superseding earlier ones) and, as of 58, the sole surviving object from the old treatment_cycles/treatment_plans/treatment_sessions hierarchy — there is no separate "cycle" any more. Retention: Bucket 3 — part of the prescribing record, deactivate rather than delete.';


-- ###########################################################################
-- 2  RLS — protocol_instances and protocol_plan no longer need to join out
-- ###########################################################################
-- Both policies used to reach clinic_id/doctor_id through
-- protocol_instances -> treatment_cycles. Those columns are now local to
-- protocol_instances, so the join disappears rather than getting repointed.

DROP POLICY IF EXISTS "rls_protocol_instances_select" ON core."protocol_instances";
CREATE POLICY "rls_protocol_instances_select" ON core."protocol_instances" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR ("patient_id" = rls_user_id())
        OR ("created_by" = rls_user_id())
        OR ("doctor_id" = rls_user_id())
        OR ("ca_id" = rls_user_id())
        OR ("clinic_id" = rls_clinic_id())
        OR ("clinic_id" IN (
            SELECT s.clinic_id FROM clinic_staff_assignments s
            WHERE s.profile_id = rls_user_id() AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_protocol_plan_select" ON core."protocol_plan";
CREATE POLICY "rls_protocol_plan_select" ON core."protocol_plan" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR (instance_id IN (
            SELECT i.instance_id
            FROM protocol_instances i
            WHERE i.clinic_id = rls_clinic_id()
               OR i.patient_id = rls_user_id()
               OR i.created_by = rls_user_id()
               OR i.doctor_id  = rls_user_id()
               OR i.ca_id      = rls_user_id()))
        OR (instance_id IN (
            SELECT i.instance_id
            FROM protocol_instances i
            JOIN clinic_staff_assignments s ON s.clinic_id = i.clinic_id
            WHERE s.profile_id = rls_user_id()
              AND s.is_active))
    );

-- fn_check_device_available_at_clinic (37, rewritten 45 §3b, 49) resolved the
-- clinic through the same join. protocol_instances.clinic_id makes that a
-- flat lookup.
CREATE OR REPLACE FUNCTION core.fn_check_device_available_at_clinic()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_clinic_id UUID;
    v_available BOOLEAN;
BEGIN
    SELECT pi.clinic_id INTO v_clinic_id
    FROM core.protocol_instances pi
    WHERE pi.instance_id = NEW.instance_id;

    IF v_clinic_id IS NULL THEN
        RAISE EXCEPTION 'Cannot resolve a clinic for protocol instance % — protocol cannot be validated',
            NEW.instance_id
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

-- Now safe: no policy or function references protocol_instances.cycle_id
-- any more.
ALTER TABLE core."protocol_instances" DROP CONSTRAINT IF EXISTS "fk_protocol_instances_cycle_id";
ALTER TABLE core."protocol_instances" DROP COLUMN IF EXISTS "cycle_id";


-- ###########################################################################
-- 3  Four tables lose a legacy, nullable, unused cycle_id
-- ###########################################################################
-- assessment_protocol_requests and prs_assessment_instances have a SELECT
-- policy branch keyed on it; the other two (anamnesis_assessments,
-- patient_medical_history_files) already scope by their own clinic_id/
-- patient_id and never referenced cycle_id in any policy. (A fifth table,
-- appointment_requests, would have been here too, but 34 already dropped it
-- entirely on 2026-08-13 — nothing to do for it.) Policies are rewritten in
-- the same transaction as the DROP COLUMN — relying on CASCADE here would
-- silently leave the table with zero SELECT policies, the exact failure 45
-- §3 and 49 already documented once each.

DROP POLICY IF EXISTS "rls_apr_select" ON core."assessment_protocol_requests";
CREATE POLICY "rls_apr_select" ON core."assessment_protocol_requests" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (clinical_assistant_id = rls_user_id())
        OR (doctor_id = rls_user_id())
        OR (patient_id = rls_user_id())
    );
ALTER TABLE core."assessment_protocol_requests" DROP CONSTRAINT IF EXISTS "fk_assessment_protocol_requests_cycle_id";
ALTER TABLE core."assessment_protocol_requests" DROP COLUMN IF EXISTS "cycle_id";

DROP POLICY IF EXISTS "rls_pai_select" ON core."prs_assessment_instances";
CREATE POLICY "rls_pai_select" ON core."prs_assessment_instances" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (patient_id = rls_user_id())
        OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text]))
            AND (patient_id IN (
                SELECT patients.profile_id FROM patients
                WHERE patients.primary_clinic_id = rls_clinic_id())))
    );
-- Three child tables reach cycle_id one level removed, through a subquery on
-- prs_assessment_instances rather than a column of their own — missed on the
-- first pass because grepping prs_assessment_instances's own policies doesn't
-- find policies defined on a *different* table that merely queries it.
DROP POLICY IF EXISTS "rls_pfr_select" ON core."prs_final_results";
CREATE POLICY "rls_pfr_select" ON core."prs_final_results" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (instance_id IN (
            SELECT prs_assessment_instances.instance_id FROM prs_assessment_instances
            WHERE prs_assessment_instances.patient_id = rls_user_id()))
        OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text]))
            AND (instance_id IN (
                SELECT prs_assessment_instances.instance_id FROM prs_assessment_instances
                WHERE prs_assessment_instances.patient_id IN (
                    SELECT patients.profile_id FROM patients WHERE patients.primary_clinic_id = rls_clinic_id()))))
    );

DROP POLICY IF EXISTS "rls_prs_resp_select" ON core."prs_responses";
CREATE POLICY "rls_prs_resp_select" ON core."prs_responses" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (instance_id IN (
            SELECT prs_assessment_instances.instance_id FROM prs_assessment_instances
            WHERE prs_assessment_instances.patient_id = rls_user_id()))
        OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text]))
            AND (instance_id IN (
                SELECT prs_assessment_instances.instance_id FROM prs_assessment_instances
                WHERE prs_assessment_instances.patient_id IN (
                    SELECT patients.profile_id FROM patients WHERE patients.primary_clinic_id = rls_clinic_id()))))
    );

DROP POLICY IF EXISTS "rls_psr_select" ON core."prs_scale_results";
CREATE POLICY "rls_psr_select" ON core."prs_scale_results" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (instance_id IN (
            SELECT prs_assessment_instances.instance_id FROM prs_assessment_instances
            WHERE prs_assessment_instances.patient_id = rls_user_id()))
        OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text]))
            AND (instance_id IN (
                SELECT prs_assessment_instances.instance_id FROM prs_assessment_instances
                WHERE prs_assessment_instances.patient_id IN (
                    SELECT patients.profile_id FROM patients WHERE patients.primary_clinic_id = rls_clinic_id()))))
    );

ALTER TABLE core."prs_assessment_instances" DROP CONSTRAINT IF EXISTS "fk_prs_assessment_instances_cycle_id";
ALTER TABLE core."prs_assessment_instances" DROP COLUMN IF EXISTS "cycle_id";

-- appointment_requests would have been here (same shape as the two below) —
-- 34 already dropped the whole table on 2026-08-13, nothing left to do.

ALTER TABLE core."anamnesis_assessments" DROP CONSTRAINT IF EXISTS "fk_anamnesis_assessments_cycle_id";
ALTER TABLE core."anamnesis_assessments" DROP COLUMN IF EXISTS "cycle_id";

ALTER TABLE core."patient_medical_history_files" DROP CONSTRAINT IF EXISTS "fk_patient_medical_history_files_cycle_id";
ALTER TABLE core."patient_medical_history_files" DROP COLUMN IF EXISTS "cycle_id";


-- ###########################################################################
-- 4  Four tables repoint cycle_id / plan_id at protocol_instances
-- ###########################################################################

-- appointments.cycle_id: optional passthrough on the generic booking path
-- (scheduling/service.py), never set by the protocol-generated path. Renamed
-- in place, not dropped — the column's purpose (tag an appointment to an
-- episode) is unchanged, only what it points at.
ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "fk_appointments_cycle_id";
ALTER TABLE core."appointments" RENAME COLUMN "cycle_id" TO "instance_id";
ALTER TABLE core."appointments" ADD CONSTRAINT "fk_appointments_instance_id"
    FOREIGN KEY ("instance_id") REFERENCES core."protocol_instances" ("instance_id") ON DELETE RESTRICT;

-- appointments.plan_id (30) also FKs into treatment_plans and must go before
-- treatment_plans can be dropped. Unlike cycle_id this one is dead, not just
-- legacy: no code path has ever written it (_generate_appointments only sets
-- protocol_id), 50 already dropped the two CHECKs that used to require it,
-- and its one remaining reader — scheduling's responsible_doctor_id fallback
-- — is dropped in the same commit as this file, since a.doctor_id is always
-- set now that ProtocolInstanceService requires doctor_id. Dropped outright
-- rather than repointed.
ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "fk_appointments_plan_id";
ALTER TABLE core."appointments" DROP COLUMN IF EXISTS "plan_id";

-- patient_clinic_transfers.active_cycle_id: the episode that carries over to
-- the new clinic without restarting (patients/service.py PatientTransferService).
ALTER TABLE core."patient_clinic_transfers" DROP CONSTRAINT IF EXISTS "fk_patient_clinic_transfers_active_cycle_id";
ALTER TABLE core."patient_clinic_transfers" RENAME COLUMN "active_cycle_id" TO "active_instance_id";
ALTER TABLE core."patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_active_instance_id"
    FOREIGN KEY ("active_instance_id") REFERENCES core."protocol_instances" ("instance_id") ON DELETE RESTRICT;

-- device_assignments.plan_id (NOT NULL): the device-order gate in
-- store/service.py. Renamed and repointed at protocol_instances — the NOT
-- NULL is unaffected by a rename.
ALTER TABLE core."device_assignments" DROP CONSTRAINT IF EXISTS "fk_device_assignments_plan_id";
ALTER TABLE core."device_assignments" RENAME COLUMN "plan_id" TO "instance_id";
ALTER TABLE core."device_assignments" ADD CONSTRAINT "fk_device_assignments_instance_id"
    FOREIGN KEY ("instance_id") REFERENCES core."protocol_instances" ("instance_id") ON DELETE RESTRICT;

-- store_orders.treatment_plan_id: nullable, only meaningful for device orders.
ALTER TABLE core."store_orders" DROP CONSTRAINT IF EXISTS "fk_store_orders_treatment_plan_id";
ALTER TABLE core."store_orders" RENAME COLUMN "treatment_plan_id" TO "instance_id";
ALTER TABLE core."store_orders" ADD CONSTRAINT "fk_store_orders_instance_id"
    FOREIGN KEY ("instance_id") REFERENCES core."protocol_instances" ("instance_id") ON DELETE RESTRICT;


-- ###########################################################################
-- 5  Strip the dangling FK from the three dead tables
-- ###########################################################################
-- sessions, doctor_session_notes, patient_eeg_files are 0-row and have had no
-- code path writing or reading them since commit b341817. Not dropped here —
-- only the constraint that would otherwise block dropping treatment_cycles.

ALTER TABLE core."sessions" DROP CONSTRAINT IF EXISTS "fk_sessions_cycle_id";
ALTER TABLE core."doctor_session_notes" DROP CONSTRAINT IF EXISTS "fk_doctor_session_notes_cycle_id";
ALTER TABLE core."patient_eeg_files" DROP CONSTRAINT IF EXISTS "fk_patient_eeg_files_cycle_id";

-- doctor_session_notes.cycle_id itself is untouched (out of scope, table left
-- otherwise as-is) — but its own SELECT policy reaches through the treatment_
-- cycles table with a subquery, and §6's DROP TABLE ... CASCADE would take
-- that policy down silently along with treatment_cycles (no error, just a
-- table that quietly loses its SELECT policy — the exact "silent empty
-- result" failure this file's §2/§3 comments already cite twice). Rewritten
-- to drop only the treatment_cycles branch, same as the PRS children above;
-- the column and everything else about this table is unchanged.
DROP POLICY IF EXISTS "rls_dsn_select" ON core."doctor_session_notes";
CREATE POLICY "rls_dsn_select" ON core."doctor_session_notes" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (doctor_id = rls_user_id())
        OR (patient_id = rls_user_id())
    );


-- ###########################################################################
-- 6  Drop treatment_sessions, treatment_plans, treatment_cycles
-- ###########################################################################
-- In dependency order: treatment_sessions references treatment_plans;
-- treatment_plans references treatment_cycles. Every other table that
-- referenced either has already been repointed or dropped its FK above, so
-- CASCADE below is a safety net, not a substitute for §§1-5.

DROP TABLE IF EXISTS core."treatment_sessions" CASCADE;
DROP TABLE IF EXISTS core."treatment_plans" CASCADE;
DROP TABLE IF EXISTS core."treatment_cycles" CASCADE;


COMMIT;


-- ###########################################################################
-- VERIFY — run after COMMIT
-- ###########################################################################
--
-- SELECT to_regclass('core.treatment_cycles');   -- NULL
-- SELECT to_regclass('core.treatment_plans');     -- NULL
-- SELECT to_regclass('core.treatment_sessions');  -- NULL
-- SELECT column_name FROM information_schema.columns
--  WHERE table_schema='core' AND table_name='protocol_instances'
--  ORDER BY ordinal_position;
--  -- instance_id, patient_id, created_by, instance_number, status, notes,
--  -- created_at, updated_at, doctor_id, ca_id, clinic_id, instance_type
--  -- (no cycle_id)
-- SELECT conname FROM pg_constraint WHERE conname LIKE '%cycle%';  -- 0 rows
--
-- As anava_app with a doctor context set: a protocol_instances row this
-- doctor created must be SELECTable, and so must the protocol_plan rows
-- under it — the part row-counting cannot check (45 §3 / 49's own lesson).
--
--
-- ###########################################################################
-- APPLICATION CODE — already done in the same change as this file
-- ###########################################################################
--
--   1. clinical/ module's TreatmentCycleService/TreatmentPlanService and
--      their repository/router code are removed. ProtocolRequestService
--      (assessment_protocol_requests) survives, minus cycle_id.
--   2. treatment_protocols/service.py's ProtocolInstanceService.create() now
--      takes patient_id/doctor_id/ca_id/clinic_id/instance_type directly, no
--      treatment_cycle involved.
--   3. patients/service.py's FollowUpService/PatientTransferService/
--      PatientExitService reuse ProtocolInstanceService/ProtocolInstanceRepository.
--   4. store/, scheduling/ modules renamed plan_id/cycle_id -> instance_id.
--
-- If this file is ever applied to a database whose app code predates these
-- changes, the app will fail at runtime (wrong column/table names) until the
-- matching code deploys — schema and code must land together.
-- ###########################################################################
