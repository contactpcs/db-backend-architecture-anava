-- 32_treatment_protocol.sql
--
-- Hand-written net-new DDL (like 30 and 31; unlike 00-29, which are verbatim
-- introspection of the live schema).
--
-- SCOPE: the Treatment Protocol module — the device / montage / dosing /
-- session-plan configuration a doctor sets once to start a course of treatment.
-- Applies to the live RDS database Anava_App_v1 on top of 30 and 31.
--
--
-- ###########################################################################
-- READ THIS BEFORE RUNNING
-- ###########################################################################
--
-- NOT YET EXECUTED ANYWHERE. This file has not been run against the RDS
-- instance, a staging copy, or a local PostgreSQL. Every statement is written
-- against the schema as recorded in SQL/v1/00-31, verified by reading those
-- files — not by executing this one. Run it on a restored snapshot first.
--
-- REQUIRES 30_appointments_spine.sql AND 31_appointments_payment_states.sql
-- to be applied first. This file depends on objects they create:
--   - core.appointments.plan_id           (30 §1)
--   - core.appointments.hold_expires_at   (31 §1)
--   - nullable doctor_id / start_time / end_time on appointments (30 §1)
--   - chk_appointments_hold, chk_appointments_time_pair (30 §1, 31 §1)
--
-- PREREQUISITE — search_path. The RLS policies below call rls_user_role() /
-- rls_user_id() unqualified, matching every other policy in this schema. The
-- database-level search_path set by 19_search_path.sql must already be in
-- effect on the connection that runs this file. Confirm with:
--     SHOW search_path;
-- Expect: core, reference, compliance, analytics, ops, extensions, public
--
--
-- ###########################################################################
-- WHAT CHANGED SINCE THE PROTOCOL MODULE WAS FIRST DRAFTED
-- ###########################################################################
--
-- The earlier draft (SQL/v1/erd.sql §7-8) modelled prescribed device sessions
-- in a standalone core.protocol_sessions table with no FK to any scheduling
-- table. That was correct for the architecture it was written against: the v2
-- board made appointments (doctor consultations) and treatment_sessions (tDCS,
-- CA-administered) siblings, with a deliberate "NO FOREIGN KEY / NO HIERARCHY"
-- gutter between them, and open decision 1 left the session table unsettled.
--
-- 30_appointments_spine.sql settled it the other way. core.appointments is now
-- the single spine for every clinic visit — initial, follow-up AND device
-- session — which is exactly why 30 made doctor_id, start_time and end_time
-- nullable and added appointments.plan_id.
--
-- So this file does NOT create protocol_sessions or protocol_followups. A
-- prescribed device session is an appointments row with
-- appointment_type = 'device_session' and status = 'planned'; a follow-up is an
-- appointments row with appointment_type = 'protocol_followup'. Building the standalone
-- tables now would create a second home for data the spine already owns, and
-- every "what is this patient's next visit" query would have to read both.
--
-- What survives from the earlier draft unchanged: treatment_protocols itself,
-- all 17 reference catalogue tables, the per-device placement/dosing split, the
-- electrode rules, and the two separate PRS response tables. Those were never
-- affected by the spine decision.
--
--
-- ###########################################################################
-- THE ONE THING THAT WILL BITE IF IT IS GOT WRONG
-- ###########################################################################
--
-- 31_appointments_payment_states.sql, "DELIBERATELY NOT DONE HERE" item 3,
-- addresses this workstream directly:
--
--     "the hold sweeper decides delete-vs-revert by testing
--      appointments.plan_id IS NULL. A protocol-born appointment row that
--      reaches 'selected' without plan_id set would be deleted rather than
--      reverted, taking the doctor's prescribed date with it. Setting plan_id
--      on every protocol-born spine row is what prevents that."
--
-- Every appointments row this file's generator writes sets plan_id. That is
-- load-bearing, not cosmetic: without it an unpaid slot selection destroys the
-- doctor's prescription on timeout instead of releasing the slot.
--
--
-- ###########################################################################
-- VOCABULARY
-- ###########################################################################
--
--   Treatment Plan     core.treatment_plans (existing). The superset: prior
--                      history + the active protocol + the doctor's signature.
--   Treatment Protocol core.treatment_protocols (this file). The narrower
--                      device + montage + dosing + session-plan config. A CHILD
--                      of a plan.
--
--   Setting a protocol with session_count = 20 and follow_up_every_n = 5
--   generates 24 appointments rows: 20 device sessions plus 4 follow-ups, after
--   sessions 5, 10, 15 and 20.


BEGIN;


-- ###########################################################################
-- §1  LAYER 1 — Namespace
-- ###########################################################################
-- Catalogue data goes in `reference` (static, superadmin-owned, read-mostly),
-- per-patient clinical config in `core`. Same split 30 used when it placed
-- billable_items in reference rather than a new billing schema.


-- ###########################################################################
-- §2  LAYER 1 — Tables: neuromodulation catalogue (reference)
-- ###########################################################################

