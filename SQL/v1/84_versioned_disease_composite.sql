-- 84_versioned_disease_composite.sql
--
-- APPLY ORDER: after 83 (reference.scoring_logic_versions must exist).
--
-- THE PROBLEM
--
-- Anava_Doctor_Dashboard_Backend_Provisions_v1.docx (TL review, Sept 2026)
-- flagged this session's disease-composite work as too simple for
-- production: reference.prs_disease_scale_map.weight_pct (79/81) is a
-- single mutable column — if a disease's weights ever change (e.g. ADHD
-- once ASRS-v1.1/EQ-5D-5L resolve), every historical composite already
-- shown to a doctor becomes indistinguishable from one computed under the
-- new weights. core.prs_final_results.composite_score is recomputed/
-- overwritten in place per instance, not an append-only history. Baseline
-- ("first-ever completed assessment for a condition") is recalculated live
-- on every dashboard read instead of a stable, persisted pointer.
--
-- THE FIX
--
-- Rather than 4 new tables, this reuses/widens 83's reference.scoring_
-- logic_versions (which already versions a SCALE's scoring code) to also
-- version a DISEASE's composite weight table, and folds "baseline" into a
-- single new core.disease_composite_scores table as one boolean flag
-- (a baseline is just "the earliest computed composite row, flagged") —
-- see Documents/Anava_Doctor_Dashboard_Backend_Provisions_v1.docx Section
-- 4 and the Decision Register (Section 6), all 5 recommendations adopted.
--
-- Adopted decisions:
--   1. Composite basis: as-of-latest-per-scale, not per-sitting (Phase 2,
--      app/modules/prs/disease_scoring.py — this file is schema only).
--   2. Recompute policy: never silently recompute historical composites —
--      enforced by disease_composite_scores being append-only.
--   3. Reports API stays core operational, RLS-scoped (confirms the module
--      choice already made: app/modules/reports).
--   4. Baseline = earliest completed instance regardless of stage, tie-
--      broken by earliest completed_at then lowest instance_id.
--   5. Device-session PRS excluded from the main trend (Phase 2 concern —
--      the as-of query only looks at cycle-current, non-device instances;
--      device-session tagging is out of scope for this migration since
--      assessment_stage today has no device_session value to filter on).

BEGIN;

-- ===========================================================================
-- 1a. Widen reference.scoring_logic_versions to also carry a disease's
--     composite weight table, versioned the same way as a scale's code.
-- ===========================================================================

ALTER TABLE reference."scoring_logic_versions" ALTER COLUMN "scale_code" DROP NOT NULL;

ALTER TABLE reference."scoring_logic_versions"
    ADD COLUMN "disease_id" TEXT REFERENCES reference."prs_diseases" ("disease_id") ON DELETE RESTRICT;

-- Exactly one of scale_code/disease_id per row — a row versions either one
-- scale's code or one disease's weight table, never both/neither.
ALTER TABLE reference."scoring_logic_versions"
    ADD CONSTRAINT "chk_scoring_logic_versions_one_key" CHECK (num_nonnulls("scale_code", "disease_id") = 1);

ALTER TABLE reference."scoring_logic_versions" DROP CONSTRAINT "chk_scoring_logic_kind";
ALTER TABLE reference."scoring_logic_versions"
    ADD CONSTRAINT "chk_scoring_logic_kind" CHECK ("logic_kind" IN ('config', 'special_scorer', 'disease_composite_weights'));

-- 'disease_composite_weights' shares 'config'-kind's shape (JSONB config,
-- no source_code) — the weight table is data, same as a flat-sum scale's
-- SCALE_CONFIG entry, never literal source code.
ALTER TABLE reference."scoring_logic_versions" DROP CONSTRAINT "chk_scoring_logic_shape";
ALTER TABLE reference."scoring_logic_versions"
    ADD CONSTRAINT "chk_scoring_logic_shape" CHECK (
        ("logic_kind" = 'config' AND "config" IS NOT NULL AND "source_code" IS NULL)
        OR ("logic_kind" = 'special_scorer' AND "source_code" IS NOT NULL AND "config" IS NULL)
        OR ("logic_kind" = 'disease_composite_weights' AND "config" IS NOT NULL AND "source_code" IS NULL)
    );

