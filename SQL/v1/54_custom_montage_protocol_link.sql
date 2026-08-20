-- 54_custom_montage_protocol_link.sql
--
-- Gives core.protocol_plan a seventh placement slot — a doctor-authored
-- custom montage (core.protocol_custom_montages, 38) — alongside the six
-- catalogue placement columns, and makes dosing optional when that slot is
-- used.
--
-- SCOPE: two ALTER TABLE additions and two rewritten CHECK constraints on
-- core.protocol_plan, one new FK, one new partial index. Nothing on
-- core.protocol_custom_montages changes.
--
-- APPLY ORDER: after 53. Independent of 53's device_sessions tables.
--
-- NOT YET EXECUTED ANYWHERE.
--
--
-- ###########################################################################
-- WHY
-- ###########################################################################
--
-- protocol_custom_montages (38) lets a doctor save a montage the curated
-- reference.*_placements library doesn't have — it is validated with the
-- same electrode-shape rule a catalogue placement gets
-- (CustomMontageService.create calls the same validate_electrodes the
-- placement-picker step uses), but it was never wired into protocol_plan
-- at all: no FK either direction, and chk_treatment_protocols_one_placement
-- (32, renamed to chk_protocol_plan_one_placement by 47) only counts the six
-- reference.*_placements-backed columns. A doctor could draw a montage on
-- the 10-20 map, have it pass validation, save it — and then be stuck: the
-- wizard's own Continue button required a placement_id from the library
-- regardless.
--
-- Dosing has the same shape of problem one level deeper. Every catalogue
-- dosing row (reference.tdcs_dosing etc.) itself FKs to a specific
-- catalogue placement row (fk_tdcs_dosing_tdcs_placement_id etc.) — a
-- custom montage cannot legitimately claim any of the six dosing columns,
-- because none of them were calibrated for a placement that isn't in the
-- catalogue. This file does not invent a parallel "custom dosing" table for
-- that. It doesn't need to: protocol_plan.prescribed_current_ma /
-- prescribed_duration_min / ramp_seconds are ALREADY independent, always-
-- editable, typed columns (39) — the wizard's Step 5 lets a doctor type over
-- whatever a chosen dosing_id suggested, and fn_check_protocol_prescription_
-- complete (39) is what actually gates activation on THOSE columns being
-- filled in, not on dosing_id. dosing_id today is redundant provenance
-- ("which catalogued dose was this nominally based on") layered on top of
-- an already-sufficient manual prescription. So: when custom_montage_id is
-- set, dosing_id has nothing truthful to point at and is simply absent —
-- the manual columns alone carry the prescription, exactly as they already
-- do for every protocol regardless of path.
--
--
-- ###########################################################################
-- WHAT THIS FILE DELIBERATELY DOES NOT DO
-- ###########################################################################
--
-- It does not touch protocol_custom_montages (38) — no new column, no
-- lifecycle change. It stays exactly what 38 made it: a doctor-authored
-- montage record with its own name-uniqueness/electrode-shape/ownership
-- rules, independent of whether any protocol currently uses it.
--
-- It does not invent a "custom dosing" table or add dosing-shaped columns
-- (current_ma_min/max, etc.) to protocol_custom_montages. The manual
-- prescription columns already on protocol_plan are the intended dose
-- source for a custom-montage protocol — see WHY above.
--
-- It does not relax chk_protocol_plan_one_placement to allow zero or many —
-- exactly one of the now-seven candidates must be set, same "exactly one"
-- invariant the six-column version already enforced.
--
--
BEGIN;


-- ###########################################################################
-- 1  core.protocol_plan — the new column
-- ###########################################################################