-- Manufacturer. A separate table rather than a text column on neuromod_devices
-- because the clinic will run the same modality from more than one vendor: two
-- tDCS units from different manufacturers are two device rows sharing one
-- modality, and a montage or dose validated on one is not automatically valid
-- on the other. A text column would spell the same vendor three ways within a
-- year and make "which units are this vendor's" an unanswerable question.
--
-- Regulatory identifiers live here, not on the device: they are properties of
-- the company, and a device recall notice arrives addressed to a manufacturer.
CREATE TABLE IF NOT EXISTS reference."device_companies" (
    "company_id"       UUID NOT NULL DEFAULT gen_random_uuid(),
    "company_code"     TEXT NOT NULL,
    "company_name"     TEXT NOT NULL,
    "country"          TEXT,
    "website"          TEXT,
    "support_email"    TEXT,
    "support_phone"    TEXT,
    "regulatory_ids"   JSONB NOT NULL DEFAULT '{}'::jsonb,
    "is_active"        BOOLEAN NOT NULL DEFAULT true,
    "notes"            TEXT,
    "created_at"       TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"       TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE reference."device_companies" IS 'Device manufacturers. One row per company whose hardware the clinic prescribes on. Retention: Bucket 3 — a historic protocol must still name the vendor whose device delivered it, so rows are deactivated (is_active = false), never deleted.';
COMMENT ON COLUMN reference."device_companies"."company_code" IS 'Short stable handle (neuroCare, Soterix, Magstim). Unique, and the safe join key for a seed script — company_name is editable, this is not meant to be.';
COMMENT ON COLUMN reference."device_companies"."regulatory_ids" IS 'Clearance/registration identifiers as {scheme: value} — e.g. {"CE": "...", "FDA_510K": "...", "CDSCO": "..."}. jsonb because the set of schemes differs per country and per device class, and India (CDSCO) plus EU/US is already three shapes. Promote to real columns if a specific scheme ever needs to be queried or enforced.';
COMMENT ON COLUMN reference."device_companies"."is_active" IS 'false = no longer prescribable (contract ended, vendor withdrawn). Existing protocols on this vendor keep working; the device picker filters on it.';

CREATE TABLE IF NOT EXISTS reference."neuromod_devices" (
    "device_id"       UUID NOT NULL DEFAULT gen_random_uuid(),
    "company_id"      UUID,
    "device_code"     TEXT NOT NULL,
    "device_name"     TEXT NOT NULL,
    "model_number"    TEXT,
    "modality"        TEXT NOT NULL,
    "phase"           SMALLINT NOT NULL DEFAULT 2,
    "is_active"       BOOLEAN NOT NULL DEFAULT true,
    "created_at"      TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_neuromod_devices_phase" CHECK ("phase" IN (1, 2)),
    -- v2 Layer 2: text + CHECK, never a native enum. ALTER TYPE can never
    -- remove a value; a CHECK can be replaced in one statement.
    CONSTRAINT "chk_neuromod_devices_modality" CHECK ("modality" IN
        ('tDCS', 'HD-tDCS', 'taVNS', 'TPS', 'rTMS', 'other'))
);
COMMENT ON TABLE reference."neuromod_devices" IS 'Device registry. One row per (manufacturer, model) the platform can prescribe on — so the same modality may appear several times, once per vendor unit. Retention: Bucket 3 — evidences which device a historic protocol prescribed; never deleted.';
COMMENT ON COLUMN reference."neuromod_devices"."company_id" IS 'Manufacturer. Nullable ONLY so this migration can apply to a registry seeded before companies existed; every new row should set it. See OPEN ITEM 10 for the promotion to NOT NULL.';
COMMENT ON COLUMN reference."neuromod_devices"."model_number" IS 'Vendor model designation (Starstim 8, 1x1 tDCS). Distinguishes two units from the same company on the same modality.';
COMMENT ON COLUMN reference."neuromod_devices"."phase" IS 'Rollout gate: 1 = selectable now (tDCS, HD-tDCS), 2 = catalogued but not yet enabled in the UI. Switching a device on is an UPDATE, not a migration.';

CREATE TABLE IF NOT EXISTS reference."neuromod_conditions" (
    "condition_id"    UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_name"  TEXT NOT NULL,
    "display_order"   INTEGER NOT NULL DEFAULT 0,
    "is_active"       BOOLEAN NOT NULL DEFAULT true,
    "created_at"      TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"      TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE reference."neuromod_conditions" IS 'Top-level treatable conditions — Depression, Anxiety Disorders, Chronic Pain, ADHD, ASD. Retention: Bucket 3.';

CREATE TABLE IF NOT EXISTS reference."neuromod_diagnoses" (
    "diagnosis_id"       UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"       UUID NOT NULL,
    "icd10_code"         TEXT NOT NULL,
    "icd10_description"  TEXT NOT NULL,
    "created_at"         TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE reference."neuromod_diagnoses" IS 'ICD-10 lookup. A doctor picks a diagnosis to narrow the placement/dosing options; the protocol row stores only the RESOLVED placement and dosing, not the diagnosis — hence no FK from treatment_protocols to here. Retention: Bucket 3.';

CREATE TABLE IF NOT EXISTS reference."neuromod_scales" (
    "scale_id"     UUID NOT NULL DEFAULT gen_random_uuid(),
    "scale_code"   TEXT NOT NULL,
    "scale_name"   TEXT NOT NULL,
    "prs_scale_id" TEXT,
    "created_at"   TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE reference."neuromod_scales" IS 'Clinical rating scales recommended per condition (PHQ-9, GAD-7, NPRS, ...). Retention: Bucket 3.';
COMMENT ON COLUMN reference."neuromod_scales"."prs_scale_id" IS 'Optional bridge to reference.prs_scales when the same instrument is also administered through the PRS questionnaire engine. Nullable: not every recommended scale is built as a PRS scale. Left as TEXT rather than a FK because reference.prs_scales keys on a text scale_id.';

CREATE TABLE IF NOT EXISTS reference."neuromod_condition_scales" (
    "cs_map_id"      UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"   UUID NOT NULL,
    "scale_id"       UUID NOT NULL,
    "display_order"  INTEGER NOT NULL DEFAULT 0,
    "created_at"     TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE reference."neuromod_condition_scales" IS 'Many-to-many: which scales are recommended for which condition. HADS, for example, appears under both Depression and Anxiety. Retention: Bucket 3.';


-- ---------------------------------------------------------------------------
-- Per-device placement tables
-- ---------------------------------------------------------------------------
-- Separate table per device, by requirement. The clinical payoff: each device's
-- electrode rule becomes a plain CHECK on its own table instead of a trigger
-- that switches on device type. tDCS's "exactly 1 anode + 1 cathode" and
-- HD-tDCS's "1 anode + up to 4 returns" are structurally different shapes and
-- cannot share a column set without one of them being unconstrained.
--
-- Cost, stated plainly: cross-device reporting ("every placement this patient
-- has had") is a 6-way UNION, and the same-device rule between a protocol's
-- placement and its dosing needs the trigger in §7 because a CHECK cannot read
-- another table.

CREATE TABLE IF NOT EXISTS reference."tdcs_placements" (
    "tdcs_placement_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"       UUID NOT NULL,
    "device_id"          UUID NOT NULL,
    "montage_label"      TEXT NOT NULL,
    "anode_site"         TEXT,
    "cathode_site"       TEXT,
    "is_active"          BOOLEAN NOT NULL DEFAULT true,
    "created_at"         TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Electrode rule: tDCS = exactly 1 anode + 1 cathode. Both sites are either
    -- specified together or both left NULL ("Not specified" in the source sheet
    -- for Chronic Pain / ADHD / ASD — carried through as-is, not invented).
    CONSTRAINT "chk_tdcs_placements_electrode_rule" CHECK (
        ("anode_site" IS NOT NULL AND "cathode_site" IS NOT NULL)
        OR ("anode_site" IS NULL AND "cathode_site" IS NULL)
    )
);
COMMENT ON TABLE reference."tdcs_placements" IS 'Conventional tDCS montage: exactly 1 anode + 1 cathode, 10-20 EEG sites. Retention: Bucket 3.';

CREATE TABLE IF NOT EXISTS reference."hd_tdcs_placements" (
    "hd_tdcs_placement_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"          UUID NOT NULL,
    "device_id"             UUID NOT NULL,
    "montage_label"         TEXT NOT NULL,
    "anode_site"            TEXT,
    "return_sites"          TEXT[] NOT NULL DEFAULT '{}',
    "is_active"             BOOLEAN NOT NULL DEFAULT true,
    "created_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Electrode rule: HD-tDCS = 1 anode + up to 4 return cathodes (4x1 ring).
    CONSTRAINT "chk_hd_tdcs_placements_electrode_rule" CHECK (
        ("anode_site" IS NOT NULL AND COALESCE(array_length("return_sites", 1), 0) BETWEEN 1 AND 4)
        OR ("anode_site" IS NULL AND "return_sites" = '{}')
    )
);
COMMENT ON TABLE reference."hd_tdcs_placements" IS 'HD-tDCS 4x1 ring montage: 1 anode + up to 4 return cathodes, 10-20 EEG sites. Retention: Bucket 3.';

CREATE TABLE IF NOT EXISTS reference."tavns_placements" (
    "tavns_placement_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"        UUID NOT NULL,
    "device_id"           UUID NOT NULL,
    "montage_label"       TEXT NOT NULL,
    "ear_side"            TEXT,
    "auricular_site"      TEXT,
    "is_active"           BOOLEAN NOT NULL DEFAULT true,
    "created_at"          TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_tavns_placements_ear_side" CHECK ("ear_side" IS NULL OR "ear_side" IN
        ('left', 'right', 'bilateral'))
);
COMMENT ON TABLE reference."tavns_placements" IS 'taVNS auricular placement. Sites are ear landmarks (cymba conchae, tragus, earlobe), not 10-20 scalp positions. Retention: Bucket 3.';

CREATE TABLE IF NOT EXISTS reference."tps_placements" (
    "tps_placement_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"      UUID NOT NULL,
    "device_id"         UUID NOT NULL,
    "montage_label"     TEXT NOT NULL,
    "target_region"     TEXT,
    "hemisphere"        TEXT,
    "is_active"         BOOLEAN NOT NULL DEFAULT true,
    "created_at"        TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_tps_placements_hemisphere" CHECK ("hemisphere" IS NULL OR "hemisphere" IN
        ('left', 'right', 'bilateral', 'midline'))
);
COMMENT ON TABLE reference."tps_placements" IS 'TPS transducer target. No electrodes at all — target_region is an anatomical region, not an electrode site. Retention: Bucket 3.';

CREATE TABLE IF NOT EXISTS reference."rtms_placements" (
    "rtms_placement_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"       UUID NOT NULL,
    "device_id"          UUID NOT NULL,
    "montage_label"      TEXT NOT NULL,
    "coil_target"        TEXT,
    "coil_type"          TEXT,
    "hemisphere"         TEXT,
    "is_active"          BOOLEAN NOT NULL DEFAULT true,
    "created_at"         TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_rtms_placements_hemisphere" CHECK ("hemisphere" IS NULL OR "hemisphere" IN
        ('left', 'right', 'bilateral', 'midline'))
);
COMMENT ON TABLE reference."rtms_placements" IS 'rTMS coil position. coil_target may be a 10-20 site (F3) or an anatomical target; coil_type is figure-8, H-coil, etc. Retention: Bucket 3.';

CREATE TABLE IF NOT EXISTS reference."other_placements" (
    "other_placement_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"        UUID NOT NULL,
    "device_id"           UUID NOT NULL,
    "montage_label"       TEXT NOT NULL,
    "placement_details"   JSONB NOT NULL DEFAULT '{}'::jsonb,
    "is_active"           BOOLEAN NOT NULL DEFAULT true,
    "created_at"          TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"          TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE reference."other_placements" IS 'Escape hatch for a device not yet modelled. jsonb here is deliberate — an unmodelled device has no known column set to type. Retention: Bucket 3.';


-- ---------------------------------------------------------------------------
-- Per-device dosing tables
-- ---------------------------------------------------------------------------
-- Same split, same reason: the parameters genuinely differ. tDCS doses in mA
-- over minutes; rTMS doses in % of resting motor threshold over pulse trains;
-- TPS doses in millijoules per pulse. One shared table would be a dozen
-- always-NULL columns and no way to require the ones that matter.

CREATE TABLE IF NOT EXISTS reference."tdcs_dosing" (
    "tdcs_dosing_id"        UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"          UUID NOT NULL,
    "device_id"             UUID NOT NULL,
    "tdcs_placement_id"     UUID NOT NULL,
    "evidence_level"        TEXT NOT NULL,
    "current_ma_min"        NUMERIC(3,1),
    "current_ma_max"        NUMERIC(3,1),
    "session_duration_min"  INTEGER,
    "sessions_per_day"      INTEGER,
    "num_sessions_text"     TEXT,
    "notes"                 TEXT,
    "is_active"             BOOLEAN NOT NULL DEFAULT true,
    "created_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_tdcs_dosing_current_range" CHECK (
        "current_ma_min" IS NULL OR "current_ma_max" IS NULL
        OR "current_ma_min" <= "current_ma_max"
    ),
    CONSTRAINT "chk_tdcs_dosing_evidence_level" CHECK ("evidence_level" IN ('A', 'B', 'C'))
);
COMMENT ON TABLE reference."tdcs_dosing" IS 'Prescribable tDCS dose per condition+placement. Retention: Bucket 3.';
COMMENT ON COLUMN reference."tdcs_dosing"."num_sessions_text" IS 'Free text because the source sheet expresses this as a protocol phrase ("1 per day x 10 days (20-30 days attempted)"), not a single number. The actual prescribed count is per-patient and lives on treatment_protocols.session_count.';

CREATE TABLE IF NOT EXISTS reference."hd_tdcs_dosing" (
    "hd_tdcs_dosing_id"      UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"           UUID NOT NULL,
    "device_id"              UUID NOT NULL,
    "hd_tdcs_placement_id"   UUID NOT NULL,
    "evidence_level"         TEXT NOT NULL,
    "total_current_ma"       NUMERIC(3,1),
    "per_return_current_ma"  NUMERIC(3,1),
    "session_duration_min"   INTEGER,
    "sessions_per_day"       INTEGER,
    "num_sessions_text"      TEXT,
    "notes"                  TEXT,
    "is_active"              BOOLEAN NOT NULL DEFAULT true,
    "created_at"             TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_hd_tdcs_dosing_evidence_level" CHECK ("evidence_level" IN ('A', 'B', 'C'))
);
COMMENT ON TABLE reference."hd_tdcs_dosing" IS 'Prescribable HD-tDCS dose per condition+placement. Retention: Bucket 3.';
COMMENT ON COLUMN reference."hd_tdcs_dosing"."per_return_current_ma" IS 'Current at each return electrode. Distinct from total_current_ma because a 4x1 ring splits the anode current across its returns — a distinction conventional tDCS does not have.';

CREATE TABLE IF NOT EXISTS reference."tavns_dosing" (
    "tavns_dosing_id"       UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"          UUID NOT NULL,
    "device_id"             UUID NOT NULL,
    "tavns_placement_id"    UUID NOT NULL,
    "evidence_level"        TEXT NOT NULL,
    "intensity_ma"          NUMERIC(4,2),
    "pulse_width_us"        INTEGER,
    "frequency_hz"          NUMERIC(6,2),
    "duty_cycle_on_sec"     INTEGER,
    "duty_cycle_off_sec"    INTEGER,
    "session_duration_min"  INTEGER,
    "num_sessions_text"     TEXT,
    "notes"                 TEXT,
    "is_active"             BOOLEAN NOT NULL DEFAULT true,
    "created_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_tavns_dosing_evidence_level" CHECK ("evidence_level" IN ('A', 'B', 'C'))
);
COMMENT ON TABLE reference."tavns_dosing" IS 'Prescribable taVNS dose per condition+placement. Retention: Bucket 3.';

CREATE TABLE IF NOT EXISTS reference."tps_dosing" (
    "tps_dosing_id"        UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"         UUID NOT NULL,
    "device_id"            UUID NOT NULL,
    "tps_placement_id"     UUID NOT NULL,
    "evidence_level"       TEXT NOT NULL,
    "energy_mj"            NUMERIC(6,3),
    "pulses_per_session"   INTEGER,
    "pulse_rate_hz"        NUMERIC(6,2),
    "num_sessions_text"    TEXT,
    "notes"                TEXT,
    "is_active"            BOOLEAN NOT NULL DEFAULT true,
    "created_at"           TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_tps_dosing_evidence_level" CHECK ("evidence_level" IN ('A', 'B', 'C'))
);
COMMENT ON TABLE reference."tps_dosing" IS 'Prescribable TPS dose per condition+placement. Retention: Bucket 3.';

CREATE TABLE IF NOT EXISTS reference."rtms_dosing" (
    "rtms_dosing_id"           UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"             UUID NOT NULL,
    "device_id"                UUID NOT NULL,
    "rtms_placement_id"        UUID NOT NULL,
    "evidence_level"           TEXT NOT NULL,
    "frequency_hz"             NUMERIC(6,2),
    "pct_motor_threshold"      NUMERIC(5,2),
    "train_count"              INTEGER,
    "pulses_per_train"         INTEGER,
    "pulses_per_session"       INTEGER,
    "inter_train_interval_sec" NUMERIC(6,2),
    "num_sessions_text"        TEXT,
    "notes"                    TEXT,
    "is_active"                BOOLEAN NOT NULL DEFAULT true,
    "created_at"               TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_rtms_dosing_pct_motor_threshold" CHECK (
        "pct_motor_threshold" IS NULL
        OR ("pct_motor_threshold" > 0 AND "pct_motor_threshold" <= 200)
    ),
    CONSTRAINT "chk_rtms_dosing_evidence_level" CHECK ("evidence_level" IN ('A', 'B', 'C'))
);
COMMENT ON TABLE reference."rtms_dosing" IS 'Prescribable rTMS dose per condition+placement. Retention: Bucket 3.';
COMMENT ON COLUMN reference."rtms_dosing"."pct_motor_threshold" IS 'rTMS intensity is expressed as a percentage of the patient''s resting motor threshold, not an absolute current. Values above 100 are clinically normal; the CHECK bounds it at 200 as a typo guard, not a clinical limit. numeric(5,2) per v2 Layer 2 so 66.67% is expressible.';

CREATE TABLE IF NOT EXISTS reference."other_dosing" (
    "other_dosing_id"      UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"         UUID NOT NULL,
    "device_id"            UUID NOT NULL,
    "other_placement_id"   UUID NOT NULL,
    "evidence_level"       TEXT NOT NULL,
    "dose_details"         JSONB NOT NULL DEFAULT '{}'::jsonb,
    "num_sessions_text"    TEXT,
    "notes"                TEXT,
    "is_active"            BOOLEAN NOT NULL DEFAULT true,
    "created_at"           TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_other_dosing_evidence_level" CHECK ("evidence_level" IN ('A', 'B', 'C'))
);
COMMENT ON TABLE reference."other_dosing" IS 'Escape hatch dose for an unmodelled device. Retention: Bucket 3.';


-- ###########################################################################
-- §3  LAYER 1 — Tables: Treatment Protocol (core)
-- ###########################################################################

CREATE TABLE IF NOT EXISTS core."treatment_protocols" (
    "protocol_id"            UUID NOT NULL DEFAULT gen_random_uuid(),
    "plan_id"                UUID NOT NULL,
    "device_id"              UUID NOT NULL,
    "set_by"                 UUID NOT NULL,

    -- Exactly one placement FK and one dosing FK is non-null; enforced by the
    -- two CHECKs at the bottom of this table. Twelve nullable columns is the
    -- price of keeping every reference a REAL foreign key. The alternative — a
    -- polymorphic (placement_type, placement_id) pair — cannot be constrained
    -- at all, and 11_foreign_keys.sql's header shows this project already
    -- treats each polymorphic column as an exception it had to justify.
    "tdcs_placement_id"      UUID,
    "hd_tdcs_placement_id"   UUID,
    "tavns_placement_id"     UUID,
    "tps_placement_id"       UUID,
    "rtms_placement_id"      UUID,
    "other_placement_id"     UUID,

    "tdcs_dosing_id"         UUID,
    "hd_tdcs_dosing_id"      UUID,
    "tavns_dosing_id"        UUID,
    "tps_dosing_id"          UUID,
    "rtms_dosing_id"         UUID,
    "other_dosing_id"        UUID,

    -- The session plan. Setting these is what generates the appointments.
    "session_count"          INTEGER NOT NULL,
    "follow_up_every_n"      INTEGER,

    "status"                 TEXT NOT NULL DEFAULT 'draft',
    "device_settings"        JSONB NOT NULL DEFAULT '{}'::jsonb,
    "notes"                  TEXT,
    "activated_at"           TIMESTAMPTZ,
    "completed_at"           TIMESTAMPTZ,
    "created_at"             TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"             TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT "chk_treatment_protocols_session_count" CHECK ("session_count" > 0),
    -- v2 Layer 2: text + CHECK, not a native enum.
    CONSTRAINT "chk_treatment_protocols_status" CHECK ("status" IN
        ('draft', 'active', 'completed', 'cancelled', 'superseded')),
    CONSTRAINT "chk_treatment_protocols_follow_up_every_n" CHECK (
        "follow_up_every_n" IS NULL
        OR ("follow_up_every_n" > 0 AND "follow_up_every_n" <= "session_count")
    ),
    CONSTRAINT "chk_treatment_protocols_one_placement" CHECK (
        num_nonnulls("tdcs_placement_id", "hd_tdcs_placement_id", "tavns_placement_id",
                     "tps_placement_id", "rtms_placement_id", "other_placement_id") = 1
    ),
    CONSTRAINT "chk_treatment_protocols_one_dosing" CHECK (
        num_nonnulls("tdcs_dosing_id", "hd_tdcs_dosing_id", "tavns_dosing_id",
                     "tps_dosing_id", "rtms_dosing_id", "other_dosing_id") = 1
    )
);
COMMENT ON TABLE core."treatment_protocols" IS 'Treatment Protocol — device + montage + dosing + session plan, set once by a doctor to start a course of treatment. A child of core.treatment_plans, which remains the superset (history + protocol + signature). Retention: clinical, Bucket 2 — anonymise with the patient profile, never hard-delete.';
COMMENT ON COLUMN core."treatment_protocols"."follow_up_every_n" IS 'Insert a follow-up appointment after every Nth device session. NULL = no scheduled follow-ups. Example: session_count=20, follow_up_every_n=5 generates 20 device-session appointments plus 4 follow-ups (after sessions 5, 10, 15, 20).';
COMMENT ON COLUMN core."treatment_protocols"."device_settings" IS 'Per-patient overrides on top of the catalogue dose (e.g. reduced current for tolerability). The catalogue row is the prescribed protocol; this is the deviation from it.';


-- ---------------------------------------------------------------------------
-- PRS response tables — two, by requirement
-- ---------------------------------------------------------------------------
-- Kept as two tables rather than one with a discriminator, per the explicit
-- requirement: a PRS taken after a device session and a PRS taken at a
-- follow-up are reported on separately.
--
-- Both now key on appointments.appointment_id, because after 30 a device
-- session and a follow-up are both appointments rows. The separation is
-- preserved by which appointment_type each table's rows point at — enforced by
-- the triggers in §7, since a CHECK cannot read the referenced row.

CREATE TABLE IF NOT EXISTS core."device_session_prs_responses" (
    "ds_prs_id"       UUID NOT NULL DEFAULT gen_random_uuid(),
    "appointment_id"  UUID NOT NULL,
    "protocol_id"     UUID NOT NULL,
    "session_number"  INTEGER NOT NULL,
    "instance_id"     TEXT NOT NULL,
    "patient_id"      UUID NOT NULL,
    "recorded_at"     TIMESTAMPTZ NOT NULL DEFAULT now(),
    "created_at"      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_ds_prs_session_number" CHECK ("session_number" > 0)
);
COMMENT ON TABLE core."device_session_prs_responses" IS 'PRS taken after a device session. Retention: clinical (7yr), Bucket 2 — the assessment stays, patient identifiers are anonymised.';
COMMENT ON COLUMN core."device_session_prs_responses"."session_number" IS 'Ordinal within the protocol (1..session_count), denormalised from the generating appointment so an assessment is traceable to its exact session without joining back through the spine.';
COMMENT ON COLUMN core."device_session_prs_responses"."patient_id" IS 'References core.profiles(id), matching the convention confirmed in NOTES.md: patient_id-shaped FK columns resolve to profiles.id, not patients.patient_id.';

CREATE TABLE IF NOT EXISTS core."followup_prs_responses" (
    "fu_prs_id"             UUID NOT NULL DEFAULT gen_random_uuid(),
    "appointment_id"        UUID NOT NULL,
    "protocol_id"           UUID NOT NULL,
    "after_session_number"  INTEGER NOT NULL,
    "instance_id"           TEXT NOT NULL,
    "patient_id"            UUID NOT NULL,
    "recorded_at"           TIMESTAMPTZ NOT NULL DEFAULT now(),
    "created_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_fu_prs_after_session_number" CHECK ("after_session_number" > 0)
);
COMMENT ON TABLE core."followup_prs_responses" IS 'PRS taken during a follow-up consultation. Kept as a separate table from device_session_prs_responses by requirement. Retention: clinical (7yr), Bucket 2.';
COMMENT ON COLUMN core."followup_prs_responses"."after_session_number" IS 'Which device session triggered this follow-up (5, 10, 15, 20 for follow_up_every_n = 5).';


-- ###########################################################################
-- §4  LAYER 1 — Spine columns for protocol-born appointments
-- ###########################################################################
-- 30 gave appointments plan_id. A protocol row needs two more facts that
-- plan_id alone cannot answer: WHICH protocol (a plan may be re-protocolled
-- after a review), and WHICH ordinal in the course.
--
-- Both nullable: every appointment that is not protocol-born (initial
-- consultation, ad-hoc visit) leaves them NULL.

ALTER TABLE core."appointments" ADD COLUMN IF NOT EXISTS "protocol_id" UUID;
ALTER TABLE core."appointments" ADD COLUMN IF NOT EXISTS "session_number" INTEGER;

COMMENT ON COLUMN core."appointments"."protocol_id" IS 'Set on every protocol-born row (device_session and follow_up). NULL for an initial or ad-hoc appointment. Distinct from plan_id: a plan can carry more than one protocol over time.';
COMMENT ON COLUMN core."appointments"."session_number" IS 'For appointment_type = device_session: the ordinal 1..treatment_protocols.session_count. For follow_up: which device session triggered it. NULL otherwise.';


-- ###########################################################################
-- §5  LAYER 2 — Primary keys
-- ###########################################################################

ALTER TABLE reference."device_companies"          DROP CONSTRAINT IF EXISTS "device_companies_pkey" CASCADE;
ALTER TABLE reference."device_companies"          ADD CONSTRAINT "device_companies_pkey"          PRIMARY KEY ("company_id");
ALTER TABLE reference."neuromod_devices"          DROP CONSTRAINT IF EXISTS "neuromod_devices_pkey" CASCADE;
ALTER TABLE reference."neuromod_devices"          ADD CONSTRAINT "neuromod_devices_pkey"          PRIMARY KEY ("device_id");
ALTER TABLE reference."neuromod_conditions"       DROP CONSTRAINT IF EXISTS "neuromod_conditions_pkey" CASCADE;
ALTER TABLE reference."neuromod_conditions"       ADD CONSTRAINT "neuromod_conditions_pkey"       PRIMARY KEY ("condition_id");
ALTER TABLE reference."neuromod_diagnoses"        DROP CONSTRAINT IF EXISTS "neuromod_diagnoses_pkey" CASCADE;
ALTER TABLE reference."neuromod_diagnoses"        ADD CONSTRAINT "neuromod_diagnoses_pkey"        PRIMARY KEY ("diagnosis_id");
ALTER TABLE reference."neuromod_scales"           DROP CONSTRAINT IF EXISTS "neuromod_scales_pkey" CASCADE;
ALTER TABLE reference."neuromod_scales"           ADD CONSTRAINT "neuromod_scales_pkey"           PRIMARY KEY ("scale_id");
ALTER TABLE reference."neuromod_condition_scales" DROP CONSTRAINT IF EXISTS "neuromod_condition_scales_pkey" CASCADE;
ALTER TABLE reference."neuromod_condition_scales" ADD CONSTRAINT "neuromod_condition_scales_pkey" PRIMARY KEY ("cs_map_id");

ALTER TABLE reference."tdcs_placements"    DROP CONSTRAINT IF EXISTS "tdcs_placements_pkey" CASCADE;
ALTER TABLE reference."tdcs_placements"    ADD CONSTRAINT "tdcs_placements_pkey"    PRIMARY KEY ("tdcs_placement_id");
ALTER TABLE reference."hd_tdcs_placements" DROP CONSTRAINT IF EXISTS "hd_tdcs_placements_pkey" CASCADE;
ALTER TABLE reference."hd_tdcs_placements" ADD CONSTRAINT "hd_tdcs_placements_pkey" PRIMARY KEY ("hd_tdcs_placement_id");
ALTER TABLE reference."tavns_placements"   DROP CONSTRAINT IF EXISTS "tavns_placements_pkey" CASCADE;
ALTER TABLE reference."tavns_placements"   ADD CONSTRAINT "tavns_placements_pkey"   PRIMARY KEY ("tavns_placement_id");
ALTER TABLE reference."tps_placements"     DROP CONSTRAINT IF EXISTS "tps_placements_pkey" CASCADE;
ALTER TABLE reference."tps_placements"     ADD CONSTRAINT "tps_placements_pkey"     PRIMARY KEY ("tps_placement_id");
ALTER TABLE reference."rtms_placements"    DROP CONSTRAINT IF EXISTS "rtms_placements_pkey" CASCADE;
ALTER TABLE reference."rtms_placements"    ADD CONSTRAINT "rtms_placements_pkey"    PRIMARY KEY ("rtms_placement_id");
ALTER TABLE reference."other_placements"   DROP CONSTRAINT IF EXISTS "other_placements_pkey" CASCADE;
ALTER TABLE reference."other_placements"   ADD CONSTRAINT "other_placements_pkey"   PRIMARY KEY ("other_placement_id");

ALTER TABLE reference."tdcs_dosing"    DROP CONSTRAINT IF EXISTS "tdcs_dosing_pkey" CASCADE;
ALTER TABLE reference."tdcs_dosing"    ADD CONSTRAINT "tdcs_dosing_pkey"    PRIMARY KEY ("tdcs_dosing_id");
ALTER TABLE reference."hd_tdcs_dosing" DROP CONSTRAINT IF EXISTS "hd_tdcs_dosing_pkey" CASCADE;
ALTER TABLE reference."hd_tdcs_dosing" ADD CONSTRAINT "hd_tdcs_dosing_pkey" PRIMARY KEY ("hd_tdcs_dosing_id");
ALTER TABLE reference."tavns_dosing"   DROP CONSTRAINT IF EXISTS "tavns_dosing_pkey" CASCADE;
ALTER TABLE reference."tavns_dosing"   ADD CONSTRAINT "tavns_dosing_pkey"   PRIMARY KEY ("tavns_dosing_id");
ALTER TABLE reference."tps_dosing"     DROP CONSTRAINT IF EXISTS "tps_dosing_pkey" CASCADE;
ALTER TABLE reference."tps_dosing"     ADD CONSTRAINT "tps_dosing_pkey"     PRIMARY KEY ("tps_dosing_id");
ALTER TABLE reference."rtms_dosing"    DROP CONSTRAINT IF EXISTS "rtms_dosing_pkey" CASCADE;
ALTER TABLE reference."rtms_dosing"    ADD CONSTRAINT "rtms_dosing_pkey"    PRIMARY KEY ("rtms_dosing_id");
ALTER TABLE reference."other_dosing"   DROP CONSTRAINT IF EXISTS "other_dosing_pkey" CASCADE;
ALTER TABLE reference."other_dosing"   ADD CONSTRAINT "other_dosing_pkey"   PRIMARY KEY ("other_dosing_id");

ALTER TABLE core."treatment_protocols"          DROP CONSTRAINT IF EXISTS "treatment_protocols_pkey" CASCADE;
ALTER TABLE core."treatment_protocols"          ADD CONSTRAINT "treatment_protocols_pkey"          PRIMARY KEY ("protocol_id");
ALTER TABLE core."device_session_prs_responses" DROP CONSTRAINT IF EXISTS "device_session_prs_responses_pkey" CASCADE;
ALTER TABLE core."device_session_prs_responses" ADD CONSTRAINT "device_session_prs_responses_pkey" PRIMARY KEY ("ds_prs_id");
ALTER TABLE core."followup_prs_responses"       DROP CONSTRAINT IF EXISTS "followup_prs_responses_pkey" CASCADE;
ALTER TABLE core."followup_prs_responses"       ADD CONSTRAINT "followup_prs_responses_pkey"       PRIMARY KEY ("fu_prs_id");


-- ###########################################################################
-- §6  LAYER 2 — Unique constraints
-- ###########################################################################

ALTER TABLE reference."device_companies" DROP CONSTRAINT IF EXISTS "device_companies_company_code_key";
ALTER TABLE reference."device_companies" ADD CONSTRAINT "device_companies_company_code_key" UNIQUE ("company_code");

ALTER TABLE reference."device_companies" DROP CONSTRAINT IF EXISTS "device_companies_company_name_key";
ALTER TABLE reference."device_companies" ADD CONSTRAINT "device_companies_company_name_key" UNIQUE ("company_name");

ALTER TABLE reference."neuromod_devices" DROP CONSTRAINT IF EXISTS "neuromod_devices_device_code_key";
ALTER TABLE reference."neuromod_devices" ADD CONSTRAINT "neuromod_devices_device_code_key" UNIQUE ("device_code");

-- One (company, model) pair is one device row. Guards the seed script against
-- inserting the same physical unit twice under two device_codes.
-- Partial, because company_id is nullable during the transition: a UNIQUE
-- constraint would treat NULL company rows as always-distinct, which is right,
-- but stating it as a partial index makes the intent explicit.
DROP INDEX IF EXISTS reference.uq_neuromod_devices_company_model;
CREATE UNIQUE INDEX uq_neuromod_devices_company_model
    ON reference."neuromod_devices" ("company_id", "model_number")
    WHERE "company_id" IS NOT NULL AND "model_number" IS NOT NULL;

ALTER TABLE reference."neuromod_conditions" DROP CONSTRAINT IF EXISTS "neuromod_conditions_condition_name_key";
ALTER TABLE reference."neuromod_conditions" ADD CONSTRAINT "neuromod_conditions_condition_name_key" UNIQUE ("condition_name");

ALTER TABLE reference."neuromod_scales" DROP CONSTRAINT IF EXISTS "neuromod_scales_scale_code_key";
ALTER TABLE reference."neuromod_scales" ADD CONSTRAINT "neuromod_scales_scale_code_key" UNIQUE ("scale_code");

-- One ICD-10 code may appear under exactly one condition.
ALTER TABLE reference."neuromod_diagnoses" DROP CONSTRAINT IF EXISTS "uq_neuromod_diagnoses_condition_code";
ALTER TABLE reference."neuromod_diagnoses" ADD CONSTRAINT "uq_neuromod_diagnoses_condition_code" UNIQUE ("condition_id", "icd10_code");

ALTER TABLE reference."neuromod_condition_scales" DROP CONSTRAINT IF EXISTS "uq_neuromod_condition_scales";
ALTER TABLE reference."neuromod_condition_scales" ADD CONSTRAINT "uq_neuromod_condition_scales" UNIQUE ("condition_id", "scale_id");

-- One montage label per condition+device: two rows both called "F3 anode /
-- F4 cathode" for Depression on tDCS is a data-entry duplicate, not a variant.
ALTER TABLE reference."tdcs_placements"    DROP CONSTRAINT IF EXISTS "uq_tdcs_placements_label";
ALTER TABLE reference."tdcs_placements"    ADD CONSTRAINT "uq_tdcs_placements_label"    UNIQUE ("condition_id", "device_id", "montage_label");
ALTER TABLE reference."hd_tdcs_placements" DROP CONSTRAINT IF EXISTS "uq_hd_tdcs_placements_label";
ALTER TABLE reference."hd_tdcs_placements" ADD CONSTRAINT "uq_hd_tdcs_placements_label" UNIQUE ("condition_id", "device_id", "montage_label");
ALTER TABLE reference."tavns_placements"   DROP CONSTRAINT IF EXISTS "uq_tavns_placements_label";
ALTER TABLE reference."tavns_placements"   ADD CONSTRAINT "uq_tavns_placements_label"   UNIQUE ("condition_id", "device_id", "montage_label");
ALTER TABLE reference."tps_placements"     DROP CONSTRAINT IF EXISTS "uq_tps_placements_label";
ALTER TABLE reference."tps_placements"     ADD CONSTRAINT "uq_tps_placements_label"     UNIQUE ("condition_id", "device_id", "montage_label");
ALTER TABLE reference."rtms_placements"    DROP CONSTRAINT IF EXISTS "uq_rtms_placements_label";
ALTER TABLE reference."rtms_placements"    ADD CONSTRAINT "uq_rtms_placements_label"    UNIQUE ("condition_id", "device_id", "montage_label");
ALTER TABLE reference."other_placements"   DROP CONSTRAINT IF EXISTS "uq_other_placements_label";
ALTER TABLE reference."other_placements"   ADD CONSTRAINT "uq_other_placements_label"   UNIQUE ("condition_id", "device_id", "montage_label");

-- A protocol-born appointment is unique on (protocol, type, ordinal): session 7
-- of a protocol exists once, and the follow-up after session 10 exists once.
-- A unique INDEX rather than a constraint because it is partial — rows that are
-- not protocol-born have protocol_id NULL and must not collide with each other.
DROP INDEX IF EXISTS core.uq_appointments_protocol_session;
CREATE UNIQUE INDEX uq_appointments_protocol_session
    ON core."appointments" ("protocol_id", "appointment_type", "session_number")
    WHERE "protocol_id" IS NOT NULL;

-- One PRS per device session, and one per follow-up. If a patient can retake a
-- PRS within a single visit, these two become non-unique — see OPEN ITEM 2.
ALTER TABLE core."device_session_prs_responses" DROP CONSTRAINT IF EXISTS "uq_device_session_prs_responses_appointment_id";
ALTER TABLE core."device_session_prs_responses" ADD CONSTRAINT "uq_device_session_prs_responses_appointment_id" UNIQUE ("appointment_id");
ALTER TABLE core."followup_prs_responses"       DROP CONSTRAINT IF EXISTS "uq_followup_prs_responses_appointment_id";
ALTER TABLE core."followup_prs_responses"       ADD CONSTRAINT "uq_followup_prs_responses_appointment_id" UNIQUE ("appointment_id");


-- ###########################################################################
-- §7  LAYER 2 — Foreign keys
-- ###########################################################################
-- Every FK is ON DELETE RESTRICT. No CASCADE anywhere on clinical data: an
-- accidental catalogue delete must not silently erase a prescribed protocol.
--
-- On NOT VALID (v2 Layer 6, "Every ADD CONSTRAINT lands NOT VALID, validated in
-- a later script"): the reference tables above are created empty in this same
-- transaction, so their constraints have nothing to scan and NOT VALID would
-- only leave them permanently unvalidated. The two constraints that DO touch a
-- pre-existing table — the appointments FKs in this section — are the ones
-- where the rule bites, and they are written NOT VALID + VALIDATE below.

ALTER TABLE reference."neuromod_devices" DROP CONSTRAINT IF EXISTS "fk_neuromod_devices_company_id";
ALTER TABLE reference."neuromod_devices"
    ADD CONSTRAINT "fk_neuromod_devices_company_id"
    FOREIGN KEY ("company_id") REFERENCES reference."device_companies" ("company_id") ON DELETE RESTRICT;

ALTER TABLE reference."neuromod_diagnoses" DROP CONSTRAINT IF EXISTS "fk_neuromod_diagnoses_condition_id";
ALTER TABLE reference."neuromod_diagnoses"
    ADD CONSTRAINT "fk_neuromod_diagnoses_condition_id"
    FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;

ALTER TABLE reference."neuromod_condition_scales" DROP CONSTRAINT IF EXISTS "fk_neuromod_condition_scales_condition_id";
ALTER TABLE reference."neuromod_condition_scales"
    ADD CONSTRAINT "fk_neuromod_condition_scales_condition_id"
    FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;
ALTER TABLE reference."neuromod_condition_scales" DROP CONSTRAINT IF EXISTS "fk_neuromod_condition_scales_scale_id";
ALTER TABLE reference."neuromod_condition_scales"
    ADD CONSTRAINT "fk_neuromod_condition_scales_scale_id"
    FOREIGN KEY ("scale_id") REFERENCES reference."neuromod_scales" ("scale_id") ON DELETE RESTRICT;

-- Placements -> condition + device
ALTER TABLE reference."tdcs_placements"    DROP CONSTRAINT IF EXISTS "fk_tdcs_placements_condition_id";
ALTER TABLE reference."tdcs_placements"    ADD CONSTRAINT "fk_tdcs_placements_condition_id"    FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;
ALTER TABLE reference."tdcs_placements"    DROP CONSTRAINT IF EXISTS "fk_tdcs_placements_device_id";
ALTER TABLE reference."tdcs_placements"    ADD CONSTRAINT "fk_tdcs_placements_device_id"       FOREIGN KEY ("device_id")    REFERENCES reference."neuromod_devices" ("device_id")       ON DELETE RESTRICT;

ALTER TABLE reference."hd_tdcs_placements" DROP CONSTRAINT IF EXISTS "fk_hd_tdcs_placements_condition_id";
ALTER TABLE reference."hd_tdcs_placements" ADD CONSTRAINT "fk_hd_tdcs_placements_condition_id" FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;
ALTER TABLE reference."hd_tdcs_placements" DROP CONSTRAINT IF EXISTS "fk_hd_tdcs_placements_device_id";
ALTER TABLE reference."hd_tdcs_placements" ADD CONSTRAINT "fk_hd_tdcs_placements_device_id"    FOREIGN KEY ("device_id")    REFERENCES reference."neuromod_devices" ("device_id")       ON DELETE RESTRICT;

ALTER TABLE reference."tavns_placements"   DROP CONSTRAINT IF EXISTS "fk_tavns_placements_condition_id";
ALTER TABLE reference."tavns_placements"   ADD CONSTRAINT "fk_tavns_placements_condition_id"   FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;
ALTER TABLE reference."tavns_placements"   DROP CONSTRAINT IF EXISTS "fk_tavns_placements_device_id";
ALTER TABLE reference."tavns_placements"   ADD CONSTRAINT "fk_tavns_placements_device_id"      FOREIGN KEY ("device_id")    REFERENCES reference."neuromod_devices" ("device_id")       ON DELETE RESTRICT;

ALTER TABLE reference."tps_placements"     DROP CONSTRAINT IF EXISTS "fk_tps_placements_condition_id";
ALTER TABLE reference."tps_placements"     ADD CONSTRAINT "fk_tps_placements_condition_id"     FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;
ALTER TABLE reference."tps_placements"     DROP CONSTRAINT IF EXISTS "fk_tps_placements_device_id";
ALTER TABLE reference."tps_placements"     ADD CONSTRAINT "fk_tps_placements_device_id"        FOREIGN KEY ("device_id")    REFERENCES reference."neuromod_devices" ("device_id")       ON DELETE RESTRICT;

ALTER TABLE reference."rtms_placements"    DROP CONSTRAINT IF EXISTS "fk_rtms_placements_condition_id";
ALTER TABLE reference."rtms_placements"    ADD CONSTRAINT "fk_rtms_placements_condition_id"    FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;
ALTER TABLE reference."rtms_placements"    DROP CONSTRAINT IF EXISTS "fk_rtms_placements_device_id";
ALTER TABLE reference."rtms_placements"    ADD CONSTRAINT "fk_rtms_placements_device_id"       FOREIGN KEY ("device_id")    REFERENCES reference."neuromod_devices" ("device_id")       ON DELETE RESTRICT;

ALTER TABLE reference."other_placements"   DROP CONSTRAINT IF EXISTS "fk_other_placements_condition_id";
ALTER TABLE reference."other_placements"   ADD CONSTRAINT "fk_other_placements_condition_id"   FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;
ALTER TABLE reference."other_placements"   DROP CONSTRAINT IF EXISTS "fk_other_placements_device_id";
ALTER TABLE reference."other_placements"   ADD CONSTRAINT "fk_other_placements_device_id"      FOREIGN KEY ("device_id")    REFERENCES reference."neuromod_devices" ("device_id")       ON DELETE RESTRICT;

-- Dosing -> condition + device + its OWN device's placement. The placement FK
-- is what makes a dose inseparable from the montage it was measured on.
ALTER TABLE reference."tdcs_dosing"    DROP CONSTRAINT IF EXISTS "fk_tdcs_dosing_condition_id";
ALTER TABLE reference."tdcs_dosing"    ADD CONSTRAINT "fk_tdcs_dosing_condition_id"    FOREIGN KEY ("condition_id")       REFERENCES reference."neuromod_conditions" ("condition_id")        ON DELETE RESTRICT;
ALTER TABLE reference."tdcs_dosing"    DROP CONSTRAINT IF EXISTS "fk_tdcs_dosing_device_id";
ALTER TABLE reference."tdcs_dosing"    ADD CONSTRAINT "fk_tdcs_dosing_device_id"       FOREIGN KEY ("device_id")          REFERENCES reference."neuromod_devices" ("device_id")              ON DELETE RESTRICT;
ALTER TABLE reference."tdcs_dosing"    DROP CONSTRAINT IF EXISTS "fk_tdcs_dosing_tdcs_placement_id";
ALTER TABLE reference."tdcs_dosing"    ADD CONSTRAINT "fk_tdcs_dosing_tdcs_placement_id"    FOREIGN KEY ("tdcs_placement_id")  REFERENCES reference."tdcs_placements" ("tdcs_placement_id")       ON DELETE RESTRICT;

ALTER TABLE reference."hd_tdcs_dosing" DROP CONSTRAINT IF EXISTS "fk_hd_tdcs_dosing_condition_id";
ALTER TABLE reference."hd_tdcs_dosing" ADD CONSTRAINT "fk_hd_tdcs_dosing_condition_id" FOREIGN KEY ("condition_id")          REFERENCES reference."neuromod_conditions" ("condition_id")     ON DELETE RESTRICT;
ALTER TABLE reference."hd_tdcs_dosing" DROP CONSTRAINT IF EXISTS "fk_hd_tdcs_dosing_device_id";
ALTER TABLE reference."hd_tdcs_dosing" ADD CONSTRAINT "fk_hd_tdcs_dosing_device_id"    FOREIGN KEY ("device_id")             REFERENCES reference."neuromod_devices" ("device_id")           ON DELETE RESTRICT;
ALTER TABLE reference."hd_tdcs_dosing" DROP CONSTRAINT IF EXISTS "fk_hd_tdcs_dosing_hd_tdcs_placement_id";
ALTER TABLE reference."hd_tdcs_dosing" ADD CONSTRAINT "fk_hd_tdcs_dosing_hd_tdcs_placement_id" FOREIGN KEY ("hd_tdcs_placement_id")  REFERENCES reference."hd_tdcs_placements" ("hd_tdcs_placement_id") ON DELETE RESTRICT;

ALTER TABLE reference."tavns_dosing"   DROP CONSTRAINT IF EXISTS "fk_tavns_dosing_condition_id";
ALTER TABLE reference."tavns_dosing"   ADD CONSTRAINT "fk_tavns_dosing_condition_id"   FOREIGN KEY ("condition_id")        REFERENCES reference."neuromod_conditions" ("condition_id")       ON DELETE RESTRICT;
ALTER TABLE reference."tavns_dosing"   DROP CONSTRAINT IF EXISTS "fk_tavns_dosing_device_id";
ALTER TABLE reference."tavns_dosing"   ADD CONSTRAINT "fk_tavns_dosing_device_id"      FOREIGN KEY ("device_id")           REFERENCES reference."neuromod_devices" ("device_id")             ON DELETE RESTRICT;
ALTER TABLE reference."tavns_dosing"   DROP CONSTRAINT IF EXISTS "fk_tavns_dosing_tavns_placement_id";
ALTER TABLE reference."tavns_dosing"   ADD CONSTRAINT "fk_tavns_dosing_tavns_placement_id"   FOREIGN KEY ("tavns_placement_id")  REFERENCES reference."tavns_placements" ("tavns_placement_id")    ON DELETE RESTRICT;

ALTER TABLE reference."tps_dosing"     DROP CONSTRAINT IF EXISTS "fk_tps_dosing_condition_id";
ALTER TABLE reference."tps_dosing"     ADD CONSTRAINT "fk_tps_dosing_condition_id"     FOREIGN KEY ("condition_id")      REFERENCES reference."neuromod_conditions" ("condition_id")         ON DELETE RESTRICT;
ALTER TABLE reference."tps_dosing"     DROP CONSTRAINT IF EXISTS "fk_tps_dosing_device_id";
ALTER TABLE reference."tps_dosing"     ADD CONSTRAINT "fk_tps_dosing_device_id"        FOREIGN KEY ("device_id")         REFERENCES reference."neuromod_devices" ("device_id")               ON DELETE RESTRICT;
ALTER TABLE reference."tps_dosing"     DROP CONSTRAINT IF EXISTS "fk_tps_dosing_tps_placement_id";
ALTER TABLE reference."tps_dosing"     ADD CONSTRAINT "fk_tps_dosing_tps_placement_id"     FOREIGN KEY ("tps_placement_id")  REFERENCES reference."tps_placements" ("tps_placement_id")          ON DELETE RESTRICT;

ALTER TABLE reference."rtms_dosing"    DROP CONSTRAINT IF EXISTS "fk_rtms_dosing_condition_id";
ALTER TABLE reference."rtms_dosing"    ADD CONSTRAINT "fk_rtms_dosing_condition_id"    FOREIGN KEY ("condition_id")       REFERENCES reference."neuromod_conditions" ("condition_id")        ON DELETE RESTRICT;
ALTER TABLE reference."rtms_dosing"    DROP CONSTRAINT IF EXISTS "fk_rtms_dosing_device_id";
ALTER TABLE reference."rtms_dosing"    ADD CONSTRAINT "fk_rtms_dosing_device_id"       FOREIGN KEY ("device_id")          REFERENCES reference."neuromod_devices" ("device_id")              ON DELETE RESTRICT;
ALTER TABLE reference."rtms_dosing"    DROP CONSTRAINT IF EXISTS "fk_rtms_dosing_rtms_placement_id";
ALTER TABLE reference."rtms_dosing"    ADD CONSTRAINT "fk_rtms_dosing_rtms_placement_id"    FOREIGN KEY ("rtms_placement_id")  REFERENCES reference."rtms_placements" ("rtms_placement_id")       ON DELETE RESTRICT;

ALTER TABLE reference."other_dosing"   DROP CONSTRAINT IF EXISTS "fk_other_dosing_condition_id";
ALTER TABLE reference."other_dosing"   ADD CONSTRAINT "fk_other_dosing_condition_id"   FOREIGN KEY ("condition_id")        REFERENCES reference."neuromod_conditions" ("condition_id")       ON DELETE RESTRICT;
ALTER TABLE reference."other_dosing"   DROP CONSTRAINT IF EXISTS "fk_other_dosing_device_id";
ALTER TABLE reference."other_dosing"   ADD CONSTRAINT "fk_other_dosing_device_id"      FOREIGN KEY ("device_id")           REFERENCES reference."neuromod_devices" ("device_id")             ON DELETE RESTRICT;
ALTER TABLE reference."other_dosing"   DROP CONSTRAINT IF EXISTS "fk_other_dosing_other_placement_id";
ALTER TABLE reference."other_dosing"   ADD CONSTRAINT "fk_other_dosing_other_placement_id"   FOREIGN KEY ("other_placement_id")  REFERENCES reference."other_placements" ("other_placement_id")    ON DELETE RESTRICT;

-- treatment_protocols -> plan, device, author
ALTER TABLE core."treatment_protocols" DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_plan_id";
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_plan_id"   FOREIGN KEY ("plan_id")   REFERENCES core."treatment_plans" ("plan_id")         ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_device_id";
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_device_id" FOREIGN KEY ("device_id") REFERENCES reference."neuromod_devices" ("device_id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_set_by";
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_set_by"    FOREIGN KEY ("set_by")    REFERENCES core."profiles" ("id")                      ON DELETE RESTRICT;

-- treatment_protocols -> the six placement tables
ALTER TABLE core."treatment_protocols" DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_tdcs_placement_id";
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tdcs_placement_id"    FOREIGN KEY ("tdcs_placement_id")    REFERENCES reference."tdcs_placements" ("tdcs_placement_id")       ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_hd_tdcs_placement_id";
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_hd_tdcs_placement_id" FOREIGN KEY ("hd_tdcs_placement_id") REFERENCES reference."hd_tdcs_placements" ("hd_tdcs_placement_id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_tavns_placement_id";
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tavns_placement_id"   FOREIGN KEY ("tavns_placement_id")   REFERENCES reference."tavns_placements" ("tavns_placement_id")     ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_tps_placement_id";
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tps_placement_id"     FOREIGN KEY ("tps_placement_id")     REFERENCES reference."tps_placements" ("tps_placement_id")         ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_rtms_placement_id";
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_rtms_placement_id"    FOREIGN KEY ("rtms_placement_id")    REFERENCES reference."rtms_placements" ("rtms_placement_id")       ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_other_placement_id";
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_other_placement_id"   FOREIGN KEY ("other_placement_id")   REFERENCES reference."other_placements" ("other_placement_id")     ON DELETE RESTRICT;

-- treatment_protocols -> the six dosing tables
ALTER TABLE core."treatment_protocols" DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_tdcs_dosing_id";
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tdcs_dosing_id"    FOREIGN KEY ("tdcs_dosing_id")    REFERENCES reference."tdcs_dosing" ("tdcs_dosing_id")       ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_hd_tdcs_dosing_id";
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_hd_tdcs_dosing_id" FOREIGN KEY ("hd_tdcs_dosing_id") REFERENCES reference."hd_tdcs_dosing" ("hd_tdcs_dosing_id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_tavns_dosing_id";
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tavns_dosing_id"   FOREIGN KEY ("tavns_dosing_id")   REFERENCES reference."tavns_dosing" ("tavns_dosing_id")     ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_tps_dosing_id";
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tps_dosing_id"     FOREIGN KEY ("tps_dosing_id")     REFERENCES reference."tps_dosing" ("tps_dosing_id")         ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_rtms_dosing_id";
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_rtms_dosing_id"    FOREIGN KEY ("rtms_dosing_id")    REFERENCES reference."rtms_dosing" ("rtms_dosing_id")       ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_other_dosing_id";
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_other_dosing_id"   FOREIGN KEY ("other_dosing_id")   REFERENCES reference."other_dosing" ("other_dosing_id")     ON DELETE RESTRICT;

-- PRS responses -> the spine, the protocol, the patient
ALTER TABLE core."device_session_prs_responses" DROP CONSTRAINT IF EXISTS "fk_device_session_prs_responses_appointment_id";
ALTER TABLE core."device_session_prs_responses" ADD CONSTRAINT "fk_device_session_prs_responses_appointment_id" FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id")     ON DELETE RESTRICT;
ALTER TABLE core."device_session_prs_responses" DROP CONSTRAINT IF EXISTS "fk_device_session_prs_responses_protocol_id";
ALTER TABLE core."device_session_prs_responses" ADD CONSTRAINT "fk_device_session_prs_responses_protocol_id"    FOREIGN KEY ("protocol_id")    REFERENCES core."treatment_protocols" ("protocol_id") ON DELETE RESTRICT;
ALTER TABLE core."device_session_prs_responses" DROP CONSTRAINT IF EXISTS "fk_device_session_prs_responses_patient_id";
ALTER TABLE core."device_session_prs_responses" ADD CONSTRAINT "fk_device_session_prs_responses_patient_id"     FOREIGN KEY ("patient_id")     REFERENCES core."profiles" ("id")                     ON DELETE RESTRICT;

ALTER TABLE core."followup_prs_responses" DROP CONSTRAINT IF EXISTS "fk_followup_prs_responses_appointment_id";
ALTER TABLE core."followup_prs_responses" ADD CONSTRAINT "fk_followup_prs_responses_appointment_id" FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id")     ON DELETE RESTRICT;
ALTER TABLE core."followup_prs_responses" DROP CONSTRAINT IF EXISTS "fk_followup_prs_responses_protocol_id";
ALTER TABLE core."followup_prs_responses" ADD CONSTRAINT "fk_followup_prs_responses_protocol_id"    FOREIGN KEY ("protocol_id")    REFERENCES core."treatment_protocols" ("protocol_id") ON DELETE RESTRICT;
ALTER TABLE core."followup_prs_responses" DROP CONSTRAINT IF EXISTS "fk_followup_prs_responses_patient_id";
ALTER TABLE core."followup_prs_responses" ADD CONSTRAINT "fk_followup_prs_responses_patient_id"     FOREIGN KEY ("patient_id")     REFERENCES core."profiles" ("id")                     ON DELETE RESTRICT;

-- prs_assessment_instances.instance_id is TEXT and is that table's primary key
-- (09_primary_keys.sql:41). The types already agreed; only the constraint was
-- missing, so a PRS response could name an assessment that does not exist.
ALTER TABLE core."device_session_prs_responses" DROP CONSTRAINT IF EXISTS "fk_device_session_prs_responses_instance_id";
ALTER TABLE core."device_session_prs_responses"
    ADD CONSTRAINT "fk_device_session_prs_responses_instance_id"
    FOREIGN KEY ("instance_id") REFERENCES core."prs_assessment_instances" ("instance_id") ON DELETE RESTRICT;

ALTER TABLE core."followup_prs_responses" DROP CONSTRAINT IF EXISTS "fk_followup_prs_responses_instance_id";
ALTER TABLE core."followup_prs_responses"
    ADD CONSTRAINT "fk_followup_prs_responses_instance_id"
    FOREIGN KEY ("instance_id") REFERENCES core."prs_assessment_instances" ("instance_id") ON DELETE RESTRICT;

-- appointments.protocol_id -> treatment_protocols. This one lands on a
-- pre-existing, application-visible table, so it follows v2 Layer 6 properly:
-- NOT VALID takes only a brief lock and skips the table scan, then VALIDATE
-- runs under a weaker lock that does not block reads or writes.
ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "fk_appointments_protocol_id";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "fk_appointments_protocol_id"
    FOREIGN KEY ("protocol_id") REFERENCES core."treatment_protocols" ("protocol_id") ON DELETE RESTRICT
    NOT VALID;

-- A protocol-born row carries both protocol_id and session_number, or neither.
ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "chk_appointments_protocol_pair";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_protocol_pair"
    CHECK (("protocol_id" IS NULL) = ("session_number" IS NULL))
    NOT VALID;

-- Reinforces 31's sweeper contract: a protocol-born row must carry plan_id, or
-- an expired hold deletes the doctor's prescribed date instead of releasing the
-- slot. 30's chk_appointments_device_session_has_plan covers device sessions
-- via appointment_type; this covers protocol-born follow-ups too.
ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "chk_appointments_protocol_has_plan";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_protocol_has_plan"
    CHECK ("protocol_id" IS NULL OR "plan_id" IS NOT NULL)
    NOT VALID;

-- Validated immediately: appointments held 0 rows as of 2026-08-11 (30/31
-- header notes), so each scan is instant. Kept as separate statements so that
-- if the table HAS grown by apply time, these three lines can be moved to a
-- later low-traffic window without touching anything above.
ALTER TABLE core."appointments" VALIDATE CONSTRAINT "fk_appointments_protocol_id";
ALTER TABLE core."appointments" VALIDATE CONSTRAINT "chk_appointments_protocol_pair";
ALTER TABLE core."appointments" VALIDATE CONSTRAINT "chk_appointments_protocol_has_plan";


-- ###########################################################################
-- §8  LAYER 2 — Indexes
-- ###########################################################################

-- "which devices does this vendor supply" — the device-picker grouping.
CREATE INDEX IF NOT EXISTS idx_neuromod_devices_company       ON reference."neuromod_devices" USING btree ("company_id");
-- The picker itself: selectable devices for a modality, phase-1 only.
CREATE INDEX IF NOT EXISTS idx_neuromod_devices_modality      ON reference."neuromod_devices" USING btree ("modality", "phase") WHERE "is_active";

CREATE INDEX IF NOT EXISTS idx_neuromod_diagnoses_condition   ON reference."neuromod_diagnoses" USING btree ("condition_id");
CREATE INDEX IF NOT EXISTS idx_neuromod_diagnoses_icd10       ON reference."neuromod_diagnoses" USING btree ("icd10_code");
CREATE INDEX IF NOT EXISTS idx_neuromod_cond_scales_condition ON reference."neuromod_condition_scales" USING btree ("condition_id");

-- The catalogue lookup the protocol UI runs on every device+condition pick.
CREATE INDEX IF NOT EXISTS idx_tdcs_placements_cond_dev    ON reference."tdcs_placements"    USING btree ("condition_id", "device_id") WHERE "is_active";
CREATE INDEX IF NOT EXISTS idx_hd_tdcs_placements_cond_dev ON reference."hd_tdcs_placements" USING btree ("condition_id", "device_id") WHERE "is_active";
CREATE INDEX IF NOT EXISTS idx_tavns_placements_cond_dev   ON reference."tavns_placements"   USING btree ("condition_id", "device_id") WHERE "is_active";
CREATE INDEX IF NOT EXISTS idx_tps_placements_cond_dev     ON reference."tps_placements"     USING btree ("condition_id", "device_id") WHERE "is_active";
CREATE INDEX IF NOT EXISTS idx_rtms_placements_cond_dev    ON reference."rtms_placements"    USING btree ("condition_id", "device_id") WHERE "is_active";
CREATE INDEX IF NOT EXISTS idx_other_placements_cond_dev   ON reference."other_placements"   USING btree ("condition_id", "device_id") WHERE "is_active";

CREATE INDEX IF NOT EXISTS idx_tdcs_dosing_placement    ON reference."tdcs_dosing"    USING btree ("tdcs_placement_id");
CREATE INDEX IF NOT EXISTS idx_hd_tdcs_dosing_placement ON reference."hd_tdcs_dosing" USING btree ("hd_tdcs_placement_id");
CREATE INDEX IF NOT EXISTS idx_tavns_dosing_placement   ON reference."tavns_dosing"   USING btree ("tavns_placement_id");
CREATE INDEX IF NOT EXISTS idx_tps_dosing_placement     ON reference."tps_dosing"     USING btree ("tps_placement_id");
CREATE INDEX IF NOT EXISTS idx_rtms_dosing_placement    ON reference."rtms_dosing"    USING btree ("rtms_placement_id");
CREATE INDEX IF NOT EXISTS idx_other_dosing_placement   ON reference."other_dosing"   USING btree ("other_placement_id");

CREATE INDEX IF NOT EXISTS idx_treatment_protocols_plan_id ON core."treatment_protocols" USING btree ("plan_id");
CREATE INDEX IF NOT EXISTS idx_treatment_protocols_status  ON core."treatment_protocols" USING btree ("status");
-- "the active protocol for this plan" — the single hottest protocol query.
CREATE INDEX IF NOT EXISTS idx_treatment_protocols_plan_active
    ON core."treatment_protocols" USING btree ("plan_id") WHERE "status" = 'active';

CREATE INDEX IF NOT EXISTS idx_appointments_protocol_id ON core."appointments" USING btree ("protocol_id");

CREATE INDEX IF NOT EXISTS idx_ds_prs_protocol_id ON core."device_session_prs_responses" USING btree ("protocol_id");
CREATE INDEX IF NOT EXISTS idx_ds_prs_patient_id  ON core."device_session_prs_responses" USING btree ("patient_id");
CREATE INDEX IF NOT EXISTS idx_fu_prs_protocol_id ON core."followup_prs_responses" USING btree ("protocol_id");
CREATE INDEX IF NOT EXISTS idx_fu_prs_patient_id  ON core."followup_prs_responses" USING btree ("patient_id");
CREATE INDEX IF NOT EXISTS idx_ds_prs_instance_id ON core."device_session_prs_responses" USING btree ("instance_id");
CREATE INDEX IF NOT EXISTS idx_fu_prs_instance_id ON core."followup_prs_responses" USING btree ("instance_id");


-- ###########################################################################
-- §9  LAYER 2 — Functions and triggers
-- ###########################################################################

-- ---------------------------------------------------------------------------
-- 9.1 Same-device consistency
-- ---------------------------------------------------------------------------
-- The protocol's device_id, its placement's device_id and its dosing's
-- device_id must agree. This is a TRIGGER and not a CHECK because a CHECK
-- cannot read another table — the acknowledged cost of the per-device split.

CREATE OR REPLACE FUNCTION core.fn_check_protocol_device_consistency()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_placement_device UUID;
    v_dosing_device    UUID;
BEGIN
    -- Exactly one of the six is non-null (chk_treatment_protocols_one_placement),
    -- so COALESCE over all six scalar subqueries yields that one's device.
    SELECT COALESCE(
        (SELECT device_id FROM reference.tdcs_placements    WHERE tdcs_placement_id    = NEW.tdcs_placement_id),
        (SELECT device_id FROM reference.hd_tdcs_placements WHERE hd_tdcs_placement_id = NEW.hd_tdcs_placement_id),
        (SELECT device_id FROM reference.tavns_placements   WHERE tavns_placement_id   = NEW.tavns_placement_id),
        (SELECT device_id FROM reference.tps_placements     WHERE tps_placement_id     = NEW.tps_placement_id),
        (SELECT device_id FROM reference.rtms_placements    WHERE rtms_placement_id    = NEW.rtms_placement_id),
        (SELECT device_id FROM reference.other_placements   WHERE other_placement_id   = NEW.other_placement_id)
    ) INTO v_placement_device;

    SELECT COALESCE(
        (SELECT device_id FROM reference.tdcs_dosing    WHERE tdcs_dosing_id    = NEW.tdcs_dosing_id),
        (SELECT device_id FROM reference.hd_tdcs_dosing WHERE hd_tdcs_dosing_id = NEW.hd_tdcs_dosing_id),
        (SELECT device_id FROM reference.tavns_dosing   WHERE tavns_dosing_id   = NEW.tavns_dosing_id),
        (SELECT device_id FROM reference.tps_dosing     WHERE tps_dosing_id     = NEW.tps_dosing_id),
        (SELECT device_id FROM reference.rtms_dosing    WHERE rtms_dosing_id    = NEW.rtms_dosing_id),
        (SELECT device_id FROM reference.other_dosing   WHERE other_dosing_id   = NEW.other_dosing_id)
    ) INTO v_dosing_device;

    IF v_placement_device IS DISTINCT FROM NEW.device_id THEN
        RAISE EXCEPTION 'Protocol device_id % does not match the placement''s device %',
            NEW.device_id, v_placement_device;
    END IF;
    IF v_dosing_device IS DISTINCT FROM NEW.device_id THEN
        RAISE EXCEPTION 'Protocol device_id % does not match the dosing''s device %',
            NEW.device_id, v_dosing_device;
    END IF;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_check_protocol_device_consistency ON core."treatment_protocols";
CREATE TRIGGER trg_check_protocol_device_consistency
    BEFORE INSERT OR UPDATE ON core."treatment_protocols"
    FOR EACH ROW EXECUTE FUNCTION core.fn_check_protocol_device_consistency();



-- ---------------------------------------------------------------------------
-- 9.2 Placement and dosing rows match their table's modality
-- ---------------------------------------------------------------------------
-- The per-device split means the TABLE carries the modality: a row in
-- tdcs_placements is a tDCS montage by virtue of where it lives. Nothing
-- enforced that its device_id actually points at a tDCS device, so an rTMS
-- device's montage could be inserted into the tDCS table with every constraint
-- satisfied. A CHECK cannot read reference.neuromod_devices; same trade as 9.1.

CREATE OR REPLACE FUNCTION reference.fn_check_device_modality()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_modality TEXT;
    v_want     TEXT := TG_ARGV[0];
BEGIN
    SELECT modality INTO v_modality
    FROM reference.neuromod_devices WHERE device_id = NEW.device_id;

    IF v_modality IS DISTINCT FROM v_want THEN
        RAISE EXCEPTION '% expects a % device, but device % is %',
            TG_TABLE_NAME, v_want, NEW.device_id, COALESCE(v_modality, '<missing>');
    END IF;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_check_modality_tdcs_placements ON reference."tdcs_placements";
CREATE TRIGGER trg_check_modality_tdcs_placements
    BEFORE INSERT OR UPDATE ON reference."tdcs_placements"
    FOR EACH ROW EXECUTE FUNCTION reference.fn_check_device_modality('tDCS');
DROP TRIGGER IF EXISTS trg_check_modality_hd_tdcs_placements ON reference."hd_tdcs_placements";
CREATE TRIGGER trg_check_modality_hd_tdcs_placements
    BEFORE INSERT OR UPDATE ON reference."hd_tdcs_placements"
    FOR EACH ROW EXECUTE FUNCTION reference.fn_check_device_modality('HD-tDCS');
DROP TRIGGER IF EXISTS trg_check_modality_tavns_placements ON reference."tavns_placements";
CREATE TRIGGER trg_check_modality_tavns_placements
    BEFORE INSERT OR UPDATE ON reference."tavns_placements"
    FOR EACH ROW EXECUTE FUNCTION reference.fn_check_device_modality('taVNS');
DROP TRIGGER IF EXISTS trg_check_modality_tps_placements ON reference."tps_placements";
CREATE TRIGGER trg_check_modality_tps_placements
    BEFORE INSERT OR UPDATE ON reference."tps_placements"
    FOR EACH ROW EXECUTE FUNCTION reference.fn_check_device_modality('TPS');
DROP TRIGGER IF EXISTS trg_check_modality_rtms_placements ON reference."rtms_placements";
CREATE TRIGGER trg_check_modality_rtms_placements
    BEFORE INSERT OR UPDATE ON reference."rtms_placements"
    FOR EACH ROW EXECUTE FUNCTION reference.fn_check_device_modality('rTMS');
DROP TRIGGER IF EXISTS trg_check_modality_other_placements ON reference."other_placements";
CREATE TRIGGER trg_check_modality_other_placements
    BEFORE INSERT OR UPDATE ON reference."other_placements"
    FOR EACH ROW EXECUTE FUNCTION reference.fn_check_device_modality('other');
DROP TRIGGER IF EXISTS trg_check_modality_tdcs_dosing ON reference."tdcs_dosing";
CREATE TRIGGER trg_check_modality_tdcs_dosing
    BEFORE INSERT OR UPDATE ON reference."tdcs_dosing"
    FOR EACH ROW EXECUTE FUNCTION reference.fn_check_device_modality('tDCS');
DROP TRIGGER IF EXISTS trg_check_modality_hd_tdcs_dosing ON reference."hd_tdcs_dosing";
CREATE TRIGGER trg_check_modality_hd_tdcs_dosing
    BEFORE INSERT OR UPDATE ON reference."hd_tdcs_dosing"
    FOR EACH ROW EXECUTE FUNCTION reference.fn_check_device_modality('HD-tDCS');
DROP TRIGGER IF EXISTS trg_check_modality_tavns_dosing ON reference."tavns_dosing";
CREATE TRIGGER trg_check_modality_tavns_dosing
    BEFORE INSERT OR UPDATE ON reference."tavns_dosing"
    FOR EACH ROW EXECUTE FUNCTION reference.fn_check_device_modality('taVNS');
DROP TRIGGER IF EXISTS trg_check_modality_tps_dosing ON reference."tps_dosing";
CREATE TRIGGER trg_check_modality_tps_dosing
    BEFORE INSERT OR UPDATE ON reference."tps_dosing"
    FOR EACH ROW EXECUTE FUNCTION reference.fn_check_device_modality('TPS');
DROP TRIGGER IF EXISTS trg_check_modality_rtms_dosing ON reference."rtms_dosing";
CREATE TRIGGER trg_check_modality_rtms_dosing
    BEFORE INSERT OR UPDATE ON reference."rtms_dosing"
    FOR EACH ROW EXECUTE FUNCTION reference.fn_check_device_modality('rTMS');
DROP TRIGGER IF EXISTS trg_check_modality_other_dosing ON reference."other_dosing";
CREATE TRIGGER trg_check_modality_other_dosing
    BEFORE INSERT OR UPDATE ON reference."other_dosing"
    FOR EACH ROW EXECUTE FUNCTION reference.fn_check_device_modality('other');

-- ---------------------------------------------------------------------------
-- 9.3 Each PRS table accepts only its own kind of visit
-- ---------------------------------------------------------------------------
-- With both tables keyed on appointment_id, nothing structural stops a
-- follow-up PRS being written to the device-session table. A CHECK cannot read
-- appointments.appointment_type, so this is a trigger.

CREATE OR REPLACE FUNCTION core.fn_check_prs_appointment_type()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_type TEXT;
    v_want TEXT := TG_ARGV[0];
BEGIN
    SELECT appointment_type INTO v_type
    FROM core.appointments WHERE appointment_id = NEW.appointment_id;

    IF v_type IS DISTINCT FROM v_want THEN
        RAISE EXCEPTION '% expects an appointment of type %, but appointment % is type %',
            TG_TABLE_NAME, v_want, NEW.appointment_id, COALESCE(v_type, '<missing>');
    END IF;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_check_device_session_prs_appointment_type ON core."device_session_prs_responses";
CREATE TRIGGER trg_check_device_session_prs_appointment_type
    BEFORE INSERT OR UPDATE ON core."device_session_prs_responses"
    FOR EACH ROW EXECUTE FUNCTION core.fn_check_prs_appointment_type('device_session');

DROP TRIGGER IF EXISTS trg_check_followup_prs_appointment_type ON core."followup_prs_responses";
CREATE TRIGGER trg_check_followup_prs_appointment_type
    BEFORE INSERT OR UPDATE ON core."followup_prs_responses"
    FOR EACH ROW EXECUTE FUNCTION core.fn_check_prs_appointment_type('protocol_followup');


-- ---------------------------------------------------------------------------
-- 9.4 updated_at
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS trg_updated_at_device_companies ON reference."device_companies";
CREATE TRIGGER trg_updated_at_device_companies    BEFORE UPDATE ON reference."device_companies"    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
DROP TRIGGER IF EXISTS trg_updated_at_neuromod_devices ON reference."neuromod_devices";
CREATE TRIGGER trg_updated_at_neuromod_devices    BEFORE UPDATE ON reference."neuromod_devices"    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
DROP TRIGGER IF EXISTS trg_updated_at_neuromod_conditions ON reference."neuromod_conditions";
CREATE TRIGGER trg_updated_at_neuromod_conditions BEFORE UPDATE ON reference."neuromod_conditions" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
DROP TRIGGER IF EXISTS trg_updated_at_tdcs_placements ON reference."tdcs_placements";
CREATE TRIGGER trg_updated_at_tdcs_placements     BEFORE UPDATE ON reference."tdcs_placements"     FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
DROP TRIGGER IF EXISTS trg_updated_at_hd_tdcs_placements ON reference."hd_tdcs_placements";
CREATE TRIGGER trg_updated_at_hd_tdcs_placements  BEFORE UPDATE ON reference."hd_tdcs_placements"  FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
DROP TRIGGER IF EXISTS trg_updated_at_tavns_placements ON reference."tavns_placements";
CREATE TRIGGER trg_updated_at_tavns_placements    BEFORE UPDATE ON reference."tavns_placements"    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
DROP TRIGGER IF EXISTS trg_updated_at_tps_placements ON reference."tps_placements";
CREATE TRIGGER trg_updated_at_tps_placements      BEFORE UPDATE ON reference."tps_placements"      FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
DROP TRIGGER IF EXISTS trg_updated_at_rtms_placements ON reference."rtms_placements";
CREATE TRIGGER trg_updated_at_rtms_placements     BEFORE UPDATE ON reference."rtms_placements"     FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
DROP TRIGGER IF EXISTS trg_updated_at_other_placements ON reference."other_placements";
CREATE TRIGGER trg_updated_at_other_placements    BEFORE UPDATE ON reference."other_placements"    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
DROP TRIGGER IF EXISTS trg_updated_at_tdcs_dosing ON reference."tdcs_dosing";
CREATE TRIGGER trg_updated_at_tdcs_dosing         BEFORE UPDATE ON reference."tdcs_dosing"         FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
DROP TRIGGER IF EXISTS trg_updated_at_hd_tdcs_dosing ON reference."hd_tdcs_dosing";
CREATE TRIGGER trg_updated_at_hd_tdcs_dosing      BEFORE UPDATE ON reference."hd_tdcs_dosing"      FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
DROP TRIGGER IF EXISTS trg_updated_at_tavns_dosing ON reference."tavns_dosing";
CREATE TRIGGER trg_updated_at_tavns_dosing        BEFORE UPDATE ON reference."tavns_dosing"        FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
DROP TRIGGER IF EXISTS trg_updated_at_tps_dosing ON reference."tps_dosing";
CREATE TRIGGER trg_updated_at_tps_dosing          BEFORE UPDATE ON reference."tps_dosing"          FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
DROP TRIGGER IF EXISTS trg_updated_at_rtms_dosing ON reference."rtms_dosing";
CREATE TRIGGER trg_updated_at_rtms_dosing         BEFORE UPDATE ON reference."rtms_dosing"         FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
DROP TRIGGER IF EXISTS trg_updated_at_other_dosing ON reference."other_dosing";
CREATE TRIGGER trg_updated_at_other_dosing        BEFORE UPDATE ON reference."other_dosing"        FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
DROP TRIGGER IF EXISTS trg_updated_at_treatment_protocols ON core."treatment_protocols";
CREATE TRIGGER trg_updated_at_treatment_protocols BEFORE UPDATE ON core."treatment_protocols"      FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();


-- ---------------------------------------------------------------------------
-- 9.5 Audit
-- ---------------------------------------------------------------------------
-- Clinical tables only. The reference catalogue is superadmin-owned and
-- low-churn; auditing every price-list read-model row is noise.

DROP TRIGGER IF EXISTS trg_audit_treatment_protocols ON core."treatment_protocols";
CREATE TRIGGER trg_audit_treatment_protocols
    AFTER INSERT OR DELETE OR UPDATE ON core."treatment_protocols"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('protocol_id');

DROP TRIGGER IF EXISTS trg_audit_device_session_prs_responses ON core."device_session_prs_responses";
CREATE TRIGGER trg_audit_device_session_prs_responses
    AFTER INSERT OR DELETE OR UPDATE ON core."device_session_prs_responses"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('ds_prs_id');

DROP TRIGGER IF EXISTS trg_audit_followup_prs_responses ON core."followup_prs_responses";
CREATE TRIGGER trg_audit_followup_prs_responses
    AFTER INSERT OR DELETE OR UPDATE ON core."followup_prs_responses"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('fu_prs_id');


-- ---------------------------------------------------------------------------
-- 9.6 The generator
-- ---------------------------------------------------------------------------
-- session_count = 20 with follow_up_every_n = 5 produces 24 appointments rows:
-- 20 device sessions plus 4 follow-ups, after sessions 5, 10, 15 and 20.
--
-- Every row is written in status 'planned' with a DATE and no TIME, which is
-- exactly the state 31 describes: "doctor set a DATE at protocol setup. No slot
-- yet, no hold." The patient later picks a time, moving the row to 'selected'.
--
-- Three things this deliberately does NOT do:
--   - set start_time/end_time. chk_appointments_slotted_has_time permits a
--     missing time only for 'planned' and 'cancelled'; that is the point.
--   - set hold_expires_at. chk_appointments_hold requires it to be NULL for
--     anything that is not 'selected'.
--   - set doctor_id on device sessions. 30 made it nullable precisely so a
--     CA-run session does not occupy the supervising doctor's calendar.
--     Follow-ups DO set it: a follow-up is a doctor consultation.

CREATE OR REPLACE FUNCTION core.fn_generate_protocol_sessions(
    p_protocol_id   UUID,
    p_start_date    DATE,
    p_slot_days     INTEGER DEFAULT 2,
    p_followup_gap  INTEGER DEFAULT 1
)
RETURNS INTEGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_protocol  core.treatment_protocols%ROWTYPE;
    v_plan      core.treatment_plans%ROWTYPE;
    v_clinic_id UUID;
    v_i         INTEGER;
    v_date      DATE;
    v_created   INTEGER := 0;
BEGIN
    SELECT * INTO v_protocol FROM core.treatment_protocols WHERE protocol_id = p_protocol_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Protocol % not found', p_protocol_id;
    END IF;

    SELECT * INTO v_plan FROM core.treatment_plans WHERE plan_id = v_protocol.plan_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Plan % not found for protocol %', v_protocol.plan_id, p_protocol_id;
    END IF;

    -- treatment_plans carries no clinic_id of its own; it comes from the cycle.
    SELECT clinic_id INTO v_clinic_id FROM core.treatment_cycles WHERE cycle_id = v_plan.cycle_id;
    IF v_clinic_id IS NULL THEN
        RAISE EXCEPTION 'Cannot resolve clinic for plan % (cycle %)', v_plan.plan_id, v_plan.cycle_id;
    END IF;

    -- Refuse to double-generate rather than silently appending a second course.
    IF EXISTS (SELECT 1 FROM core.appointments WHERE protocol_id = p_protocol_id) THEN
        RAISE EXCEPTION 'Protocol % has already generated its appointments', p_protocol_id;
    END IF;

    FOR v_i IN 1..v_protocol.session_count LOOP
        v_date := p_start_date + ((v_i - 1) * p_slot_days);

        -- Device session. doctor_id NULL (CA-administered), no time yet.
        INSERT INTO core.appointments (
            clinic_id, patient_id, doctor_id, plan_id, protocol_id,
            appointment_date, appointment_type, session_number,
            status, booked_by, booked_by_role
        ) VALUES (
            v_clinic_id, v_plan.patient_id, NULL, v_plan.plan_id, p_protocol_id,
            v_date, 'device_session', v_i,
            'planned', v_protocol.set_by, 'doctor'
        );
        v_created := v_created + 1;

        -- Follow-up consultation after every Nth session. Carries doctor_id:
        -- this one IS the doctor's visit.
        IF v_protocol.follow_up_every_n IS NOT NULL
           AND v_i % v_protocol.follow_up_every_n = 0 THEN

            INSERT INTO core.appointments (
                clinic_id, patient_id, doctor_id, plan_id, protocol_id,
                appointment_date, appointment_type, session_number,
                status, booked_by, booked_by_role
            ) VALUES (
                v_clinic_id, v_plan.patient_id, v_plan.doctor_id, v_plan.plan_id, p_protocol_id,
                v_date + p_followup_gap, 'protocol_followup', v_i,
                'planned', v_protocol.set_by, 'doctor'
            );
            v_created := v_created + 1;
        END IF;
    END LOOP;

    RETURN v_created;
END;
$function$;

COMMENT ON FUNCTION core.fn_generate_protocol_sessions IS 'Generates every device-session and follow-up appointment for a protocol, all in status planned with a date and no time. Returns the number of rows created. Calendar placement is a fixed day-gap placeholder — real slotting needs doctor_weekly_schedules and doctor_schedule_overrides, which is application work. See OPEN ITEM 1.';


-- ###########################################################################
-- §10  LAYER 3 — RLS
-- ###########################################################################
-- ENABLE + FORCE on every table, policies in the same file as the ENABLE.
-- Enabling RLS without writing policies is what silently locked four tables out
-- of the app earlier (NOTES.md); 30 makes the same point.

ALTER TABLE reference."device_companies"          ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."device_companies"          FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_devices"          ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_devices"          FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_conditions"       ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_conditions"       FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_diagnoses"        ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_diagnoses"        FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_scales"           ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_scales"           FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_condition_scales" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_condition_scales" FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."tdcs_placements"           ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."tdcs_placements"           FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."hd_tdcs_placements"        ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."hd_tdcs_placements"        FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."tavns_placements"          ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."tavns_placements"          FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."tps_placements"            ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."tps_placements"            FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."rtms_placements"           ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."rtms_placements"           FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."other_placements"          ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."other_placements"          FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."tdcs_dosing"               ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."tdcs_dosing"               FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."hd_tdcs_dosing"            ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."hd_tdcs_dosing"            FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."tavns_dosing"              ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."tavns_dosing"              FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."tps_dosing"                ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."tps_dosing"                FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."rtms_dosing"               ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."rtms_dosing"               FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."other_dosing"              ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."other_dosing"              FORCE  ROW LEVEL SECURITY;

ALTER TABLE core."treatment_protocols"          ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."treatment_protocols"          FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."device_session_prs_responses" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."device_session_prs_responses" FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."followup_prs_responses"       ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."followup_prs_responses"       FORCE  ROW LEVEL SECURITY;


-- Catalogue: everyone reads, only super_admin writes. Mirrors
-- reference.products and reference.billable_items exactly.
--
-- Written out rather than generated in a DO loop: every one of the schema's
-- other 152 policies is explicit, and a policy that only exists at runtime
-- cannot be grepped, diffed in review, or compared against the introspection
-- output that regenerates 00-29.

DROP POLICY IF EXISTS "rls_device_companies_select" ON reference."device_companies";
CREATE POLICY "rls_device_companies_select" ON reference."device_companies" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_device_companies_insert" ON reference."device_companies";
CREATE POLICY "rls_device_companies_insert" ON reference."device_companies" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_device_companies_update" ON reference."device_companies";
CREATE POLICY "rls_device_companies_update" ON reference."device_companies" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_neuromod_devices_select" ON reference."neuromod_devices";
CREATE POLICY "rls_neuromod_devices_select" ON reference."neuromod_devices" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_neuromod_devices_insert" ON reference."neuromod_devices";
CREATE POLICY "rls_neuromod_devices_insert" ON reference."neuromod_devices" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_neuromod_devices_update" ON reference."neuromod_devices";
CREATE POLICY "rls_neuromod_devices_update" ON reference."neuromod_devices" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_neuromod_conditions_select" ON reference."neuromod_conditions";
CREATE POLICY "rls_neuromod_conditions_select" ON reference."neuromod_conditions" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_neuromod_conditions_insert" ON reference."neuromod_conditions";
CREATE POLICY "rls_neuromod_conditions_insert" ON reference."neuromod_conditions" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_neuromod_conditions_update" ON reference."neuromod_conditions";
CREATE POLICY "rls_neuromod_conditions_update" ON reference."neuromod_conditions" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_neuromod_diagnoses_select" ON reference."neuromod_diagnoses";
CREATE POLICY "rls_neuromod_diagnoses_select" ON reference."neuromod_diagnoses" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_neuromod_diagnoses_insert" ON reference."neuromod_diagnoses";
CREATE POLICY "rls_neuromod_diagnoses_insert" ON reference."neuromod_diagnoses" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_neuromod_diagnoses_update" ON reference."neuromod_diagnoses";
CREATE POLICY "rls_neuromod_diagnoses_update" ON reference."neuromod_diagnoses" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_neuromod_scales_select" ON reference."neuromod_scales";
CREATE POLICY "rls_neuromod_scales_select" ON reference."neuromod_scales" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_neuromod_scales_insert" ON reference."neuromod_scales";
CREATE POLICY "rls_neuromod_scales_insert" ON reference."neuromod_scales" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_neuromod_scales_update" ON reference."neuromod_scales";
CREATE POLICY "rls_neuromod_scales_update" ON reference."neuromod_scales" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_neuromod_condition_scales_select" ON reference."neuromod_condition_scales";
CREATE POLICY "rls_neuromod_condition_scales_select" ON reference."neuromod_condition_scales" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_neuromod_condition_scales_insert" ON reference."neuromod_condition_scales";
CREATE POLICY "rls_neuromod_condition_scales_insert" ON reference."neuromod_condition_scales" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_neuromod_condition_scales_update" ON reference."neuromod_condition_scales";
CREATE POLICY "rls_neuromod_condition_scales_update" ON reference."neuromod_condition_scales" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_tdcs_placements_select" ON reference."tdcs_placements";
CREATE POLICY "rls_tdcs_placements_select" ON reference."tdcs_placements" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_tdcs_placements_insert" ON reference."tdcs_placements";
CREATE POLICY "rls_tdcs_placements_insert" ON reference."tdcs_placements" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_tdcs_placements_update" ON reference."tdcs_placements";
CREATE POLICY "rls_tdcs_placements_update" ON reference."tdcs_placements" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_hd_tdcs_placements_select" ON reference."hd_tdcs_placements";
CREATE POLICY "rls_hd_tdcs_placements_select" ON reference."hd_tdcs_placements" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_hd_tdcs_placements_insert" ON reference."hd_tdcs_placements";
CREATE POLICY "rls_hd_tdcs_placements_insert" ON reference."hd_tdcs_placements" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_hd_tdcs_placements_update" ON reference."hd_tdcs_placements";
CREATE POLICY "rls_hd_tdcs_placements_update" ON reference."hd_tdcs_placements" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_tavns_placements_select" ON reference."tavns_placements";
CREATE POLICY "rls_tavns_placements_select" ON reference."tavns_placements" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_tavns_placements_insert" ON reference."tavns_placements";
CREATE POLICY "rls_tavns_placements_insert" ON reference."tavns_placements" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_tavns_placements_update" ON reference."tavns_placements";
CREATE POLICY "rls_tavns_placements_update" ON reference."tavns_placements" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_tps_placements_select" ON reference."tps_placements";
CREATE POLICY "rls_tps_placements_select" ON reference."tps_placements" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_tps_placements_insert" ON reference."tps_placements";
CREATE POLICY "rls_tps_placements_insert" ON reference."tps_placements" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_tps_placements_update" ON reference."tps_placements";
CREATE POLICY "rls_tps_placements_update" ON reference."tps_placements" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_rtms_placements_select" ON reference."rtms_placements";
CREATE POLICY "rls_rtms_placements_select" ON reference."rtms_placements" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_rtms_placements_insert" ON reference."rtms_placements";
CREATE POLICY "rls_rtms_placements_insert" ON reference."rtms_placements" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_rtms_placements_update" ON reference."rtms_placements";
CREATE POLICY "rls_rtms_placements_update" ON reference."rtms_placements" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_other_placements_select" ON reference."other_placements";
CREATE POLICY "rls_other_placements_select" ON reference."other_placements" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_other_placements_insert" ON reference."other_placements";
CREATE POLICY "rls_other_placements_insert" ON reference."other_placements" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_other_placements_update" ON reference."other_placements";
CREATE POLICY "rls_other_placements_update" ON reference."other_placements" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_tdcs_dosing_select" ON reference."tdcs_dosing";
CREATE POLICY "rls_tdcs_dosing_select" ON reference."tdcs_dosing" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_tdcs_dosing_insert" ON reference."tdcs_dosing";
CREATE POLICY "rls_tdcs_dosing_insert" ON reference."tdcs_dosing" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_tdcs_dosing_update" ON reference."tdcs_dosing";
CREATE POLICY "rls_tdcs_dosing_update" ON reference."tdcs_dosing" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_hd_tdcs_dosing_select" ON reference."hd_tdcs_dosing";
CREATE POLICY "rls_hd_tdcs_dosing_select" ON reference."hd_tdcs_dosing" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_hd_tdcs_dosing_insert" ON reference."hd_tdcs_dosing";
CREATE POLICY "rls_hd_tdcs_dosing_insert" ON reference."hd_tdcs_dosing" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_hd_tdcs_dosing_update" ON reference."hd_tdcs_dosing";
CREATE POLICY "rls_hd_tdcs_dosing_update" ON reference."hd_tdcs_dosing" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_tavns_dosing_select" ON reference."tavns_dosing";
CREATE POLICY "rls_tavns_dosing_select" ON reference."tavns_dosing" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_tavns_dosing_insert" ON reference."tavns_dosing";
CREATE POLICY "rls_tavns_dosing_insert" ON reference."tavns_dosing" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_tavns_dosing_update" ON reference."tavns_dosing";
CREATE POLICY "rls_tavns_dosing_update" ON reference."tavns_dosing" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_tps_dosing_select" ON reference."tps_dosing";
CREATE POLICY "rls_tps_dosing_select" ON reference."tps_dosing" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_tps_dosing_insert" ON reference."tps_dosing";
CREATE POLICY "rls_tps_dosing_insert" ON reference."tps_dosing" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_tps_dosing_update" ON reference."tps_dosing";
CREATE POLICY "rls_tps_dosing_update" ON reference."tps_dosing" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_rtms_dosing_select" ON reference."rtms_dosing";
CREATE POLICY "rls_rtms_dosing_select" ON reference."rtms_dosing" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_rtms_dosing_insert" ON reference."rtms_dosing";
CREATE POLICY "rls_rtms_dosing_insert" ON reference."rtms_dosing" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_rtms_dosing_update" ON reference."rtms_dosing";
CREATE POLICY "rls_rtms_dosing_update" ON reference."rtms_dosing" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

DROP POLICY IF EXISTS "rls_other_dosing_select" ON reference."other_dosing";
CREATE POLICY "rls_other_dosing_select" ON reference."other_dosing" FOR SELECT TO public
    USING (true);
DROP POLICY IF EXISTS "rls_other_dosing_insert" ON reference."other_dosing";
CREATE POLICY "rls_other_dosing_insert" ON reference."other_dosing" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
DROP POLICY IF EXISTS "rls_other_dosing_update" ON reference."other_dosing";
CREATE POLICY "rls_other_dosing_update" ON reference."other_dosing" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));



-- treatment_protocols: staff in the clinic, plus the patient it belongs to.
-- Reached through treatment_plans -> treatment_cycles, which is where clinic_id
-- actually lives.
-- The clinic branch below cannot be the only one. ops.rls_clinic_id() reads
-- app.current_clinic_id, which AuthContextMiddleware never sets, so it returns
-- NULL for every caller and that branch is never true. Left in place (inert)
-- for whenever the GUC is set, with a clinic_staff_assignments branch alongside
-- it that works today.
--
-- Without that second branch a clinical assistant — the person who actually
-- administers a device session — could read no protocol at all, and so could
-- not see the montage or dose they are meant to deliver. Keying off
-- appointments.ca_id would not help: ca_id stays NULL until the assistant
-- STARTS the session, which is after they need the dose.
DROP POLICY IF EXISTS "rls_treatment_protocols_select" ON core."treatment_protocols";
CREATE POLICY "rls_treatment_protocols_select" ON core."treatment_protocols" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR (plan_id IN (
            SELECT p.plan_id FROM treatment_plans p
            JOIN treatment_cycles c ON c.cycle_id = p.cycle_id
            WHERE c.clinic_id = rls_clinic_id()
               OR p.patient_id = rls_user_id()
               OR p.doctor_id  = rls_user_id()))
        OR (plan_id IN (
            SELECT p.plan_id
            FROM treatment_plans p
            JOIN treatment_cycles c         ON c.cycle_id  = p.cycle_id
            JOIN clinic_staff_assignments s ON s.clinic_id = c.clinic_id
            WHERE s.profile_id = rls_user_id()
              AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_treatment_protocols_insert" ON core."treatment_protocols";
CREATE POLICY "rls_treatment_protocols_insert" ON core."treatment_protocols" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text])
    );

DROP POLICY IF EXISTS "rls_treatment_protocols_update" ON core."treatment_protocols";
CREATE POLICY "rls_treatment_protocols_update" ON core."treatment_protocols" FOR UPDATE TO public
    USING (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text])
    );

