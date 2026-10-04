-- 105_signup_auto_approval.sql
--
-- Schema for automatic approval of self-registered patients
-- (design: Documents/design_signup_auto_approval.md).
--
-- SCOPE: one generated column + index on core.profiles, three columns + one
-- check + one index on core.patients, one new table ops.signup_security_log.
-- DATA: existing approved patients are marked approval_method = 'manual'
-- (every approval so far was a receptionist's).
--
-- APPLY ORDER: after 104 (needs ops.normalize_email), and BEFORE deploying the
-- backend that uses these. Safe under the old backend, which ignores them.


-- The normalized form of profiles.email, kept in step by Postgres itself on
-- every insert and update, whichever code path writes the email.
-- Not unique: accounts created before this check may already share a mailbox.
ALTER TABLE core."profiles"
    ADD COLUMN IF NOT EXISTS "email_normalized" TEXT GENERATED ALWAYS AS (ops.normalize_email("email")) STORED;

CREATE INDEX IF NOT EXISTS idx_profiles_email_normalized ON core."profiles" USING btree (email_normalized);


-- approval_method  'auto' = approved by the system's checks, 'manual' = by a
--                  staff member (approved_by). NULL until approved.
-- risk_flags       the checks this registration failed, as a list of codes.
--                  Empty list = passed every check.
-- signup_ip        where the self-signup came from. NULL for staff-registered
--                  patients.
ALTER TABLE core."patients"
    ADD COLUMN IF NOT EXISTS "approval_method" TEXT,
    ADD COLUMN IF NOT EXISTS "risk_flags" JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS "signup_ip" INET;

ALTER TABLE core."patients" DROP CONSTRAINT IF EXISTS "chk_patients_approval_method";
ALTER TABLE core."patients" ADD CONSTRAINT "chk_patients_approval_method"
    CHECK ("approval_method" IS NULL OR "approval_method" IN ('auto', 'manual'));

UPDATE core."patients" SET "approval_method" = 'manual'
WHERE "approval_status" = 'approved' AND "approval_method" IS NULL;

-- "How many registrations came from this address in the last 24 hours?"
CREATE INDEX IF NOT EXISTS idx_patients_signup_ip ON core."patients" USING btree (signup_ip, registration_completed_at)
    WHERE (signup_ip IS NOT NULL);


-- One row per security decision on the self-signup path: an attempt that was
-- let through, one that was blocked, a registration that was flagged for
-- review or approved automatically. It is both the counter the rate limits
-- read and the audit trail. Append-only. Never holds a raw email or phone:
-- contact_hash is a keyed one-way hash.
CREATE TABLE IF NOT EXISTS ops."signup_security_log" (
    "log_id"       UUID NOT NULL DEFAULT gen_random_uuid(),
    "checkpoint"   TEXT NOT NULL,
    "outcome"      TEXT NOT NULL,
    "reason"       TEXT,
    "ip"           INET,
    "contact_hash" TEXT,
    "patient_id"   UUID,
    "created_at"   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "signup_security_log_pkey" PRIMARY KEY ("log_id"),
    CONSTRAINT "chk_signup_security_log_checkpoint"
        CHECK ("checkpoint" IN ('signup_start', 'signup_resend', 'otp_confirm', 'registration_complete')),
    CONSTRAINT "chk_signup_security_log_outcome"
        CHECK ("outcome" IN ('allowed', 'blocked', 'flagged', 'auto_approved'))
);

COMMENT ON TABLE ops."signup_security_log" IS
    'Every decision on the patient self-signup path: rate-limit counters and security audit trail in one. Append-only, no raw contact details.';
COMMENT ON COLUMN ops."signup_security_log"."reason" IS
    'Which rule blocked or flagged it (ip_rate_limit, disposable_email, duplicate_patient, ...). NULL when allowed or auto_approved.';

ALTER TABLE ops."signup_security_log" ADD CONSTRAINT "fk_signup_security_log_patient"
    FOREIGN KEY ("patient_id") REFERENCES core."patients" ("patient_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_signup_security_log_ip ON ops."signup_security_log" USING btree (ip, created_at);
CREATE INDEX IF NOT EXISTS idx_signup_security_log_contact ON ops."signup_security_log" USING btree (contact_hash, created_at);
CREATE INDEX IF NOT EXISTS idx_signup_security_log_patient ON ops."signup_security_log" USING btree (patient_id)
    WHERE (patient_id IS NOT NULL);


-- RLS. Only role 'system', which the app sets for these statements: the rows
-- are written on anonymous requests, before any user identity exists.
ALTER TABLE ops."signup_security_log" ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops."signup_security_log" FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_signup_security_log_system" ON ops."signup_security_log";
CREATE POLICY "rls_signup_security_log_system" ON ops."signup_security_log" FOR ALL TO public
    USING (rls_user_role() = 'system')
    WITH CHECK (rls_user_role() = 'system');

GRANT SELECT, INSERT ON ops."signup_security_log" TO anava_app;
REVOKE UPDATE, DELETE ON ops."signup_security_log" FROM anava_app;
GRANT EXECUTE ON FUNCTION ops.normalize_email(text) TO anava_app;


-- VERIFY
--  SELECT ops.normalize_email('  John.Doe+clinic@GoogleMail.com ');          -- johndoe@gmail.com
--  SELECT count(*) FROM core.profiles WHERE email_normalized IS NULL;        -- 0
--  SELECT approval_method, count(*) FROM core.patients GROUP BY 1;
--
-- WHY WAS THIS PATIENT APPROVED OR HELD?
--  SELECT approval_status, approval_method, approved_by, risk_flags, signup_ip
--  FROM core.patients WHERE patient_id = '<id>';
--  SELECT created_at, checkpoint, outcome, reason, ip
--  FROM ops.signup_security_log WHERE patient_id = '<id>' ORDER BY created_at;
--
-- MAILBOXES THAT ALREADY HAVE MORE THAN ONE ACCOUNT
--  SELECT email_normalized, count(*) FROM core.profiles GROUP BY 1 HAVING count(*) > 1;
