-- Audit log for PRS scale scoring logic (app/modules/prs/scoring_rules.py).
-- Deliberately passive: scoring always executes straight from that file —
-- nothing here is read at scoring time. This table exists so every change
-- to a scale's formula/config is a permanent, append-only, timestamped
-- record, and every scored result can be traced back to exactly the
-- version that produced it. Rollback to an old version means restoring
-- scoring_rules.py to match an old row's content, then logging that as a
-- new version — history is never edited or deleted, only appended to.
CREATE TABLE reference."scoring_logic_versions" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    "scale_code" TEXT NOT NULL REFERENCES reference."prs_scales" ("scale_code") ON DELETE RESTRICT,
    "version" INTEGER NOT NULL,
    -- 'config': the 30 flat-sum scales — SCALE_CONFIG[scale_code] (+ its
    -- _RISK_THRESHOLDS entry, if any) snapshotted as JSON.
    -- 'special_scorer': the 11 scales with real scoring code (COMPASS-31,
    -- PSQI, DASS-21, FIQR, MSQ, PainDETECT, SNAP-IV, MFIS, SS-QOL, SLEEP-50,
    -- ASRS-v1.1) — the scorer function's literal source text.
    "logic_kind" TEXT NOT NULL,
    "config" JSONB,
    "source_code" TEXT,
    -- Commit hash of scoring_rules.py at the time this version was logged —
    -- the actual pointer to the executable code; config/source_code above
    -- are a readable snapshot for convenience, not a substitute for git.
    "git_commit" TEXT,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "changed_by" UUID REFERENCES core."profiles" ("id") ON DELETE RESTRICT,
    "change_reason" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_scoring_logic_kind" CHECK ("logic_kind" IN ('config', 'special_scorer')),
    CONSTRAINT "chk_scoring_logic_shape" CHECK (
        ("logic_kind" = 'config' AND "config" IS NOT NULL AND "source_code" IS NULL)
        OR ("logic_kind" = 'special_scorer' AND "source_code" IS NOT NULL AND "config" IS NULL)
    ),
    CONSTRAINT "uq_scoring_logic_versions_scale_version" UNIQUE ("scale_code", "version")
);

-- Exactly one active version per scale at any time.
CREATE UNIQUE INDEX "uq_scoring_logic_versions_active" ON reference."scoring_logic_versions" ("scale_code") WHERE "is_active";
CREATE INDEX "idx_scoring_logic_versions_scale" ON reference."scoring_logic_versions" ("scale_code");

ALTER TABLE reference."scoring_logic_versions" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."scoring_logic_versions" FORCE ROW LEVEL SECURITY;

-- Same shape as reference.prs_scales: public read (every clinical role
-- needs to see scoring context), super_admin-only write. No UPDATE/DELETE
-- policy on purpose — this is an append-only log, same Bucket-3-style
-- pattern as the compliance tables (erasure_requests etc.): a command with
-- no matching policy just matches zero rows under FORCE RLS, not an error.
CREATE POLICY "rls_scoring_logic_versions_select" ON reference."scoring_logic_versions" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_scoring_logic_versions_write" ON reference."scoring_logic_versions" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

-- 18_grants.sql's default privileges predate this table (same gap noted in
-- 30_appointments_spine.sql for reference.billable_items) — explicit grants
-- needed. No UPDATE/DELETE grant either, for the same append-only reason.
GRANT SELECT, INSERT ON reference."scoring_logic_versions" TO anava_app;
GRANT SELECT ON reference."scoring_logic_versions" TO anava_readonly;

-- Ties every scored result to the exact version that computed it — the
-- actual audit trail, not just a changelog of the logic in isolation.
-- Nullable: existing rows predate this column and were scored before any
-- version was ever logged, so backfilling a version onto them would claim
-- a precision the data doesn't have.
ALTER TABLE core."prs_scale_results" ADD COLUMN "scoring_version_id" UUID REFERENCES reference."scoring_logic_versions" ("id") ON DELETE RESTRICT;
CREATE INDEX "idx_prs_scale_results_scoring_version" ON core."prs_scale_results" ("scoring_version_id");