-- PRS responses: the patient reads their own, staff read their clinic's.
DROP POLICY IF EXISTS "rls_ds_prs_select" ON core."device_session_prs_responses";
CREATE POLICY "rls_ds_prs_select" ON core."device_session_prs_responses" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR (patient_id = rls_user_id())
        OR (appointment_id IN (SELECT a.appointment_id FROM appointments a
                               WHERE a.clinic_id = rls_clinic_id()
                                  OR a.doctor_id = rls_user_id()
                                  OR a.ca_id     = rls_user_id()))
        -- clinic staff, resolved without the always-NULL rls_clinic_id()
        OR (appointment_id IN (
            SELECT a.appointment_id FROM appointments a
            JOIN clinic_staff_assignments s ON s.clinic_id = a.clinic_id
            WHERE s.profile_id = rls_user_id() AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_ds_prs_insert" ON core."device_session_prs_responses";
CREATE POLICY "rls_ds_prs_insert" ON core."device_session_prs_responses" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text,
                                      'clinical_assistant'::text, 'system'::text]))
        OR (patient_id = rls_user_id())
    );

DROP POLICY IF EXISTS "rls_ds_prs_update" ON core."device_session_prs_responses";
CREATE POLICY "rls_ds_prs_update" ON core."device_session_prs_responses" FOR UPDATE TO public
    USING (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text])
    );

