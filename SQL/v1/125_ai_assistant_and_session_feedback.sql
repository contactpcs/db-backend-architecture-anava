-- 125_ai_assistant_and_session_feedback.sql
--
-- APPLY ORDER: after 124. Idempotent.
--
-- Patient-app "Ask ANAVA" assistant and post-session feedback
-- (Documents/Ask_ANAVA_Design_Brief.md, Step 2).
--
--   reference.assistant_faq      approved answers the assistant matches against (en / hi / mr)
--   core.assistant_consents      one-time patient opt-in to the assistant, versioned
--   core.assistant_messages      every chat message, both sides (append-only, 12-month purge)
--   core.session_feedback        one feedback form per completed appointment
--   ops.ai_usage_log             every call to the external AI model (free-tier quota + cost case)
--
-- Scope decisions baked in here (brief, "Decisions needed"):
--   * Patient app only. Clinic staff get no read access to chats or feedback:
--     only the patient, platform admins (super_admin / regional_admin) and
--     role 'system' (background jobs) can read them.
--   * Feedback is stored separately. core.device_session_feedback (the
--     clinical assistant's form, shown in the doctor's plan view) is untouched.
--   * Feedback cannot be edited once sent. Only role 'system' may UPDATE a row,
--     to add AI tags and sentiment to the comment afterwards.
--   * No separate "conversation" table: assistant_messages.conversation_id is
--     an app-generated UUID per chat thread.
--   * Feedback prompts and reminders are de-duplicated through core.notifications
--     (type 'session_feedback_request', entity_id = appointment_id), so no
--     extra tracking table.
--   * No new extensions. FAQ matching runs in the app over the (small) FAQ set.
--
-- Retention: assistant_messages = Bucket 3, purged after 12 months by the
-- retention worker (role 'system' DELETE). session_feedback = clinical,
-- Bucket 2 (anonymise with the patient). ai_usage_log holds no patient data.

BEGIN;

-- ###########################################################################
-- 1  reference.assistant_faq
-- ###########################################################################

CREATE TABLE IF NOT EXISTS reference."assistant_faq" (
    "faq_id"        UUID NOT NULL DEFAULT gen_random_uuid(),
    "intent"        TEXT NOT NULL,
    "language"      TEXT NOT NULL,
    "category"      TEXT NOT NULL,
    "questions"     TEXT[] NOT NULL,
    "answer"        TEXT NOT NULL,
    "reply_type"    TEXT NOT NULL,
    "action"        TEXT,
    "approved_by"   UUID,
    "approved_at"   TIMESTAMPTZ,
    "is_active"     BOOLEAN NOT NULL DEFAULT TRUE,
    "sort_order"    INTEGER NOT NULL DEFAULT 0,
    "created_at"    TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "assistant_faq_pkey" PRIMARY KEY ("faq_id"),
    CONSTRAINT "uq_assistant_faq_intent_language" UNIQUE ("intent", "language"),
    CONSTRAINT "chk_assistant_faq_language" CHECK ("language" IN ('en', 'hi', 'mr')),
    CONSTRAINT "chk_assistant_faq_reply_type"
        CHECK ("reply_type" IN ('faq', 'data', 'action', 'redirect', 'decline', 'safety')),
    CONSTRAINT "chk_assistant_faq_questions" CHECK (cardinality("questions") > 0),
    CONSTRAINT "chk_assistant_faq_answer" CHECK (length(btrim("answer")) > 0)
);

COMMENT ON TABLE reference."assistant_faq" IS
    'Approved assistant answers, one row per intent and language. The app matches the patient question against questions[] and returns answer. Only active rows are served.';
COMMENT ON COLUMN reference."assistant_faq"."intent" IS
    'Stable key shared by the en / hi / mr rows of the same answer, e.g. next_session, refund_policy, crisis_self_harm.';
COMMENT ON COLUMN reference."assistant_faq"."questions" IS
    'Example phrasings in this language. The matcher compares the patient message against these.';
COMMENT ON COLUMN reference."assistant_faq"."answer" IS
    'Reply text. {placeholders} such as {next_date} or {clinic_phone} are filled by the backend from the patient''s own data before sending.';
COMMENT ON COLUMN reference."assistant_faq"."reply_type" IS
    'faq = stored answer, data = template filled from patient records, action = reply plus screen button, redirect = to doctor or clinic, decline = polite refusal, safety = fixed safety screen.';