ALTER TABLE core."protocol_plan" ADD COLUMN IF NOT EXISTS "custom_montage_id" UUID;
COMMENT ON COLUMN core."protocol_plan"."custom_montage_id" IS 'A doctor-authored montage (core.protocol_custom_montages, 38) used in place of a catalogue placement. Mutually exclusive with the six reference.*_placements columns — see chk_protocol_plan_one_placement. When set, none of the six dosing columns may be — see chk_protocol_plan_dosing_requires_catalogue_placement — the prescription lives entirely in prescribed_current_ma/prescribed_duration_min/ramp_seconds instead.';

ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "fk_protocol_plan_custom_montage_id";
ALTER TABLE core."protocol_plan"
    ADD CONSTRAINT "fk_protocol_plan_custom_montage_id"
    FOREIGN KEY ("custom_montage_id") REFERENCES core."protocol_custom_montages" ("custom_montage_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_protocol_plan_custom_montage
    ON core."protocol_plan" USING btree ("custom_montage_id") WHERE "custom_montage_id" IS NOT NULL;


-- ###########################################################################
-- 2  chk_protocol_plan_one_placement — widen to seven candidates
-- ###########################################################################

ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_protocol_plan_one_placement";
ALTER TABLE core."protocol_plan"
    ADD CONSTRAINT "chk_protocol_plan_one_placement" CHECK (
        num_nonnulls("tdcs_placement_id", "hd_tdcs_placement_id", "tavns_placement_id",
                     "tps_placement_id", "rtms_placement_id", "other_placement_id",
                     "custom_montage_id") = 1
    );


-- ###########################################################################
-- 3  chk_protocol_plan_one_dosing — <= 1, plus a biconditional against
--    custom_montage_id
-- ###########################################################################
-- Two constraints rather than one compound expression, matching this
-- schema's existing style for "N iff M" rules (e.g.
-- chk_appointments_device_session_has_device, 41): each CHECK states one
-- readable fact, and a constraint-violation error names the specific rule
-- that broke rather than one opaque boolean expression.

ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_protocol_plan_one_dosing";
ALTER TABLE core."protocol_plan"
    ADD CONSTRAINT "chk_protocol_plan_one_dosing" CHECK (
        num_nonnulls("tdcs_dosing_id", "hd_tdcs_dosing_id", "tavns_dosing_id",
                     "tps_dosing_id", "rtms_dosing_id", "other_dosing_id") <= 1
    );

ALTER TABLE core."protocol_plan" DROP CONSTRAINT IF EXISTS "chk_protocol_plan_dosing_requires_catalogue_placement";
ALTER TABLE core."protocol_plan"
    ADD CONSTRAINT "chk_protocol_plan_dosing_requires_catalogue_placement" CHECK (
        ("custom_montage_id" IS NOT NULL) = (
            num_nonnulls("tdcs_dosing_id", "hd_tdcs_dosing_id", "tavns_dosing_id",
                         "tps_dosing_id", "rtms_dosing_id", "other_dosing_id") = 0
        )
    );
COMMENT ON CONSTRAINT "chk_protocol_plan_dosing_requires_catalogue_placement" ON core."protocol_plan" IS 'A custom-montage protocol has zero dosing FKs (nothing catalogued to point at — see file 54 header); a catalogue-placement protocol still has exactly one, same as before this file. The two CHECKs together reproduce the old "exactly one dosing" rule for the catalogue path and add "exactly zero" for the custom path.';


COMMIT;


-- ###########################################################################
-- VERIFY — run after COMMIT
-- ###########################################################################
--
-- SELECT column_name FROM information_schema.columns
--  WHERE table_schema='core' AND table_name='protocol_plan' AND column_name='custom_montage_id'; -- 1 row
--
-- SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--  WHERE conrelid = 'core.protocol_plan'::regclass
--    AND conname IN ('chk_protocol_plan_one_placement', 'chk_protocol_plan_one_dosing',
--                     'chk_protocol_plan_dosing_requires_catalogue_placement', 'fk_protocol_plan_custom_montage_id');
--                                                               -- 4 rows, defs match this file
--
-- Existing catalogue-placement rows are untouched and still satisfy both
-- CHECKs (custom_montage_id NULL on all of them, so the biconditional reads
-- FALSE = FALSE):
-- SELECT count(*) FROM core.protocol_plan WHERE custom_montage_id IS NOT NULL; -- 0, until the app writes one
--
--
-- ###########################################################################
-- STILL OPEN AFTER THIS FILE
-- ###########################################################################
--
--   1. backend/app/modules/treatment_protocols/{schemas,service,repository}.py
--      — ProtocolCreate accepting custom_montage_id, ProtocolService.create()
--      branching on it, ProtocolRead/Detail hydrating it. Schema-only file,
--      ships with the matching backend commit.
--
--   2. backend/alembic/versions/0035_custom_montage_protocol_link.py — the
--      wrapper, chained after 0034 (device_session_records).
-- ###########################################################################
