-- 127_ai_provider_nvidia.sql
--
-- APPLY ORDER: after 125 and 126. Idempotent.
--
-- The Ask ANAVA assistant now calls NVIDIA's API Catalog (build.nvidia.com)
-- instead of Google Gemini (backend/app/integrations/ai_provider.py).
--
--   * ops.ai_usage_log: allow provider 'nvidia'. 'gemini' stays allowed so the
--     rows already logged remain valid (the table is append-only).
--   * reference.assistant_faq: the privacy answer no longer names Google.
--     Rows a reviewer has approved are left alone, like the 126 seed.

BEGIN;

ALTER TABLE ops."ai_usage_log" DROP CONSTRAINT IF EXISTS "chk_ai_usage_log_provider";
ALTER TABLE ops."ai_usage_log" ADD CONSTRAINT "chk_ai_usage_log_provider"
    CHECK ("provider" IN ('nvidia', 'gemini', 'claude'));

UPDATE reference."assistant_faq"
SET "answer" = 'Only the words of a question I can''t answer myself are sent to an AI service (NVIDIA), without your name, phone or health records.',
    "updated_at" = now()
WHERE "intent" = 'do_you_send_my_data_to_google' AND "language" = 'en'
  AND "approved_by" IS NULL AND "approved_at" IS NULL;

COMMIT;
