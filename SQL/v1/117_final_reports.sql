-- 117_final_reports.sql
--
-- APPLY ORDER: after 116. Idempotent.
--
-- Doctor-signed Final Report (Documents/Anava_Final_Report_Plan_v1.md).
-- A row exists only once the doctor has signed: there is no draft state.
-- `snapshot` freezes every report section at sign time, so what the patient
-- opens later is exactly what the doctor signed, even if anamnesis, notes or
-- protocols change afterwards.
--
-- IMMUTABLE: anava_app gets SELECT + INSERT only, and a trigger rejects any
-- UPDATE/DELETE as a second lock. A correction is a new report; older ones
-- stay visible.
--
-- Retention: clinical, Bucket 2 (anonymise with the patient, never hard-delete).

BEGIN;

CREATE TABLE IF NOT EXISTS core."final_reports" (
    "report_id"      UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id"     UUID NOT NULL,
    "clinic_id"      UUID NOT NULL,
    "doctor_id"      UUID NOT NULL,
    "scope"          TEXT NOT NULL,
    -- The consultation the report was cut at (set for last_consultation).
    "appointment_id" UUID,
    "diagnosis"      TEXT NOT NULL,
    "snapshot"       JSONB NOT NULL,
    "signature"      JSONB NOT NULL,
    "content_hash"   TEXT NOT NULL,
    "signed_at"      TIMESTAMPTZ NOT NULL DEFAULT now(),
    "created_at"     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "final_reports_pkey" PRIMARY KEY ("report_id"),
    -- Text + CHECK, never a native enum (v2 Layer 2).
    CONSTRAINT "chk_final_reports_scope" CHECK ("scope" IN ('overall', 'last_consultation')),
    CONSTRAINT "chk_final_reports_diagnosis" CHECK (length(btrim("diagnosis")) > 0)
);

COMMENT ON TABLE core."final_reports" IS
    'Doctor-signed Final Report. Rows exist only once signed, and are immutable (no UPDATE/DELETE grant + trigger). A correction is a new row. Retention: clinical, Bucket 2.';
COMMENT ON COLUMN core."final_reports"."patient_id" IS
    'References core.profiles(id), per the NOTES.md convention for patient_id-shaped FK columns.';
COMMENT ON COLUMN core."final_reports"."scope" IS
    'overall = patient start to signing date, last_consultation = the window ending at appointment_id.';
COMMENT ON COLUMN core."final_reports"."signature" IS
    'Shape: {"typed_name": str, "license_number": str|null, "style": str, "signed_at": timestamptz}. A typed e-signature, not a cryptographic one. content_hash is the tamper evidence.';
COMMENT ON COLUMN core."final_reports"."content_hash" IS
    'sha256 over the canonical JSON of diagnosis + snapshot + signature.';

ALTER TABLE core."final_reports" DROP CONSTRAINT IF EXISTS "fk_final_reports_patient";
ALTER TABLE core."final_reports" ADD CONSTRAINT "fk_final_reports_patient"
    FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."final_reports" DROP CONSTRAINT IF EXISTS "fk_final_reports_clinic";
ALTER TABLE core."final_reports" ADD CONSTRAINT "fk_final_reports_clinic"
    FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."final_reports" DROP CONSTRAINT IF EXISTS "fk_final_reports_doctor";
ALTER TABLE core."final_reports" ADD CONSTRAINT "fk_final_reports_doctor"
    FOREIGN KEY ("doctor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."final_reports" DROP CONSTRAINT IF EXISTS "fk_final_reports_appointment";
ALTER TABLE core."final_reports" ADD CONSTRAINT "fk_final_reports_appointment"
    FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_final_reports_patient
    ON core."final_reports" USING btree ("patient_id", "signed_at" DESC);
CREATE INDEX IF NOT EXISTS idx_final_reports_doctor
    ON core."final_reports" USING btree ("doctor_id", "signed_at" DESC);

DROP TRIGGER IF EXISTS trg_audit_final_reports ON core."final_reports";
CREATE TRIGGER trg_audit_final_reports
    AFTER INSERT OR DELETE OR UPDATE ON core."final_reports"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('report_id');


-- RLS. Read: platform admins, doctor / clinic_admin of the report's clinic,
-- and the patient themself. Write: the signing doctor only. The app layer
-- additionally checks the doctor is assigned to the patient.
ALTER TABLE core."final_reports" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."final_reports" FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_final_reports_select" ON core."final_reports";
CREATE POLICY "rls_final_reports_select" ON core."final_reports" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR ("patient_id" = rls_user_id())
        OR (rls_user_role() = ANY (ARRAY['doctor'::text, 'clinic_admin'::text]) AND "clinic_id" = rls_clinic_id())
    );

DROP POLICY IF EXISTS "rls_final_reports_insert" ON core."final_reports";
CREATE POLICY "rls_final_reports_insert" ON core."final_reports" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = 'doctor' AND "doctor_id" = rls_user_id() AND "clinic_id" = rls_clinic_id()
    );

-- No UPDATE / DELETE policy on purpose: with FORCE RLS that already denies both.


-- Grants: read + insert only. No UPDATE, no DELETE, ever.
GRANT SELECT, INSERT ON core."final_reports" TO anava_app;
REVOKE UPDATE, DELETE ON core."final_reports" FROM anava_app;
GRANT SELECT ON core."final_reports" TO anava_readonly;
GRANT SELECT ON core."final_reports" TO anava_compliance;

COMMIT;


-- ==FUNCTION==
-- Second lock, belt and braces. Kept after the marker so alembic 0063 can run
-- this one dollar-quoted statement whole instead of splitting on ';'.
CREATE OR REPLACE FUNCTION ops.fn_block_final_report_change()
RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    RAISE EXCEPTION 'final_reports rows are immutable once signed (report_id %)', OLD.report_id
        USING ERRCODE = 'restrict_violation';
END;
$function$;

DROP TRIGGER IF EXISTS trg_final_reports_immutable ON core."final_reports";
CREATE TRIGGER trg_final_reports_immutable
    BEFORE UPDATE OR DELETE ON core."final_reports"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_block_final_report_change();


-- VERIFY
--  SELECT count(*) FROM pg_policies WHERE tablename = 'final_reports';              -- 2
--  SELECT count(*) FROM pg_trigger WHERE tgrelid = 'core.final_reports'::regclass
--      AND NOT tgisinternal;                                                        -- 2