-- Twin of the existing (scale_code, version) uniqueness — NULLs don't
-- collide in Postgres UNIQUE constraints, so this coexists safely with the
-- pre-existing uq_scoring_logic_versions_scale_version.
ALTER TABLE reference."scoring_logic_versions"
    ADD CONSTRAINT "uq_scoring_logic_versions_disease_version" UNIQUE ("disease_id", "version");

-- Twin of 83's uq_scoring_logic_versions_active (which is scoped to
-- scale_code and does NOT protect disease_id rows — a plain unique index on
-- scale_code lets unlimited disease-only rows through since NULL never
-- collides with NULL). Exactly one active weight version per disease.
CREATE UNIQUE INDEX "uq_scoring_logic_versions_active_disease"
    ON reference."scoring_logic_versions" ("disease_id")
    WHERE "is_active" AND "disease_id" IS NOT NULL;

-- ===========================================================================
-- 1b. core.disease_composite_scores — append-only computed-composite
--     history, with is_baseline absorbing what would otherwise be a
--     separate baseline-pointer table.
-- ===========================================================================

CREATE TABLE core."disease_composite_scores" (
    "composite_id"               UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    "patient_id"                 UUID NOT NULL REFERENCES core."profiles" ("id") ON DELETE RESTRICT,
    "disease_id"                 TEXT NOT NULL REFERENCES reference."prs_diseases" ("disease_id") ON DELETE RESTRICT,
    "formula_version_id"         UUID NOT NULL REFERENCES reference."scoring_logic_versions" ("id") ON DELETE RESTRICT,
    "calculated_value"           NUMERIC NOT NULL,
    "severity_level"             TEXT,
    "severity_label"             TEXT,
    "is_provisional"             BOOLEAN NOT NULL DEFAULT FALSE,
    -- {scale_code: instance_id} — which sitting each contributing scale's
    -- value came from; the as-of-latest-per-scale audit trail (Decision 1).
    "contributing_instance_ids"  JSONB NOT NULL DEFAULT '{}'::jsonb,
    "is_baseline"                BOOLEAN NOT NULL DEFAULT FALSE,
    "computed_at"                TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At most one baseline row per patient+disease, DB-enforced (same pattern
-- as the active-version indexes above) — Decision 4 / Edge Case 3.
CREATE UNIQUE INDEX "uq_disease_composite_scores_baseline"
    ON core."disease_composite_scores" ("patient_id", "disease_id")
    WHERE "is_baseline";

CREATE INDEX "idx_disease_composite_scores_patient_disease"
    ON core."disease_composite_scores" ("patient_id", "disease_id", "computed_at" DESC);

ALTER TABLE core."disease_composite_scores" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."disease_composite_scores" FORCE ROW LEVEL SECURITY;

-- Same shape as core.prs_scale_results' RLS (17_rls_policies.sql,
-- rls_psr_select/insert/update) — keyed on patient_id directly here instead
-- of an instance_id subquery, since this table isn't instance-scoped.
CREATE POLICY "rls_dcs_select" ON core."disease_composite_scores" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin', 'regional_admin']))
        OR ("patient_id" = rls_user_id())
        OR (
            (rls_user_role() = ANY (ARRAY['clinic_admin', 'doctor', 'clinical_assistant', 'receptionist']))
            AND ("patient_id" IN (SELECT "profile_id" FROM core."patients" WHERE "primary_clinic_id" = rls_clinic_id()))
        )
    );

CREATE POLICY "rls_dcs_insert" ON core."disease_composite_scores" FOR INSERT TO public
    WITH CHECK (rls_user_role() = ANY (ARRAY['super_admin', 'clinic_admin', 'clinical_assistant', 'doctor']));

-- UPDATE only ever flips is_baseline on an existing row (Edge Case 7 —
-- reassigning baseline after a void) — never touches calculated_value or
-- any other already-computed field.
CREATE POLICY "rls_dcs_update" ON core."disease_composite_scores" FOR UPDATE TO public
    USING (rls_user_role() = ANY (ARRAY['super_admin', 'doctor']));

GRANT SELECT, INSERT, UPDATE ON core."disease_composite_scores" TO anava_app;
GRANT SELECT ON core."disease_composite_scores" TO anava_readonly;