DROP POLICY IF EXISTS "rls_fu_prs_select" ON core."followup_prs_responses";
CREATE POLICY "rls_fu_prs_select" ON core."followup_prs_responses" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR (patient_id = rls_user_id())
        OR (appointment_id IN (SELECT a.appointment_id FROM appointments a
                               WHERE a.clinic_id = rls_clinic_id()
                                  OR a.doctor_id = rls_user_id()
                                  OR a.ca_id     = rls_user_id()))
        -- clinic staff, resolved without the always-NULL rls_clinic_id()
        OR (appointment_id IN (
            SELECT a.appointment_id FROM appointments a
            JOIN clinic_staff_assignments s ON s.clinic_id = a.clinic_id
            WHERE s.profile_id = rls_user_id() AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_fu_prs_insert" ON core."followup_prs_responses";
CREATE POLICY "rls_fu_prs_insert" ON core."followup_prs_responses" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text,
                                      'clinical_assistant'::text, 'system'::text]))
        OR (patient_id = rls_user_id())
    );

DROP POLICY IF EXISTS "rls_fu_prs_update" ON core."followup_prs_responses";
CREATE POLICY "rls_fu_prs_update" ON core."followup_prs_responses" FOR UPDATE TO public
    USING (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text])
    );


-- ###########################################################################
-- §11  LAYER 3 — Grants
-- ###########################################################################
-- 18_grants.sql's GRANT ... ON ALL TABLES ran before these tables existed, and
-- ALTER DEFAULT PRIVILEGES only covers tables created by the role that set it.
-- Granting explicitly so none of these becomes the next silent lockout.

