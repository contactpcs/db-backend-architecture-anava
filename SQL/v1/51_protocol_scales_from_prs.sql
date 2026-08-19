-- 51_protocol_scales_from_prs.sql
--
-- Makes the PRS catalogue the source of assessment scales for the Treatment
-- Protocol wizard, and retires Autism Spectrum Disorder from the neuromod
-- condition list.
--
-- SCOPE: one mapping table, one condition deactivated, and the FK on
-- core.protocol_scales repointed. No rows are deleted.
--
-- APPLY ORDER: after 50.
--
-- NOT YET EXECUTED ANYWHERE.
--
--
-- ###########################################################################
-- WHY
-- ###########################################################################
--
-- Step 6 of the wizard offers scales from reference.neuromod_scales, seeded by
-- 40 from the tDCS mapping sheet. The PRS module has its own, larger catalogue
-- — reference.prs_scales (41 rows) mapped to reference.prs_diseases (14) — and
-- that is the one that actually produces patient questionnaires. A protocol
-- prescribing a scale the PRS engine does not have prescribes nothing.
--
-- The two catalogues barely overlap. Of the 13 scales 40 seeded, only 5 exist
-- in prs_scales:
--
--     GAD-7, MADRS, BDI-II, VAS         exact match
--     ASRS -> ASRS-v1.1                 same instrument, different code
--     BPI, CAARS, CARS-2, HAM-A, PCS,
--     PHQ-9, SRS-2, Y-BOCS              NOT IN PRS AT ALL
--
-- PHQ-9 is the one that matters most: it is the primary depression instrument
-- the wizard shows by default, and the PRS engine has never had it. Every
-- protocol prescribing PHQ-9 has queued a questionnaire that cannot be
-- rendered.
--
-- So this is not a code mapping exercise. reference.neuromod_scales and
-- reference.neuromod_condition_scales stop being the source; prs_scales and
-- prs_disease_scale_map become it.
--
--
-- ###########################################################################
-- THE TWO CATALOGUES DO NOT LINE UP, AND THAT IS RECORDED HERE
-- ###########################################################################
--
--   neuromod condition          prs_diseases
--   -------------------------   -------------------------------
--   Depression                  Depression/Anxiety   (shared)
--   Anxiety Disorders           Depression/Anxiety   (shared)
--   Chronic Pain                Chronic Pain
--   ADHD                        ADHD
--   Autism Spectrum Disorder    — none —
--
-- Depression and Anxiety Disorders both resolve to the single PRS disease
-- 'Depression/Anxiety'. A doctor selecting Depression is therefore offered
-- GAD-7 alongside MADRS and PSQI. That is what the PRS catalogue models: it
-- treats the two as one disease, and inventing a subset here would mean this
-- file deciding which instruments belong to depression rather than anxiety —
-- a clinical judgement that belongs in the PRS catalogue, not in a mapping
-- table.
--
-- Autism Spectrum Disorder has no PRS disease, so it can no longer be
-- prescribed against. §2 deactivates it rather than deleting it: 17 rows
-- across five reference tables point at it (diagnoses, placements, dosing,
-- condition_scales, dosing_unspecified_notes) and every FK is ON DELETE
-- RESTRICT. Nothing clinical uses it — core.protocol_conditions has zero rows
-- for it — so is_active = false removes it from the picker while leaving the
-- catalogue history intact.


BEGIN;


-- ###########################################################################
-- 1  neuromod condition -> PRS disease
-- ###########################################################################

