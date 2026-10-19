-- 122_consent_v2.sql
--
-- APPLY ORDER: after 121. Design: Documents/design_consent_v2.md.
--
-- 1. consent_templates: language, clauses (super-admin-defined checkboxes),
--    published_at (NULL = draft). content_hash is computed by trigger over
--    title + content + clauses. A published template can never be edited or
--    deleted; a wording change is a new version row.
-- 2. Existing active templates are frozen as published v1 (hashes untouched)
--    and an active v2 copy with one required clause takes over.
-- 3. consent_records: evidence columns (rendered text + hash, checkbox
--    responses, user agent, auth method, signer, PDF hash/version). Once a
--    record is signed only revocation and the PDF fields may change.
-- 4. patients.guardian_reason: why a guardian signs for an adult.
--
-- Safe to re-run: every statement is IF NOT EXISTS / OR REPLACE / guarded.

BEGIN;

-- ── 1. consent_templates ────────────────────────────────────────────────
ALTER TABLE reference."consent_templates" ADD COLUMN IF NOT EXISTS "language" TEXT NOT NULL DEFAULT 'en';
ALTER TABLE reference."consent_templates" ADD COLUMN IF NOT EXISTS "clauses" JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE reference."consent_templates" ADD COLUMN IF NOT EXISTS "published_at" TIMESTAMPTZ;

ALTER TABLE reference."consent_templates" DROP CONSTRAINT IF EXISTS "chk_consent_templates_clauses_array";
ALTER TABLE reference."consent_templates"
    ADD CONSTRAINT "chk_consent_templates_clauses_array" CHECK (jsonb_typeof("clauses") = 'array');
ALTER TABLE reference."consent_templates" DROP CONSTRAINT IF EXISTS "chk_consent_templates_language";
ALTER TABLE reference."consent_templates"
    ADD CONSTRAINT "chk_consent_templates_language" CHECK ("language" ~ '^[a-z]{2}$');

-- A draft is never served, so only a published row may be active.
ALTER TABLE reference."consent_templates" DROP CONSTRAINT IF EXISTS "chk_consent_templates_active_published";

-- Every row that exists before this file first runs has been served to
-- signers: freeze it. Only on the first run (guard trigger not created yet),
-- so a re-run never publishes a super admin's draft.
DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_consent_template_guard') THEN
        UPDATE reference."consent_templates" SET "published_at" = "created_at" WHERE "published_at" IS NULL;
    END IF;
END;
$do$;

ALTER TABLE reference."consent_templates"
    ADD CONSTRAINT "chk_consent_templates_active_published" CHECK (NOT "is_active" OR "published_at" IS NOT NULL);

-- (type, role, version) becomes (type, role, language, version); NULL role
-- compares equal so two role-less rows can't share a version.
ALTER TABLE reference."consent_templates" DROP CONSTRAINT IF EXISTS "uq_consent_templates_type_role_version";
CREATE UNIQUE INDEX IF NOT EXISTS "uq_consent_templates_type_role_lang_version"
    ON reference."consent_templates" ("consent_type", COALESCE("role", ''), "language", "version");
-- One active template per (type, role, language).
CREATE UNIQUE INDEX IF NOT EXISTS "uq_consent_templates_one_active"
    ON reference."consent_templates" ("consent_type", COALESCE("role", ''), "language") WHERE "is_active";

CREATE OR REPLACE FUNCTION reference.fn_consent_template_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.published_at IS NOT NULL THEN
            RAISE EXCEPTION 'consent template % is published and cannot be deleted', OLD.template_id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.published_at IS NOT NULL AND (
        NEW.title IS DISTINCT FROM OLD.title
        OR NEW.content IS DISTINCT FROM OLD.content
        OR NEW.clauses IS DISTINCT FROM OLD.clauses
        OR NEW.consent_type IS DISTINCT FROM OLD.consent_type
        OR NEW.role IS DISTINCT FROM OLD.role
        OR NEW.version IS DISTINCT FROM OLD.version
        OR NEW.language IS DISTINCT FROM OLD.language
        OR NEW.published_at IS DISTINCT FROM OLD.published_at
    ) THEN
        RAISE EXCEPTION 'consent template % is published; create a new version instead', OLD.template_id
            USING ERRCODE = 'check_violation';
    END IF;
    -- Published rows keep the hash they were signed against (v1 rows were
    -- hashed over content only); drafts and new rows get title+content+clauses.
    IF TG_OP = 'INSERT' OR OLD.published_at IS NULL THEN
        NEW.content_hash := encode(sha256(convert_to(NEW.title || E'\n' || NEW.content || E'\n' || NEW.clauses::text, 'UTF8')), 'hex');
    END IF;
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS "trg_consent_template_guard" ON reference."consent_templates";
CREATE TRIGGER "trg_consent_template_guard"
    BEFORE INSERT OR UPDATE OR DELETE ON reference."consent_templates"
    FOR EACH ROW EXECUTE FUNCTION reference.fn_consent_template_guard();

-- The app role only had SELECT on reference.* (18_grants.sql), so the super
-- admin could never write a template. RLS (rls_ct_*) keeps it super_admin-only
-- and the guard trigger keeps published rows frozen.
GRANT INSERT, UPDATE, DELETE ON reference."consent_templates" TO anava_app;