GRANT SELECT, INSERT, UPDATE ON
    core."treatment_protocols", core."device_session_prs_responses", core."followup_prs_responses"
    TO anava_app;

-- v2 Layer 3: "REVOKE DELETE ... deletion belongs to the purge worker." All
-- three are Bucket 2 (anonymise, never delete-now), so the application role has
-- no legitimate reason to DELETE from any of them. The REVOKE is required
-- separately because 18_grants.sql's ALTER DEFAULT PRIVILEGES grants DELETE on
-- every future core table.
REVOKE DELETE ON
    core."treatment_protocols", core."device_session_prs_responses", core."followup_prs_responses"
    FROM anava_app;

-- Catalogue is read-only to the app, for the same reason 30 made billable_items
-- read-only: an application bug must not be able to silently alter a prescribed
-- dose or montage.
GRANT SELECT ON
    reference."device_companies",
    reference."neuromod_devices", reference."neuromod_conditions",
    reference."neuromod_diagnoses", reference."neuromod_scales",
    reference."neuromod_condition_scales",
    reference."tdcs_placements", reference."hd_tdcs_placements",
    reference."tavns_placements", reference."tps_placements",
    reference."rtms_placements", reference."other_placements",
    reference."tdcs_dosing", reference."hd_tdcs_dosing",
    reference."tavns_dosing", reference."tps_dosing",
    reference."rtms_dosing", reference."other_dosing"
    TO anava_app;

