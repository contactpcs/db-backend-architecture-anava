-- 47_protocol_prescription_tables.run.sql — stripped, copy-paste-and-run version
-- Full documented version: 47_protocol_prescription_tables.sql (this is the same logic, comments removed)

BEGIN;

ALTER TABLE core."treatment_protocols" RENAME TO "protocol_plan";

COMMENT ON TABLE core."protocol_plan" IS 'Treatment Protocol — device + montage + dosing + session plan, set once by a doctor to start a course of treatment. Formerly core.treatment_protocols (renamed 47); a CHILD of core.protocol_instances (45), which is the course, and optionally of core.treatment_plans, which is the clinical picture. Retention: clinical, Bucket 2 — anonymise with the patient profile, never hard-delete.';

-- backfill: give every plan-only row (instance_id NULL) an instance before instance_id becomes NOT NULL
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
            SELECT instance_id INTO v_instance_id
            FROM core."protocol_instances"
            WHERE cycle_id = r.cycle_id AND status IN ('draft', 'active')
            LIMIT 1;
        END;

        UPDATE core."protocol_plan" SET instance_id = v_instance_id WHERE protocol_id = r.protocol_id;
    END LOOP;
END $$;

ALTER TABLE core."protocol_plan" ALTER COLUMN "instance_id" SET NOT NULL;
ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_treatment_protocols_has_parent";

-- cosmetic renames — best-effort, tolerant of names not matching live schema
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

ALTER INDEX IF EXISTS core."idx_treatment_protocols_plan_id" RENAME TO "idx_protocol_plan_plan_id";
ALTER INDEX IF EXISTS core."idx_treatment_protocols_status" RENAME TO "idx_protocol_plan_status";
ALTER INDEX IF EXISTS core."idx_treatment_protocols_plan_active" RENAME TO "idx_protocol_plan_plan_active";
ALTER INDEX IF EXISTS core."idx_treatment_protocols_instance" RENAME TO "idx_protocol_plan_instance";
ALTER INDEX IF EXISTS core."idx_treatment_protocols_authored_in" RENAME TO "idx_protocol_plan_authored_in";
ALTER INDEX IF EXISTS core."uq_treatment_protocols_supersedes" RENAME TO "uq_protocol_plan_supersedes";


-- core.protocol_device_sessions

CREATE TABLE IF NOT EXISTS core."protocol_device_sessions" (
    "device_session_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "instance_id"        UUID NOT NULL,
    "protocol_id"        UUID NOT NULL,
    "appointment_id"     UUID NOT NULL,
    "clinic_device_id"   UUID NOT NULL,
    "session_number"     INTEGER NOT NULL,
    "planned_date"       DATE NOT NULL,
    "created_at"         TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_protocol_device_sessions_number" CHECK ("session_number" > 0)
);
COMMENT ON TABLE core."protocol_device_sessions" IS 'One device session as the doctor set it up: which ordinal, which unit, what date was prescribed. Points at the appointments row that carries the live bookable state (status, start_time, hold_expires_at) — this table never mirrors that state, so there is one place it can live. Retention: clinical, Bucket 2.';
COMMENT ON COLUMN core."protocol_device_sessions"."instance_id" IS 'The course this session belongs to. Denormalised from protocol_id so a course''s full schedule is one query without joining through protocol_plan.';
COMMENT ON COLUMN core."protocol_device_sessions"."planned_date" IS 'The date prescribed at authoring time. Does not move if the patient reschedules — appointments.appointment_date is the current date.';
COMMENT ON COLUMN core."protocol_device_sessions"."clinic_device_id" IS 'Denormalised from the appointments row created alongside it (41 made this mandatory there).';

ALTER TABLE core."protocol_device_sessions" DROP CONSTRAINT IF EXISTS "protocol_device_sessions_pkey" CASCADE;
ALTER TABLE core."protocol_device_sessions" ADD CONSTRAINT "protocol_device_sessions_pkey" PRIMARY KEY ("device_session_id");

ALTER TABLE core."protocol_device_sessions" DROP CONSTRAINT IF EXISTS "uq_protocol_device_sessions_appointment_id";
ALTER TABLE core."protocol_device_sessions" ADD CONSTRAINT "uq_protocol_device_sessions_appointment_id" UNIQUE ("appointment_id");

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


-- core.protocol_followup

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
COMMENT ON TABLE core."protocol_followup" IS 'One follow-up consultation as the doctor set it up: after which device session, what date was prescribed. Points at the appointments row that carries the live bookable state. Retention: clinical, Bucket 2.';
COMMENT ON COLUMN core."protocol_followup"."after_session_number" IS 'Which device session triggers this follow-up (5, 10, 15, 20 for follow_up_every_n = 5).';
COMMENT ON COLUMN core."protocol_followup"."planned_date" IS 'The date prescribed at authoring time; does not move on reschedule.';

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


-- RLS

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


-- Grants

GRANT SELECT, INSERT, UPDATE ON core."protocol_device_sessions", core."protocol_followup" TO anava_app;
REVOKE DELETE ON core."protocol_device_sessions", core."protocol_followup" FROM anava_app;
GRANT SELECT ON core."protocol_device_sessions", core."protocol_followup" TO anava_readonly;
GRANT SELECT ON core."protocol_device_sessions", core."protocol_followup" TO anava_compliance;

COMMIT;
