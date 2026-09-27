-- 96_prescribed_medicines.sql
--
-- Stores the doctor's medicine prescriptions for a patient (doctor workspace
-- → "Prescribed Medicine"). Until now the screen kept them only in browser
-- memory (Redux) and lost them on reload.
--
-- SCOPE: one new table with RLS, triggers and grants. No existing table
-- changes.
--
-- APPLY ORDER: after 95.
--
-- One row per prescribed medicine. Stopping a medicine flips status to
-- 'stopped' (and resuming flips it back) — rows are never deleted, so the
-- patient's full medication history stays readable. Retention: clinical,
-- Bucket 2 (anonymise with the patient, never hard-delete).

BEGIN;

CREATE TABLE IF NOT EXISTS core."prescribed_medicines" (
    "medicine_id"       UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id"        UUID NOT NULL,
    "clinic_id"         UUID NOT NULL,
    "prescribed_by"     UUID NOT NULL,
    -- The consultation it was prescribed in, when known. Provenance only.
    "appointment_id"    UUID,
    "medicine_name"     TEXT NOT NULL,
    "dose"              TEXT,
    "timing"            TEXT,
    "meal_instruction"  TEXT,
    "duration"          TEXT,
    "note"              TEXT,
    "status"            TEXT NOT NULL DEFAULT 'active',
    "started_at"        TIMESTAMPTZ NOT NULL DEFAULT now(),
    "stopped_at"        TIMESTAMPTZ,
    "stopped_by"        UUID,
    "created_at"        TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "prescribed_medicines_pkey" PRIMARY KEY ("medicine_id"),
    CONSTRAINT "chk_prescribed_medicines_name" CHECK (length(btrim("medicine_name")) > 0),
    -- Text + CHECK, never a native enum (v2 Layer 2): values match the
    -- doctor workspace's dropdowns exactly.
    CONSTRAINT "chk_prescribed_medicines_status" CHECK ("status" IN ('active', 'stopped')),
    CONSTRAINT "chk_prescribed_medicines_timing" CHECK ("timing" IS NULL OR "timing" IN
        ('Morning', 'Afternoon', 'Evening', 'Night', 'Twice daily', 'Three times daily', 'As needed')),
    CONSTRAINT "chk_prescribed_medicines_meal" CHECK ("meal_instruction" IS NULL OR "meal_instruction" IN
        ('Before meal', 'After meal', 'With meal', 'Empty stomach', 'Not applicable')),
    -- stopped_at/stopped_by exist exactly while the medicine is stopped.
    CONSTRAINT "chk_prescribed_medicines_stopped" CHECK (
        ("status" = 'stopped') = ("stopped_at" IS NOT NULL)
    )
);

COMMENT ON TABLE core."prescribed_medicines" IS
    'A medicine a doctor prescribed for a patient. Stopped, never deleted — the full medication history stays readable. Retention: clinical, Bucket 2.';
COMMENT ON COLUMN core."prescribed_medicines"."patient_id" IS
    'References core.profiles(id), per the NOTES.md convention for patient_id-shaped FK columns.';

ALTER TABLE core."prescribed_medicines" ADD CONSTRAINT "fk_prescribed_medicines_patient"
    FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."prescribed_medicines" ADD CONSTRAINT "fk_prescribed_medicines_clinic"
    FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."prescribed_medicines" ADD CONSTRAINT "fk_prescribed_medicines_prescribed_by"
    FOREIGN KEY ("prescribed_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."prescribed_medicines" ADD CONSTRAINT "fk_prescribed_medicines_stopped_by"
    FOREIGN KEY ("stopped_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."prescribed_medicines" ADD CONSTRAINT "fk_prescribed_medicines_appointment"
    FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_prescribed_medicines_patient
    ON core."prescribed_medicines" USING btree ("patient_id", "started_at" DESC);

DROP TRIGGER IF EXISTS trg_prescribed_medicines_updated_at ON core."prescribed_medicines";
CREATE TRIGGER trg_prescribed_medicines_updated_at
    BEFORE UPDATE ON core."prescribed_medicines"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();

DROP TRIGGER IF EXISTS trg_audit_prescribed_medicines ON core."prescribed_medicines";
CREATE TRIGGER trg_audit_prescribed_medicines
    AFTER INSERT OR DELETE OR UPDATE ON core."prescribed_medicines"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('medicine_id');


-- RLS. Read: the patient themself, platform admins, and staff of the
-- patient's clinic. Write: doctors (and super_admin) of that clinic — the
-- app layer additionally checks clinic scope and role.
ALTER TABLE core."prescribed_medicines" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."prescribed_medicines" FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_prescribed_medicines_select" ON core."prescribed_medicines";
CREATE POLICY "rls_prescribed_medicines_select" ON core."prescribed_medicines" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR ("patient_id" = rls_user_id())
        OR ("clinic_id" = rls_clinic_id())
    );

DROP POLICY IF EXISTS "rls_prescribed_medicines_insert" ON core."prescribed_medicines";
CREATE POLICY "rls_prescribed_medicines_insert" ON core."prescribed_medicines" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = 'super_admin')
        OR (rls_user_role() = 'doctor' AND "clinic_id" = rls_clinic_id() AND "prescribed_by" = rls_user_id())
    );

DROP POLICY IF EXISTS "rls_prescribed_medicines_update" ON core."prescribed_medicines";
CREATE POLICY "rls_prescribed_medicines_update" ON core."prescribed_medicines" FOR UPDATE TO public
    USING (
        (rls_user_role() = 'super_admin')
        OR (rls_user_role() = 'doctor' AND "clinic_id" = rls_clinic_id())
    );


-- Grants: read/insert/update for the app, deletion never (history only).
GRANT SELECT, INSERT, UPDATE ON core."prescribed_medicines" TO anava_app;
REVOKE DELETE ON core."prescribed_medicines" FROM anava_app;
GRANT SELECT ON core."prescribed_medicines" TO anava_readonly;
GRANT SELECT ON core."prescribed_medicines" TO anava_compliance;

COMMIT;


-- VERIFY
--  SELECT count(*) FROM pg_policies WHERE tablename = 'prescribed_medicines';  -- 3