-- Drafts are deletable by super_admin (no DELETE policy existed at all).
DROP POLICY IF EXISTS "rls_ct_delete" ON reference."consent_templates";
CREATE POLICY "rls_ct_delete" ON reference."consent_templates" FOR DELETE TO public
    USING ((rls_user_role() = 'super_admin'::text));

-- ── 2. v2 of every active template, with one required clause ───────────
-- Guarded by NOT EXISTS so a re-run doesn't add a v3.
CREATE TEMP TABLE _ct_v1 ON COMMIT DROP AS
    SELECT t.* FROM reference."consent_templates" t
     WHERE t."is_active" AND t."clauses" = '[]'::jsonb
       AND NOT EXISTS (
           SELECT 1 FROM reference."consent_templates" n
            WHERE n."consent_type" = t."consent_type" AND n."role" IS NOT DISTINCT FROM t."role"
              AND n."language" = t."language" AND n."version" > t."version");

UPDATE reference."consent_templates" t SET "is_active" = FALSE
  FROM _ct_v1 v WHERE t."template_id" = v."template_id";

INSERT INTO reference."consent_templates"
    ("consent_type", "role", "language", "version", "title", "content", "clauses", "is_active", "published_at")
SELECT "consent_type", "role", "language", "version" + 1, "title", "content",
       '[{"code": "agree_terms", "label": "I have read and understood the above, and I agree to it.", "required": true}]'::jsonb,
       TRUE, now()
  FROM _ct_v1;

-- ── 3. consent_records evidence ─────────────────────────────────────────
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "rendered_title" TEXT;
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "rendered_content" TEXT;
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "rendered_hash" TEXT;
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "responses" JSONB;
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "user_agent" TEXT;
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "auth_method" TEXT;
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "signer_type" TEXT;
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "signer_name" TEXT;
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "signer_relation" TEXT;
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "signer_contact" TEXT;
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "guardian_reason" TEXT;
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "pdf_sha256" TEXT;
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "pdf_version_id" TEXT;
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "pdf_generated_at" TIMESTAMPTZ;

ALTER TABLE compliance."consent_records" DROP CONSTRAINT IF EXISTS "chk_consent_records_signer_type";
ALTER TABLE compliance."consent_records"
    ADD CONSTRAINT "chk_consent_records_signer_type" CHECK ("signer_type" IS NULL OR "signer_type" IN ('self', 'guardian'));
ALTER TABLE compliance."consent_records" DROP CONSTRAINT IF EXISTS "chk_consent_records_responses_array";
ALTER TABLE compliance."consent_records"
    ADD CONSTRAINT "chk_consent_records_responses_array" CHECK ("responses" IS NULL OR jsonb_typeof("responses") = 'array');

CREATE OR REPLACE FUNCTION compliance.fn_consent_record_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_mutable text[] := ARRAY['status', 'revoked_at', 'revoked_by', 'pdf_s3_key', 'pdf_sha256', 'pdf_version_id', 'pdf_generated_at'];
BEGIN
    IF OLD.status = 'pending' THEN
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - v_mutable) IS DISTINCT FROM (to_jsonb(OLD) - v_mutable) THEN
        RAISE EXCEPTION 'consent record % is %; its signed content cannot change', OLD.consent_id, OLD.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT (NEW.status = OLD.status OR (OLD.status = 'signed' AND NEW.status = 'revoked')) THEN
        RAISE EXCEPTION 'consent record % cannot go from % to %', OLD.consent_id, OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'revoked' AND (NEW.revoked_at IS DISTINCT FROM OLD.revoked_at OR NEW.revoked_by IS DISTINCT FROM OLD.revoked_by) THEN
        RAISE EXCEPTION 'consent record % revocation cannot change', OLD.consent_id USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.pdf_s3_key IS NOT NULL AND (
        NEW.pdf_s3_key IS DISTINCT FROM OLD.pdf_s3_key
        OR NEW.pdf_sha256 IS DISTINCT FROM OLD.pdf_sha256
        OR NEW.pdf_version_id IS DISTINCT FROM OLD.pdf_version_id
        OR NEW.pdf_generated_at IS DISTINCT FROM OLD.pdf_generated_at
    ) THEN
        RAISE EXCEPTION 'consent record % already has its PDF', OLD.consent_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS "trg_consent_record_guard" ON compliance."consent_records";
CREATE TRIGGER "trg_consent_record_guard"
    BEFORE UPDATE ON compliance."consent_records"
    FOR EACH ROW EXECUTE FUNCTION compliance.fn_consent_record_guard();

-- ── 4. patients.guardian_reason ─────────────────────────────────────────
ALTER TABLE core."patients" ADD COLUMN IF NOT EXISTS "guardian_reason" TEXT;
ALTER TABLE core."patients" DROP CONSTRAINT IF EXISTS "chk_patients_guardian_reason";
ALTER TABLE core."patients"
    ADD CONSTRAINT "chk_patients_guardian_reason"
    CHECK ("guardian_reason" IS NULL OR "guardian_reason" IN ('minor', 'lacks_capacity', 'lawful_guardian', 'nominated_representative'));

COMMIT;