REVOKE INSERT, UPDATE, DELETE ON
    reference."device_companies",
    reference."neuromod_devices", reference."neuromod_conditions",
    reference."neuromod_diagnoses", reference."neuromod_scales",
    reference."neuromod_condition_scales",
    reference."tdcs_placements", reference."hd_tdcs_placements",
    reference."tavns_placements", reference."tps_placements",
    reference."rtms_placements", reference."other_placements",
    reference."tdcs_dosing", reference."hd_tdcs_dosing",
    reference."tavns_dosing", reference."tps_dosing",
    reference."rtms_dosing", reference."other_dosing"
    FROM anava_app;

GRANT SELECT ON
    core."treatment_protocols", core."device_session_prs_responses", core."followup_prs_responses",
    reference."device_companies",
    reference."neuromod_devices", reference."neuromod_conditions",
    reference."neuromod_diagnoses", reference."neuromod_scales",
    reference."neuromod_condition_scales",
    reference."tdcs_placements", reference."hd_tdcs_placements",
    reference."tavns_placements", reference."tps_placements",
    reference."rtms_placements", reference."other_placements",
    reference."tdcs_dosing", reference."hd_tdcs_dosing",
    reference."tavns_dosing", reference."tps_dosing",
    reference."rtms_dosing", reference."other_dosing"
    TO anava_readonly;

