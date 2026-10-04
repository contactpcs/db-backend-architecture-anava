-- 103_live_notify_tables.sql
--
-- The two tables that replace Redis for logged-out access tokens and
-- one-time live-stream tickets (design: Documents/design_postgres_notify_live.md).
-- Live pushes themselves move to pg_notify and need no schema.
--
-- SCOPE: two new tables with RLS and grants. No existing table changes.
--
-- APPLY ORDER: after 102, and BEFORE deploying the backend that uses them.
-- Safe under the old backend, which never touches them.
--
-- Rows are short-lived (a token until it would have expired anyway, at most an
-- hour; a ticket for seconds). The app deletes expired rows as it writes new
-- ones, so neither table grows.


-- An access token that was logged out before it expired.
CREATE TABLE IF NOT EXISTS ops."revoked_access_tokens" (
    "jti"        TEXT NOT NULL,
    "expires_at" TIMESTAMPTZ NOT NULL,
    CONSTRAINT "revoked_access_tokens_pkey" PRIMARY KEY ("jti")
);

COMMENT ON TABLE ops."revoked_access_tokens" IS
    'Access tokens logged out before their expiry. Every API process keeps a copy in memory (app/core/live.py).';


-- A one-time ticket for opening the live stream. Only its SHA-256 hash is
-- stored, so a row read from here cannot be used to open a stream.
CREATE TABLE IF NOT EXISTS ops."sse_tickets" (
    "ticket_hash" TEXT NOT NULL,
    "cognito_sub" TEXT NOT NULL,
    "expires_at"  TIMESTAMPTZ NOT NULL,
    CONSTRAINT "sse_tickets_pkey" PRIMARY KEY ("ticket_hash")
);

COMMENT ON TABLE ops."sse_tickets" IS
    'One-time tickets for GET /events/stream, stored as SHA-256 hashes. Deleted on use.';


-- RLS. Only role 'system', which the app sets for these statements
-- (core/auth_session.py). A ticket is consumed before the caller's identity
-- is known, so no per-user rule is possible.
ALTER TABLE ops."revoked_access_tokens" ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops."revoked_access_tokens" FORCE  ROW LEVEL SECURITY;
ALTER TABLE ops."sse_tickets" ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops."sse_tickets" FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_revoked_access_tokens_system" ON ops."revoked_access_tokens";
CREATE POLICY "rls_revoked_access_tokens_system" ON ops."revoked_access_tokens" FOR ALL TO public
    USING (rls_user_role() = 'system')
    WITH CHECK (rls_user_role() = 'system');

DROP POLICY IF EXISTS "rls_sse_tickets_system" ON ops."sse_tickets";
CREATE POLICY "rls_sse_tickets_system" ON ops."sse_tickets" FOR ALL TO public
    USING (rls_user_role() = 'system')
    WITH CHECK (rls_user_role() = 'system');


GRANT SELECT, INSERT, DELETE ON ops."revoked_access_tokens" TO anava_app;
GRANT SELECT, INSERT, DELETE ON ops."sse_tickets" TO anava_app;
REVOKE UPDATE ON ops."revoked_access_tokens" FROM anava_app;
REVOKE UPDATE ON ops."sse_tickets" FROM anava_app;


-- VERIFY
--  SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
--  WHERE relname IN ('revoked_access_tokens', 'sse_tickets');             -- both true/true
--  SELECT count(*) FROM ops.sse_tickets WHERE expires_at < now() - interval '1 hour';   -- 0 once in use