COMMENT ON COLUMN reference."assistant_faq"."action" IS
    'App screen key for the button shown with the answer, e.g. pay, reschedule, book, payments, profile_personal. NULL = no button.';
COMMENT ON COLUMN reference."assistant_faq"."approved_by" IS
    'Profile of the person who approved this text (doctor sign-off for treatment and side-effect answers). NULL = not yet approved.';

ALTER TABLE reference."assistant_faq" DROP CONSTRAINT IF EXISTS "fk_assistant_faq_approved_by";
ALTER TABLE reference."assistant_faq" ADD CONSTRAINT "fk_assistant_faq_approved_by"
    FOREIGN KEY ("approved_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_assistant_faq_active
    ON reference."assistant_faq" USING btree ("language", "sort_order") WHERE ("is_active" = TRUE);

DROP TRIGGER IF EXISTS trg_audit_assistant_faq ON reference."assistant_faq";
CREATE TRIGGER trg_audit_assistant_faq
    AFTER INSERT OR DELETE OR UPDATE ON reference."assistant_faq"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('faq_id');

-- RLS. Everyone signed in reads active rows. Only super_admin writes.
ALTER TABLE reference."assistant_faq" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."assistant_faq" FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_assistant_faq_select" ON reference."assistant_faq";
CREATE POLICY "rls_assistant_faq_select" ON reference."assistant_faq" FOR SELECT TO public
    USING ("is_active" = TRUE OR rls_user_role() = ANY (ARRAY['super_admin'::text, 'system'::text]));

DROP POLICY IF EXISTS "rls_assistant_faq_write" ON reference."assistant_faq";
CREATE POLICY "rls_assistant_faq_write" ON reference."assistant_faq" FOR ALL TO public
    USING (rls_user_role() = 'super_admin')
    WITH CHECK (rls_user_role() = 'super_admin');

GRANT SELECT, INSERT, UPDATE ON reference."assistant_faq" TO anava_app;
REVOKE DELETE ON reference."assistant_faq" FROM anava_app;
GRANT SELECT ON reference."assistant_faq" TO anava_readonly;


-- ###########################################################################
-- 2  core.assistant_consents
-- ###########################################################################

CREATE TABLE IF NOT EXISTS core."assistant_consents" (
    "consent_id"    UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id"    UUID NOT NULL,
    "kind"          TEXT NOT NULL,
    "version"       TEXT NOT NULL,
    "language"      TEXT NOT NULL,
    "accepted_at"   TIMESTAMPTZ NOT NULL DEFAULT now(),
    "withdrawn_at"  TIMESTAMPTZ,
    CONSTRAINT "assistant_consents_pkey" PRIMARY KEY ("consent_id"),
    CONSTRAINT "uq_assistant_consents_patient_kind_version" UNIQUE ("patient_id", "kind", "version"),
    CONSTRAINT "chk_assistant_consents_kind" CHECK ("kind" IN ('assistant', 'feedback')),
    CONSTRAINT "chk_assistant_consents_language" CHECK ("language" IN ('en', 'hi', 'mr')),
    CONSTRAINT "chk_assistant_consents_withdrawn" CHECK ("withdrawn_at" IS NULL OR "withdrawn_at" >= "accepted_at")
);

COMMENT ON TABLE core."assistant_consents" IS
    'Patient opt-in shown before first use of the assistant (kind assistant) or the first feedback form (kind feedback). A new text version needs a new acceptance.';
COMMENT ON COLUMN core."assistant_consents"."patient_id" IS
    'References core.profiles(id), per the NOTES.md convention for patient_id-shaped FK columns.';
COMMENT ON COLUMN core."assistant_consents"."version" IS
    'Version label of the consent text shown, e.g. 2026-10-v1. Text itself lives in the app per language.';

ALTER TABLE core."assistant_consents" DROP CONSTRAINT IF EXISTS "fk_assistant_consents_patient";
ALTER TABLE core."assistant_consents" ADD CONSTRAINT "fk_assistant_consents_patient"
    FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

DROP TRIGGER IF EXISTS trg_audit_assistant_consents ON core."assistant_consents";
CREATE TRIGGER trg_audit_assistant_consents
    AFTER INSERT OR DELETE OR UPDATE ON core."assistant_consents"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('consent_id');

-- RLS. The patient accepts and withdraws their own. Admins and system read.
ALTER TABLE core."assistant_consents" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."assistant_consents" FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_assistant_consents_select" ON core."assistant_consents";
CREATE POLICY "rls_assistant_consents_select" ON core."assistant_consents" FOR SELECT TO public
    USING (
        ("patient_id" = rls_user_id())
        OR (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
    );

DROP POLICY IF EXISTS "rls_assistant_consents_insert" ON core."assistant_consents";
CREATE POLICY "rls_assistant_consents_insert" ON core."assistant_consents" FOR INSERT TO public
    WITH CHECK (rls_user_role() = 'patient' AND "patient_id" = rls_user_id());

DROP POLICY IF EXISTS "rls_assistant_consents_update" ON core."assistant_consents";
CREATE POLICY "rls_assistant_consents_update" ON core."assistant_consents" FOR UPDATE TO public
    USING (rls_user_role() = 'patient' AND "patient_id" = rls_user_id())
    WITH CHECK (rls_user_role() = 'patient' AND "patient_id" = rls_user_id());

GRANT SELECT, INSERT ON core."assistant_consents" TO anava_app;
-- Only withdrawn_at may change, and only by the patient (policy above).
GRANT UPDATE ("withdrawn_at") ON core."assistant_consents" TO anava_app;
REVOKE DELETE ON core."assistant_consents" FROM anava_app;
GRANT SELECT ON core."assistant_consents" TO anava_readonly;
GRANT SELECT ON core."assistant_consents" TO anava_compliance;


-- ###########################################################################
-- 3  core.assistant_messages
-- ###########################################################################

CREATE TABLE IF NOT EXISTS core."assistant_messages" (
    "message_id"       UUID NOT NULL DEFAULT gen_random_uuid(),
    "conversation_id"  UUID NOT NULL,
    "patient_id"       UUID NOT NULL,
    "role"             TEXT NOT NULL,
    "body"             TEXT NOT NULL,
    "language"         TEXT NOT NULL,
    "input_mode"       TEXT,
    "answered_by"      TEXT,
    "intent"           TEXT,
    "faq_id"           UUID,
    "safety_screen"    TEXT,
    "created_at"       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "assistant_messages_pkey" PRIMARY KEY ("message_id"),
    CONSTRAINT "chk_assistant_messages_role" CHECK ("role" IN ('patient', 'assistant')),
    CONSTRAINT "chk_assistant_messages_language" CHECK ("language" IN ('en', 'hi', 'mr')),
    -- Patient messages are capped at 500 characters (brief, usage limits).
    CONSTRAINT "chk_assistant_messages_body"
        CHECK (length(btrim("body")) > 0 AND length("body") <= CASE WHEN "role" = 'patient' THEN 500 ELSE 2000 END),
    CONSTRAINT "chk_assistant_messages_input_mode"
        CHECK ("input_mode" IS NULL OR "input_mode" IN ('text', 'voice', 'chip')),
    CONSTRAINT "chk_assistant_messages_answered_by"
        CHECK ("answered_by" IS NULL OR "answered_by" IN ('safety', 'data', 'faq', 'ai', 'fallback', 'limit')),
    CONSTRAINT "chk_assistant_messages_safety_screen"
        CHECK ("safety_screen" IS NULL OR "safety_screen" IN ('S0', 'S1', 'S2', 'S3', 'S4')),
    -- Who answered is recorded on assistant rows only. Input mode on patient rows only.
    CONSTRAINT "chk_assistant_messages_role_fields" CHECK (
        ("role" = 'patient' AND "answered_by" IS NULL)
        OR ("role" = 'assistant' AND "answered_by" IS NOT NULL AND "input_mode" IS NULL)
    )
);

COMMENT ON TABLE core."assistant_messages" IS
    'Every Ask ANAVA chat message, patient and assistant. Append-only. Not visible to clinic staff. Retention: Bucket 3, purged after 12 months by role system.';
COMMENT ON COLUMN core."assistant_messages"."conversation_id" IS
    'App-generated UUID for one chat thread. The last 6 messages of the thread are the context sent with a follow-up question.';
COMMENT ON COLUMN core."assistant_messages"."patient_id" IS
    'References core.profiles(id). Also the key for the 30-messages-per-day limit.';
COMMENT ON COLUMN core."assistant_messages"."answered_by" IS
    'Which step produced the reply: safety screen, patient-data template, stored FAQ, ai (external model), fallback (could not answer or AI unavailable), limit (daily cap reached).';
COMMENT ON COLUMN core."assistant_messages"."intent" IS
    'Matched reference.assistant_faq.intent, or the data intent, when known.';

ALTER TABLE core."assistant_messages" DROP CONSTRAINT IF EXISTS "fk_assistant_messages_patient";
ALTER TABLE core."assistant_messages" ADD CONSTRAINT "fk_assistant_messages_patient"
    FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."assistant_messages" DROP CONSTRAINT IF EXISTS "fk_assistant_messages_faq";
ALTER TABLE core."assistant_messages" ADD CONSTRAINT "fk_assistant_messages_faq"
    FOREIGN KEY ("faq_id") REFERENCES reference."assistant_faq" ("faq_id") ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_assistant_messages_patient
    ON core."assistant_messages" USING btree ("patient_id", "created_at" DESC);
CREATE INDEX IF NOT EXISTS idx_assistant_messages_conversation
    ON core."assistant_messages" USING btree ("conversation_id", "created_at" DESC);
CREATE INDEX IF NOT EXISTS idx_assistant_messages_created
    ON core."assistant_messages" USING btree ("created_at");

-- No audit trigger: high volume, append-only, and the rows are their own log.

-- RLS. The patient reads and writes their own. Admins and system read.
-- Only system deletes (12-month retention purge).
ALTER TABLE core."assistant_messages" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."assistant_messages" FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_assistant_messages_select" ON core."assistant_messages";
CREATE POLICY "rls_assistant_messages_select" ON core."assistant_messages" FOR SELECT TO public
    USING (
        ("patient_id" = rls_user_id())
        OR (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
    );

-- Both the question and the reply are written in the patient's request.
DROP POLICY IF EXISTS "rls_assistant_messages_insert" ON core."assistant_messages";
CREATE POLICY "rls_assistant_messages_insert" ON core."assistant_messages" FOR INSERT TO public
    WITH CHECK (rls_user_role() = 'patient' AND "patient_id" = rls_user_id());

DROP POLICY IF EXISTS "rls_assistant_messages_delete" ON core."assistant_messages";
CREATE POLICY "rls_assistant_messages_delete" ON core."assistant_messages" FOR DELETE TO public
    USING (rls_user_role() = 'system');

GRANT SELECT, INSERT, DELETE ON core."assistant_messages" TO anava_app;
REVOKE UPDATE ON core."assistant_messages" FROM anava_app;
GRANT SELECT ON core."assistant_messages" TO anava_readonly;


-- ###########################################################################
-- 4  core.session_feedback
-- ###########################################################################

CREATE TABLE IF NOT EXISTS core."session_feedback" (
    "feedback_id"           UUID NOT NULL DEFAULT gen_random_uuid(),
    "appointment_id"        UUID NOT NULL,
    "patient_id"            UUID NOT NULL,
    "clinic_id"             UUID NOT NULL,
    "appointment_type"      TEXT NOT NULL,
    -- Copied from the appointment at submit time, so later edits to the
    -- appointment never change what this visit's feedback is about.
    "doctor_id"             UUID,
    "protocol_id"           UUID,
    "session_number"        INTEGER,
    "form_version"          TEXT NOT NULL,
    "status"                TEXT NOT NULL,
    "language"              TEXT NOT NULL,
    "input_mode"            TEXT NOT NULL DEFAULT 'tap',
    "answers"               JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Promoted from answers for analysis (brief, F1-F11 and C1-C3). All
    -- optional: every question can be skipped.
    "overall_rating"        SMALLINT,
    "comfort"               TEXT,
    "felt_after"            TEXT,
    "side_effects"          TEXT[] NOT NULL DEFAULT '{}'::text[],
    "side_effect_severity"  TEXT,
    "side_effect_ongoing"   BOOLEAN,
    "next_intensity"        TEXT,
    "wait_band"             TEXT,
    "device_ok"             BOOLEAN,
    "device_issue"          TEXT,
    "staff_rating"          SMALLINT,
    "facility_rating"       SMALLINT,
    "explained_rating"      SMALLINT,
    "questions_answered"    TEXT,
    "booking_rating"        SMALLINT,
    "nps"                   SMALLINT,
    "comment"               TEXT,
    "comment_tags"          TEXT[] NOT NULL DEFAULT '{}'::text[],
    "sentiment"             TEXT,
    "safety_screen"         TEXT,
    "sent_at"               TIMESTAMPTZ NOT NULL DEFAULT now(),
    "tagged_at"             TIMESTAMPTZ,
    CONSTRAINT "session_feedback_pkey" PRIMARY KEY ("feedback_id"),
    CONSTRAINT "uq_session_feedback_appointment" UNIQUE ("appointment_id"),
    CONSTRAINT "chk_session_feedback_status" CHECK ("status" IN ('submitted', 'skipped')),
    CONSTRAINT "chk_session_feedback_language" CHECK ("language" IN ('en', 'hi', 'mr')),
    CONSTRAINT "chk_session_feedback_input_mode" CHECK ("input_mode" IN ('tap', 'voice', 'mixed')),
    CONSTRAINT "chk_session_feedback_overall" CHECK ("overall_rating" IS NULL OR "overall_rating" BETWEEN 1 AND 5),
    CONSTRAINT "chk_session_feedback_comfort" CHECK ("comfort" IS NULL OR "comfort" IN ('comfortable', 'tolerable', 'uncomfortable')),
    CONSTRAINT "chk_session_feedback_felt_after" CHECK ("felt_after" IS NULL OR "felt_after" IN ('better', 'no_change', 'worse')),
    CONSTRAINT "chk_session_feedback_side_effects" CHECK ("side_effects" <@ ARRAY[
        'tingling', 'itching', 'burning', 'redness', 'headache', 'dizziness', 'tiredness', 'nausea', 'concentration', 'other']::text[]),
    CONSTRAINT "chk_session_feedback_severity"
        CHECK ("side_effect_severity" IS NULL OR "side_effect_severity" IN ('mild', 'moderate', 'severe')),
    CONSTRAINT "chk_session_feedback_next_intensity"
        CHECK ("next_intensity" IS NULL OR "next_intensity" IN ('lower', 'same', 'higher', 'not_sure')),
    CONSTRAINT "chk_session_feedback_wait_band"
        CHECK ("wait_band" IS NULL OR "wait_band" IN ('on_time', 'under_15', '15_30', 'over_30')),
    CONSTRAINT "chk_session_feedback_device_issue"
        CHECK ("device_issue" IS NULL OR "device_issue" IN ('fit', 'stopped', 'pads', 'other')),
    CONSTRAINT "chk_session_feedback_staff" CHECK ("staff_rating" IS NULL OR "staff_rating" BETWEEN 1 AND 5),
    CONSTRAINT "chk_session_feedback_facility" CHECK ("facility_rating" IS NULL OR "facility_rating" BETWEEN 1 AND 5),
    CONSTRAINT "chk_session_feedback_explained" CHECK ("explained_rating" IS NULL OR "explained_rating" BETWEEN 1 AND 5),
    CONSTRAINT "chk_session_feedback_questions"
        CHECK ("questions_answered" IS NULL OR "questions_answered" IN ('yes', 'partly', 'no')),
    CONSTRAINT "chk_session_feedback_booking" CHECK ("booking_rating" IS NULL OR "booking_rating" BETWEEN 1 AND 5),
    CONSTRAINT "chk_session_feedback_nps" CHECK ("nps" IS NULL OR "nps" BETWEEN 0 AND 10),
    CONSTRAINT "chk_session_feedback_comment" CHECK ("comment" IS NULL OR length("comment") <= 2000),
    CONSTRAINT "chk_session_feedback_sentiment"
        CHECK ("sentiment" IS NULL OR "sentiment" IN ('positive', 'neutral', 'negative')),
    CONSTRAINT "chk_session_feedback_safety_screen"
        CHECK ("safety_screen" IS NULL OR "safety_screen" IN ('S0', 'S1', 'S2', 'S3', 'S4')),
    -- A skipped form carries no answers.
    CONSTRAINT "chk_session_feedback_skipped" CHECK (
        "status" = 'submitted'
        OR ("answers" = '{}'::jsonb AND "overall_rating" IS NULL AND "comment" IS NULL AND cardinality("side_effects") = 0)
    )
);

COMMENT ON TABLE core."session_feedback" IS
    'Patient feedback after a completed visit, one row per appointment (submitted or skipped). Collected only, not shown to clinic staff. Cannot be edited by the patient. Separate from core.device_session_feedback. Retention: clinical, Bucket 2.';
COMMENT ON COLUMN core."session_feedback"."patient_id" IS
    'References core.profiles(id), per the NOTES.md convention for patient_id-shaped FK columns.';
COMMENT ON COLUMN core."session_feedback"."form_version" IS
    'Version of the question set shown, e.g. device-2026-10-v1 or consult-2026-10-v1. answers keys are question IDs (F1..F11, C1..C3) of that version.';
COMMENT ON COLUMN core."session_feedback"."answers" IS
    'Every answer as given, keyed by question ID of form_version. The promoted columns repeat the ones used for analysis.';
COMMENT ON COLUMN core."session_feedback"."next_intensity" IS
    'Patient preference only. The doctor decides the intensity.';
COMMENT ON COLUMN core."session_feedback"."comment_tags" IS
    'Topic tags added after submit by role system (AI over the comment, names and numbers removed): wait, staff, pain, device, billing, cleanliness, praise, other.';
COMMENT ON COLUMN core."session_feedback"."safety_screen" IS
    'Safety screen shown during the form (S2 for a severe side effect, S1 for self-harm words in the comment). Nobody is notified: the record only shows the patient saw it.';

ALTER TABLE core."session_feedback" DROP CONSTRAINT IF EXISTS "fk_session_feedback_appointment";
ALTER TABLE core."session_feedback" ADD CONSTRAINT "fk_session_feedback_appointment"
    FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT;
ALTER TABLE core."session_feedback" DROP CONSTRAINT IF EXISTS "fk_session_feedback_patient";
ALTER TABLE core."session_feedback" ADD CONSTRAINT "fk_session_feedback_patient"
    FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."session_feedback" DROP CONSTRAINT IF EXISTS "fk_session_feedback_clinic";
ALTER TABLE core."session_feedback" ADD CONSTRAINT "fk_session_feedback_clinic"
    FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."session_feedback" DROP CONSTRAINT IF EXISTS "fk_session_feedback_doctor";
ALTER TABLE core."session_feedback" ADD CONSTRAINT "fk_session_feedback_doctor"
    FOREIGN KEY ("doctor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."session_feedback" DROP CONSTRAINT IF EXISTS "fk_session_feedback_protocol";
ALTER TABLE core."session_feedback" ADD CONSTRAINT "fk_session_feedback_protocol"
    FOREIGN KEY ("protocol_id") REFERENCES core."protocol_plan" ("protocol_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_session_feedback_patient
    ON core."session_feedback" USING btree ("patient_id", "sent_at" DESC);
CREATE INDEX IF NOT EXISTS idx_session_feedback_clinic
    ON core."session_feedback" USING btree ("clinic_id", "sent_at" DESC);
CREATE INDEX IF NOT EXISTS idx_session_feedback_untagged
    ON core."session_feedback" USING btree ("sent_at") WHERE ("comment" IS NOT NULL AND "tagged_at" IS NULL);

DROP TRIGGER IF EXISTS trg_audit_session_feedback ON core."session_feedback";
CREATE TRIGGER trg_audit_session_feedback
    AFTER INSERT OR DELETE OR UPDATE ON core."session_feedback"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('feedback_id');

-- RLS. The patient submits feedback only for their own appointment and reads
-- their own. Admins and system read. Only system updates (comment tagging).
-- The app also checks the appointment is completed and inside the 48-hour window.
ALTER TABLE core."session_feedback" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."session_feedback" FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_session_feedback_select" ON core."session_feedback";
CREATE POLICY "rls_session_feedback_select" ON core."session_feedback" FOR SELECT TO public
    USING (
        ("patient_id" = rls_user_id())
        OR (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
    );

DROP POLICY IF EXISTS "rls_session_feedback_insert" ON core."session_feedback";
CREATE POLICY "rls_session_feedback_insert" ON core."session_feedback" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = 'patient'
        AND "patient_id" = rls_user_id()
        AND EXISTS (
            SELECT 1 FROM core."appointments" a
            WHERE a."appointment_id" = "session_feedback"."appointment_id"
              AND a."patient_id" = rls_user_id()
              AND a."clinic_id" = "session_feedback"."clinic_id"
        )
    );

DROP POLICY IF EXISTS "rls_session_feedback_update" ON core."session_feedback";
CREATE POLICY "rls_session_feedback_update" ON core."session_feedback" FOR UPDATE TO public
    USING (rls_user_role() = 'system')
    WITH CHECK (rls_user_role() = 'system');

GRANT SELECT, INSERT ON core."session_feedback" TO anava_app;
-- System may only add the AI tags afterwards.
GRANT UPDATE ("comment_tags", "sentiment", "tagged_at") ON core."session_feedback" TO anava_app;
REVOKE DELETE ON core."session_feedback" FROM anava_app;
GRANT SELECT ON core."session_feedback" TO anava_readonly;
GRANT SELECT ON core."session_feedback" TO anava_compliance;


-- ###########################################################################
-- 5  ops.ai_usage_log
-- ###########################################################################

CREATE TABLE IF NOT EXISTS ops."ai_usage_log" (
    "usage_id"       UUID NOT NULL DEFAULT gen_random_uuid(),
    "provider"       TEXT NOT NULL,
    "model"          TEXT NOT NULL,
    "purpose"        TEXT NOT NULL,
    "outcome"        TEXT NOT NULL,
    "input_tokens"   INTEGER,
    "output_tokens"  INTEGER,
    "latency_ms"     INTEGER,
    "error_code"     TEXT,
    "created_at"     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "ai_usage_log_pkey" PRIMARY KEY ("usage_id"),
    CONSTRAINT "chk_ai_usage_log_provider" CHECK ("provider" IN ('gemini', 'claude')),
    CONSTRAINT "chk_ai_usage_log_purpose" CHECK ("purpose" IN ('assistant_reply', 'feedback_tags')),
    CONSTRAINT "chk_ai_usage_log_outcome"
        CHECK ("outcome" IN ('ok', 'blocked_output', 'rate_limited', 'quota_exhausted', 'error', 'timeout')),
    CONSTRAINT "chk_ai_usage_log_tokens"
        CHECK (("input_tokens" IS NULL OR "input_tokens" >= 0) AND ("output_tokens" IS NULL OR "output_tokens" >= 0))
);

COMMENT ON TABLE ops."ai_usage_log" IS
    'One row per call to an external AI model. No patient identifiers or text. Used for the per-day and per-minute quota checks and the cost case for a paid plan. Append-only.';
COMMENT ON COLUMN ops."ai_usage_log"."outcome" IS
    'ok, blocked_output (failed our output check, fallback shown), rate_limited / quota_exhausted (provider refused, fallback shown), error, timeout.';

CREATE INDEX IF NOT EXISTS idx_ai_usage_log_created
    ON ops."ai_usage_log" USING btree ("created_at", "provider");

-- RLS. Written and read only under role system (the backend sets it for
-- these statements). Admins read for the cost report.
ALTER TABLE ops."ai_usage_log" ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops."ai_usage_log" FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_ai_usage_log_select" ON ops."ai_usage_log";
CREATE POLICY "rls_ai_usage_log_select" ON ops."ai_usage_log" FOR SELECT TO public
    USING (rls_user_role() = ANY (ARRAY['super_admin'::text, 'system'::text]));

DROP POLICY IF EXISTS "rls_ai_usage_log_insert" ON ops."ai_usage_log";
CREATE POLICY "rls_ai_usage_log_insert" ON ops."ai_usage_log" FOR INSERT TO public
    WITH CHECK (rls_user_role() = 'system');

GRANT SELECT, INSERT ON ops."ai_usage_log" TO anava_app;
REVOKE UPDATE, DELETE ON ops."ai_usage_log" FROM anava_app;
GRANT SELECT ON ops."ai_usage_log" TO anava_readonly;

COMMIT;


-- VERIFY
--  SELECT count(*) FROM pg_policies
--   WHERE tablename IN ('assistant_faq', 'assistant_consents', 'assistant_messages', 'session_feedback', 'ai_usage_log');  -- 13
--  SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
--   WHERE relname IN ('assistant_faq', 'assistant_consents', 'assistant_messages', 'session_feedback', 'ai_usage_log');   -- all t, t
--  -- A skipped form with an answer must fail:
--  -- INSERT INTO core.session_feedback (..., status, answers) VALUES (..., 'skipped', '{"F1": 5}');   -- chk_session_feedback_skipped