-- anava_compliance exists (02_roles.sql) and 24_layer5_grants.sql grants it read
-- access to clinical tables, because an erasure or portability request has to be
-- able to see the data it is reporting on. All three tables below hold patient
-- data and are Bucket 2 (anonymise, never hard-delete), so the compliance role
-- must reach them or those requests silently under-report.
GRANT SELECT ON
    core."treatment_protocols", core."device_session_prs_responses", core."followup_prs_responses"
    TO anava_compliance;

GRANT EXECUTE ON FUNCTION core.fn_generate_protocol_sessions(UUID, DATE, INTEGER, INTEGER) TO anava_app;


COMMIT;


-- ###########################################################################
-- NO SEED DATA
-- ###########################################################################
-- The device registry, conditions, ICD-10 codes, montages and doses from the
-- protocol mapping sheet are deliberately not inserted here. Seeding clinical
-- reference data is a separate, reviewable step: a wrong montage or dose that
-- arrives silently with a schema migration is a clinical safety problem, not a
-- schema problem. An empty catalogue fails loudly the first time a doctor opens
-- the protocol screen; a wrong one does not fail at all.
--
-- Notes for the seed script:
--   - reference.neuromod_devices.phase gates the UI. Insert tDCS and HD-tDCS
--     with phase = 1, the other four with phase = 2.
--   - reference.device_companies must be seeded BEFORE neuromod_devices, and
--     every device row should set company_id even though the column is
--     nullable (see OPEN ITEM 10). Join on company_code, not company_name.
--   - The same modality legitimately appears once per vendor. Two tDCS units
--     from different companies are two device rows, each with its own
--     placements and doses — a montage validated on one is not automatically
--     valid on the other.


