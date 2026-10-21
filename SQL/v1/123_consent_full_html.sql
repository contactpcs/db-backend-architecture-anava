-- 123_consent_full_html.sql
--
-- APPLY ORDER: after 122. Design: Documents/design_consent_v2.md ("Consent v3").
--
-- 1. consent_templates.full_html: the complete, binding consent form (HTML),
--    shown behind "Read the full consent form"; `content` becomes the summary.
--    Frozen on publish and covered by content_hash like the rest (own
--    trigger, so a re-run of 122 cannot undo it).
-- 2. consent_records: fill_values (every placeholder value at signing),
--    rendered_full_hash (hash of the filled full form), full_viewed_at (first
--    time the signer opened the full form). The record guard from 122 already
--    freezes them once signed.
--
-- Safe to re-run.

BEGIN;

-- ── 1. consent_templates.full_html ──────────────────────────────────────
ALTER TABLE reference."consent_templates" ADD COLUMN IF NOT EXISTS "full_html" TEXT;
ALTER TABLE reference."consent_templates" DROP CONSTRAINT IF EXISTS "chk_consent_templates_full_html_size";
ALTER TABLE reference."consent_templates"
    ADD CONSTRAINT "chk_consent_templates_full_html_size" CHECK ("full_html" IS NULL OR octet_length("full_html") <= 1048576);

-- Its own trigger, not a new version of 122's fn_consent_template_guard:
-- re-running 122 later (it says "safe to re-run") must not silently drop the
-- full_html freeze (the SQL 97-after-74 trap). Fires after 122's trigger
-- (triggers run in name order), so it has the last word on content_hash.
CREATE OR REPLACE FUNCTION reference.fn_consent_template_full_html_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.published_at IS NOT NULL THEN
        IF NEW.full_html IS DISTINCT FROM OLD.full_html THEN
            RAISE EXCEPTION 'consent template % is published; create a new version instead', OLD.template_id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;  -- published: keep the hash it was signed against
    END IF;
    -- Drafts and new rows. Without full_html this is exactly 122's formula.
    NEW.content_hash := encode(sha256(convert_to(
        NEW.title || E'\n' || NEW.content || E'\n' || NEW.clauses::text || COALESCE(E'\n' || NEW.full_html, ''),
        'UTF8')), 'hex');
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS "trg_consent_template_guard_full_html" ON reference."consent_templates";
CREATE TRIGGER "trg_consent_template_guard_full_html"
    BEFORE INSERT OR UPDATE ON reference."consent_templates"
    FOR EACH ROW EXECUTE FUNCTION reference.fn_consent_template_full_html_guard();

-- An adult may take a guardian's help without a legal reason (D3 revised):
-- recorded as patient_requested. Same list as 122 (kept in sync there).
ALTER TABLE core."patients" DROP CONSTRAINT IF EXISTS "chk_patients_guardian_reason";
ALTER TABLE core."patients"
    ADD CONSTRAINT "chk_patients_guardian_reason"
    CHECK ("guardian_reason" IS NULL OR "guardian_reason" IN ('minor', 'lacks_capacity', 'lawful_guardian', 'nominated_representative', 'patient_requested'));

-- ── 2. consent_records evidence ─────────────────────────────────────────
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "fill_values" JSONB;
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "rendered_full_hash" TEXT;
ALTER TABLE compliance."consent_records" ADD COLUMN IF NOT EXISTS "full_viewed_at" TIMESTAMPTZ;

ALTER TABLE compliance."consent_records" DROP CONSTRAINT IF EXISTS "chk_consent_records_fill_values_object";
ALTER TABLE compliance."consent_records"
    ADD CONSTRAINT "chk_consent_records_fill_values_object" CHECK ("fill_values" IS NULL OR jsonb_typeof("fill_values") = 'object');

COMMIT;