-- ===========================================================================
-- 1c. Void/supersede mechanism on prs_assessment_instances (Edge Case 7) —
--     nothing like this exists today; the as-of compute layer can't honor
--     "never count a voided instance" without it.
-- ===========================================================================

ALTER TABLE core."prs_assessment_instances" ADD COLUMN "is_voided" BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE core."prs_assessment_instances" ADD COLUMN "voided_at" TIMESTAMPTZ;
ALTER TABLE core."prs_assessment_instances" ADD COLUMN "voided_by" UUID REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."prs_assessment_instances" ADD COLUMN "voided_reason" TEXT;

COMMIT;

-- ###########################################################################
-- BACKFILL — run after COMMIT, so today's real data isn't lost or reset.
-- ###########################################################################

BEGIN;

-- One disease_composite_weights version-1 row per disease that has any
-- seeded weight today, config = {"weights": {scale_code: weight_pct}}.
INSERT INTO reference."scoring_logic_versions" (disease_id, version, logic_kind, config, is_active, change_reason)
SELECT
    m.disease_id,
    1,
    'disease_composite_weights',
    jsonb_build_object('weights', jsonb_object_agg(sc.scale_code, m.weight_pct)),
    TRUE,
    'Backfilled from reference.prs_disease_scale_map.weight_pct (SQL/v1/81) — first versioned formula for this disease.'
FROM reference."prs_disease_scale_map" m
JOIN reference."prs_scales" sc ON sc.scale_id = m.scale_id
WHERE m.weight_pct IS NOT NULL
GROUP BY m.disease_id;

-- disease_composite_scores backfilled from prs_final_results.composite_score
-- (this session's work) — one row per completed instance that already has a
-- computed composite, tagged against the new version-1 row for that
-- instance's disease. is_baseline set inline via ROW_NUMBER() (earliest
-- completed_at, tie-broken by lowest instance_id — Decision 4 / Edge Case
-- 3) rather than a second pass matched by score VALUE, which would be
-- wrong if two instances ever happened to share an identical score.
INSERT INTO core."disease_composite_scores"
    (patient_id, disease_id, formula_version_id, calculated_value, severity_level, severity_label, is_provisional, computed_at, is_baseline)
SELECT
    pai.patient_id,
    pai.disease_id,
    slv.id,
    pfr.composite_score,
    pfr.composite_severity_level,
    pfr.composite_severity_label,
    FALSE,
    pfr.time_stamp,
    ROW_NUMBER() OVER (PARTITION BY pai.patient_id, pai.disease_id ORDER BY pai.completed_at ASC, pai.instance_id ASC) = 1
FROM core."prs_final_results" pfr
JOIN core."prs_assessment_instances" pai ON pai.instance_id = pfr.instance_id
JOIN reference."scoring_logic_versions" slv
    ON slv.disease_id = pai.disease_id AND slv.logic_kind = 'disease_composite_weights' AND slv.is_active
WHERE pfr.composite_score IS NOT NULL AND pai.disease_id IS NOT NULL AND pai.status = 'completed';

COMMIT;

-- ###########################################################################
-- VERIFICATION — run after both transactions commit
-- ###########################################################################
-- 1) Exactly one active disease_composite_weights version per disease:
--
--   SELECT disease_id, COUNT(*) FROM reference.scoring_logic_versions
--   WHERE logic_kind = 'disease_composite_weights' AND is_active
--   GROUP BY disease_id HAVING COUNT(*) <> 1;
--
--   Expect ZERO rows back.
--
-- 2) Exactly one baseline row per patient+disease that has any composite:
--
--   SELECT patient_id, disease_id, COUNT(*) FROM core.disease_composite_scores
--   WHERE is_baseline GROUP BY patient_id, disease_id HAVING COUNT(*) > 1;
--
--   Expect ZERO rows back.
--
-- 3) Spot-check against a known instance (57f2d25d-3ac8e210, CHRONICPAIN,
--    verified this session as composite_score=29.42, Mild):
--
--   SELECT * FROM core.disease_composite_scores
--   WHERE patient_id = '57f2d25d-a944-4f61-82b4-3bb766159f5e' AND disease_id = 'CHRONICPAIN/2026';
--
--   Expect calculated_value = 29.42, severity_label = 'Mild', is_baseline = true
--   (this was this patient's first and only CHRONICPAIN instance).