-- ###########################################################################
-- OPEN ITEMS — flagged, not silently assumed
-- ###########################################################################
--
--   1. fn_generate_protocol_sessions places dates on a fixed day gap. It
--      guarantees the fan-out SHAPE (N sessions + N/K follow-ups, correctly
--      interleaved) and nothing about calendar realism — it does not consult
--      doctor_weekly_schedules or doctor_schedule_overrides, and it does not
--      skip weekends or clinic holidays. Real slotting is application work.
--      Because every generated row is 'planned' with no time, none of them can
--      collide with excl_doctor_overlap or excl_ca_overlap yet; the collision
--      surface appears when the patient books a slot, which is exactly where
--      31's guards apply.
--
--   2. The two PRS tables are 1:1 with their visit (UNIQUE on appointment_id).
--      If a patient can retake a PRS within one visit, both constraints must be
--      dropped and replaced with something that admits multiple rows.
--
--   3. appointment_type is still unconstrained. 30 and 31 both defer the CHECK
--      because the staff booking path writes seven legacy values. This file's
--      generator writes 'device_session' and 'protocol_followup' — the agreed
--      vocabulary — and the PRS triggers in §9.2 test those literals. When the
--      CHECK finally lands, 'device_session' and 'protocol_followup' must be in it.
--
--   4. appointments.status is likewise unconstrained; the generator writes
--      'planned', which 31 documents as the protocol-setup state. Same
--      dependency: 'planned' must survive into the eventual status CHECK.
--
--   5. Cross-device reporting ("every placement this patient has had") is a
--      6-way UNION rather than one join. Accepted cost of the per-device split.
--
--   6. The same-device rule is a trigger, not a CHECK, because a CHECK cannot
--      read another table. Same trade.
--
--   7. Pricing is not wired here. reference.billable_items (30 §5) prices a
--      device_session by treatment_plans.device_type, which is a free-text
--      column, while this module keys devices by reference.neuromod_devices.
--      Those two vocabularies need reconciling before a device session can be
--      priced — most cleanly by adding billable_items.device_id as a real FK.
--      Out of scope here; it belongs to the payments workstream.
--
--   8. v2 open decision 4 asks whether core.devices exists. It does not, and
--      this module does not create it: reference.neuromod_devices is a registry
--      of prescribable device TYPES — (manufacturer, model, modality) — not of
--      physical machines. The session slot guard v2 wants needs per-unit
--      hardware rows. See OPEN ITEM 11.
--
--   9. Not executed anywhere. Restore a snapshot, run this file against it,
--      then re-run the constraint tests before it goes near Anava_App_v1.
--
--  10. reference.neuromod_devices.company_id is NULLABLE, and should not stay
--      that way. It is nullable only so this file applies cleanly whether or
--      not a device registry was already seeded. Once every device row carries
--      a company, promote it in a follow-up script:
--
--          ALTER TABLE reference.neuromod_devices
--              ADD CONSTRAINT chk_neuromod_devices_has_company
--              CHECK (company_id IS NOT NULL) NOT VALID;
--          -- verify: SELECT count(*) FROM reference.neuromod_devices
--          --         WHERE company_id IS NULL;   -- must be 0
--          ALTER TABLE reference.neuromod_devices
--              VALIDATE CONSTRAINT chk_neuromod_devices_has_company;
--
--      NOT VALID first, then VALIDATE, per v2 Layer 6 — the same pattern the
--      appointments constraints in §7 use. A plain SET NOT NULL takes an ACCESS
--      EXCLUSIVE lock and scans the whole table in one shot.
--
--  11. Device COMPANY is modelled; device UNIT is not. reference.neuromod_devices
--      is one row per (manufacturer, model) — a prescribable device type, not a
--      serial-numbered machine sitting in a treatment room. Per-unit tracking
--      (serial number, clinic assignment, service history, calibration due
--      date) is what v2 open decision 4's "core.devices" slot guard would need,
--      and it is a different table in a different workstream. Nothing here
--      blocks it: a future core.device_units would FK to neuromod_devices for
--      its type and to clinics for its location.
-- ###########################################################################