CREATE TABLE IF NOT EXISTS reference."neuromod_condition_prs_diseases" (
    "map_id"       UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id" UUID NOT NULL,
    -- reference.prs_diseases.disease_id is TEXT ('DEPRESSION/ANXIETY/2026'),
    -- not a UUID. Matching that type is what lets the wizard join straight
    -- through to prs_disease_scale_map.
    "disease_id"   TEXT NOT NULL,
    "created_at"   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE reference."neuromod_condition_prs_diseases" IS
    'Bridges the neuromod condition catalogue to the PRS disease catalogue, so wizard step 6 can offer the scales the PRS engine will actually render. Many-to-one on purpose: Depression and Anxiety Disorders both map to the single PRS disease Depression/Anxiety.';

ALTER TABLE reference."neuromod_condition_prs_diseases"
    DROP CONSTRAINT IF EXISTS "neuromod_condition_prs_diseases_pkey" CASCADE;
ALTER TABLE reference."neuromod_condition_prs_diseases"
    ADD CONSTRAINT "neuromod_condition_prs_diseases_pkey" PRIMARY KEY ("map_id");

ALTER TABLE reference."neuromod_condition_prs_diseases"
    DROP CONSTRAINT IF EXISTS "uq_ncpd_condition_disease";
ALTER TABLE reference."neuromod_condition_prs_diseases"
    ADD CONSTRAINT "uq_ncpd_condition_disease" UNIQUE ("condition_id", "disease_id");

ALTER TABLE reference."neuromod_condition_prs_diseases"
    DROP CONSTRAINT IF EXISTS "fk_ncpd_condition";
ALTER TABLE reference."neuromod_condition_prs_diseases"
    ADD CONSTRAINT "fk_ncpd_condition"
    FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;

ALTER TABLE reference."neuromod_condition_prs_diseases"
    DROP CONSTRAINT IF EXISTS "fk_ncpd_disease";
ALTER TABLE reference."neuromod_condition_prs_diseases"
    ADD CONSTRAINT "fk_ncpd_disease"
    FOREIGN KEY ("disease_id") REFERENCES reference."prs_diseases" ("disease_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_ncpd_condition
    ON reference."neuromod_condition_prs_diseases" USING btree ("condition_id");

-- Matched on name rather than a hardcoded UUID: condition_id is generated per
-- database (40 uses gen_random_uuid), so the literals differ between RDS and
-- any freshly provisioned copy.
INSERT INTO reference."neuromod_condition_prs_diseases" ("condition_id", "disease_id")
SELECT c.condition_id, v.disease_id
FROM (VALUES
    ('Depression',        'DEPRESSION/ANXIETY/2026'),
    ('Anxiety Disorders', 'DEPRESSION/ANXIETY/2026'),
    ('Chronic Pain',      'CHRONICPAIN/2026'),
    ('ADHD',              'ADHD/2026')
) AS v(condition_name, disease_id)
JOIN reference."neuromod_conditions" c ON c.condition_name = v.condition_name
WHERE EXISTS (SELECT 1 FROM reference."prs_diseases" d WHERE d.disease_id = v.disease_id)
ON CONFLICT ("condition_id", "disease_id") DO NOTHING;


-- ###########################################################################
-- 2  Retire Autism Spectrum Disorder
-- ###########################################################################
-- Deactivated, not deleted — see the header. list_conditions already filters
-- on is_active, so this removes it from the wizard with no code change.

UPDATE reference."neuromod_conditions"
   SET "is_active" = FALSE, "updated_at" = now()
 WHERE "condition_name" = 'Autism Spectrum Disorder';

-- Its catalogue rows go with it, so a stale client cannot prescribe against
-- them either.
UPDATE reference."tdcs_placements" SET "is_active" = FALSE, "updated_at" = now()
 WHERE "condition_id" IN (SELECT condition_id FROM reference."neuromod_conditions" WHERE condition_name = 'Autism Spectrum Disorder');
UPDATE reference."tdcs_dosing" SET "is_active" = FALSE, "updated_at" = now()
 WHERE "condition_id" IN (SELECT condition_id FROM reference."neuromod_conditions" WHERE condition_name = 'Autism Spectrum Disorder');


-- ###########################################################################
-- 3  core.protocol_scales now references the PRS catalogue
-- ###########################################################################
-- The column was scale_id UUID -> reference.neuromod_scales. PRS scale ids are
-- TEXT ('GAD-7/2026'), so the column type changes with the FK.
--
-- Safe as a straight swap only because protocol_scales holds rows written
-- against the old catalogue. Those are preserved: prs_scale_id is added
-- alongside, the old column is kept as neuromod_scale_id, and nothing is
-- dropped. A protocol written before this file keeps pointing at the scale it
-- was actually prescribed with.

ALTER TABLE core."protocol_scales"
    ADD COLUMN IF NOT EXISTS "prs_scale_id" TEXT;

COMMENT ON COLUMN core."protocol_scales"."prs_scale_id" IS
    'The PRS scale this protocol prescribes — reference.prs_scales.scale_id, the catalogue the questionnaire engine actually renders from. New rows set this; scale_id (neuromod_scales) is kept for rows written before 51.';

ALTER TABLE core."protocol_scales"
    DROP CONSTRAINT IF EXISTS "fk_protocol_scales_prs_scale";
ALTER TABLE core."protocol_scales"
    ADD CONSTRAINT "fk_protocol_scales_prs_scale"
    FOREIGN KEY ("prs_scale_id") REFERENCES reference."prs_scales" ("scale_id") ON DELETE RESTRICT
    NOT VALID;

-- scale_id becomes optional: a row now carries one or the other.
ALTER TABLE core."protocol_scales" ALTER COLUMN "scale_id" DROP NOT NULL;

ALTER TABLE core."protocol_scales"
    DROP CONSTRAINT IF EXISTS "chk_protocol_scales_one_catalogue";
ALTER TABLE core."protocol_scales"
    ADD CONSTRAINT "chk_protocol_scales_one_catalogue"
    CHECK (num_nonnulls("scale_id", "prs_scale_id") = 1)
    NOT VALID;

-- The old UNIQUE (protocol_id, scale_id) no longer covers PRS-sourced rows,
-- which would otherwise let the same questionnaire be prescribed twice on one
-- protocol and released to the patient twice on the same trigger.
DROP INDEX IF EXISTS core.uq_protocol_scales_prs;
CREATE UNIQUE INDEX uq_protocol_scales_prs
    ON core."protocol_scales" ("protocol_id", "prs_scale_id")
    WHERE "prs_scale_id" IS NOT NULL;


COMMIT;


-- ###########################################################################
-- 4  Validate
-- ###########################################################################

ALTER TABLE core."protocol_scales" VALIDATE CONSTRAINT "fk_protocol_scales_prs_scale";
ALTER TABLE core."protocol_scales" VALIDATE CONSTRAINT "chk_protocol_scales_one_catalogue";


-- ###########################################################################
-- VERIFY — run after COMMIT
-- ###########################################################################
--
-- Four mappings, ASD gone from the picker:
--
-- SELECT c.condition_name, m.disease_id
--   FROM reference.neuromod_condition_prs_diseases m
--   JOIN reference.neuromod_conditions c USING (condition_id)
--  ORDER BY c.display_order;
--
-- SELECT condition_name, is_active FROM reference.neuromod_conditions
--  ORDER BY display_order;                          -- ASD must be false
--
-- What step 6 will now offer for Depression:
--
-- SELECT s.scale_id, s.scale_code, s.scale_name, dm.display_order, dm.is_required
--   FROM reference.neuromod_condition_prs_diseases m
--   JOIN reference.prs_disease_scale_map dm ON dm.disease_id = m.disease_id
--   JOIN reference.prs_scales s            ON s.scale_id     = dm.scale_id
--   JOIN reference.neuromod_conditions c   ON c.condition_id = m.condition_id
--  WHERE c.condition_name = 'Depression'
--  ORDER BY dm.display_order;
--
--
-- ###########################################################################
-- STILL OPEN
-- ###########################################################################
--
--   1. reference.neuromod_scales and reference.neuromod_condition_scales are
--      now unused by the wizard but left in place — core.protocol_scales rows
--      written before this file still point at them. Dropping them is a
--      separate change, after those rows are migrated or aged out.
--
--   2. reference.neuromod_scales.prs_scale_id stays NULL. It was the obvious
--      bridge, but only 5 of 13 rows have a PRS counterpart, so a join through
--      it would silently hide the other 8 rather than fail — the same
--      silent-empty-result shape this module has hit repeatedly. The mapping
--      table in §1 joins at the DISEASE level instead, where the catalogues
--      genuinely correspond.
-- ###########################################################################
