-- 39_protocol_prescription_fields.sql
--
-- Hand-written net-new DDL. Closes the last gaps between the Treatment
-- Protocol wizard and what the schema can store, after 38 covered custom
-- montages, per-scale cadence, and the conditions/diagnoses that were being
-- computed and then discarded.
--
-- SCOPE: columns on core.treatment_protocols, plus one table for the
-- free-text dosing the mapping sheet actually contains. ADDITIVE ONLY —
-- nothing existing is altered or dropped.
--
-- APPLY ORDER: after 38. Independent of 33-37.
--
-- NOT YET EXECUTED ANYWHERE.
--
--
-- ###########################################################################
-- WHAT THE WIZARD STILL COLLECTS AND THE SCHEMA STILL DISCARDS
-- ###########################################################################
--
-- Step 1  "Assign the protocol to an appointment." A protocol is authored
--         DURING a consultation. 30 recorded that link for treatment_plans
--         (authored_in_appointment_id) but treatment_protocols never got the
--         equivalent, so which visit produced a prescription was unanswerable.
--
-- Step 5  Four of the five fields on the Stimulation Parameters screen had
--         nowhere to live:
--             Current intensity   2.0 mA      -> device_settings jsonb (untyped)
--             Session duration    30 min      -> device_settings jsonb (untyped)
--             Ramp up / down      30 sec      -> nowhere at all
--             Total sessions      20          -> session_count (OK)
--             Frequency           5x / week   -> nowhere at all
--
--         The prescribed dose was being stuffed into an untyped jsonb blob.
--         For the field that decides how much current enters a patient's head
--         that is the wrong storage: no CHECK, no range validation, no way to
--         query "every protocol above 2 mA" during a safety review.
--
--         Frequency is worse than merely missing. The generator needs it to
--         lay out the calendar, so it was passed to POST /treatment-protocols,
--         used once, and thrown away — meaning a protocol could not be
--         regenerated or audited against the cadence it was prescribed with.
--
-- Step 8  A protocol that is superseded by an amendment had no way to point at
--         its replacement, though treatment_plans has had parent_plan_id since
--         05. 'superseded' was already a legal status with nothing to link to.
--
--
-- ###########################################################################
-- WHY prescribed_* COLUMNS AND NOT MORE JSONB
-- ###########################################################################
--
-- device_settings stays exactly as 32 describes it: per-patient DEVIATIONS
-- from the catalogue dose. That is a genuinely open-ended bag and jsonb is
-- right for it.
--
-- The prescription itself is not open-ended. Every device family in the
-- catalogue is dosed in current, duration and a session count; the wizard
-- shows those same four boxes whichever device is picked. They are typed
-- columns with CHECKs because they are clinical safety values, and because
-- "reduced current for tolerability" is only a meaningful deviation if there
-- is a typed number to deviate FROM.
--
-- The v2 board's Layer 2 rule is followed throughout: text + CHECK, never a
-- new native enum — ALTER TYPE can never remove a value.


BEGIN;


-- ###########################################################################
-- 1  Provenance — which consultation authored this protocol  (Step 1)
-- ###########################################################################
-- Mirrors treatment_plans.authored_in_appointment_id from 30, and carries the
-- same warning: this is provenance, NEVER a join path. Reaching a protocol's
-- device sessions by walking through the authoring appointment would rebuild
-- exactly the appointment-owns-sessions hierarchy that 30's hatched gutter
-- exists to prevent. Sessions are found by appointments.protocol_id.

ALTER TABLE core."treatment_protocols"
    ADD COLUMN IF NOT EXISTS "authored_in_appointment_id" UUID;

COMMENT ON COLUMN core."treatment_protocols"."authored_in_appointment_id" IS
    'The consultation during which this protocol was set. Provenance only — never joined through to reach sessions, which are found via appointments.protocol_id. Nullable: a protocol may be authored outside a booked visit (ward round, tele-review).';

ALTER TABLE core."treatment_protocols"
    DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_authored_in_appointment_id";
ALTER TABLE core."treatment_protocols"
    ADD CONSTRAINT "fk_treatment_protocols_authored_in_appointment_id"
    FOREIGN KEY ("authored_in_appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT
    NOT VALID;

CREATE INDEX IF NOT EXISTS idx_treatment_protocols_authored_in
    ON core."treatment_protocols" USING btree ("authored_in_appointment_id")
    WHERE "authored_in_appointment_id" IS NOT NULL;


-- ###########################################################################
-- 2  The prescribed dose  (Step 5)
-- ###########################################################################
-- Typed, range-checked, queryable. Nullable because the wizard's own "Other
-- condition (free text)" path states plainly: "No suggested montage or dosing
-- will be available — placement and parameters must be set manually." A
-- protocol may legitimately be saved as a draft before these are filled in;
-- §4 below requires them at activation, which is the point they become a
-- live prescription.

ALTER TABLE core."treatment_protocols"
    ADD COLUMN IF NOT EXISTS "prescribed_current_ma" NUMERIC(4,2);
ALTER TABLE core."treatment_protocols"
    ADD COLUMN IF NOT EXISTS "prescribed_duration_min" INTEGER;
ALTER TABLE core."treatment_protocols"
    ADD COLUMN IF NOT EXISTS "ramp_seconds" INTEGER NOT NULL DEFAULT 30;
ALTER TABLE core."treatment_protocols"
    ADD COLUMN IF NOT EXISTS "sessions_per_week" INTEGER;

COMMENT ON COLUMN core."treatment_protocols"."prescribed_current_ma" IS
    'Current intensity actually prescribed, in mA. The catalogue row (reference.*_dosing) is the recommendation; this is what the doctor set. numeric(4,2) reaches 99.99 — ample for tDCS (1-2 mA) and for rTMS expressed as intensity rather than %MT.';
COMMENT ON COLUMN core."treatment_protocols"."prescribed_duration_min" IS
    'Session duration in minutes as prescribed. Library default for Depression on tDCS is 30.';
COMMENT ON COLUMN core."treatment_protocols"."ramp_seconds" IS
    'Ramp up AND down, in seconds — the wizard collects one value and applies it to both ends. Defaults to 30, the value the UI pre-fills, so existing rows are correct rather than null.';
COMMENT ON COLUMN core."treatment_protocols"."sessions_per_week" IS
    'Prescribed cadence: 1, 2, 3, 5 or 7 (the wizard offers 1x/2x/3x/5x/Daily). Persisted so the calendar can be regenerated and audited against what was prescribed, rather than being consumed once by the generator and lost.';

-- 4 mA is roughly double the highest routine tDCS dose and well above anything
-- in the mapping sheet (1-2 mA). It is a typo guard against a misplaced
-- decimal point (20 instead of 2.0), not a clinical limit.
ALTER TABLE core."treatment_protocols"
    DROP CONSTRAINT IF EXISTS "chk_treatment_protocols_current_ma";
ALTER TABLE core."treatment_protocols"
    ADD CONSTRAINT "chk_treatment_protocols_current_ma"
    CHECK ("prescribed_current_ma" IS NULL
           OR ("prescribed_current_ma" > 0 AND "prescribed_current_ma" <= 4))
    NOT VALID;

ALTER TABLE core."treatment_protocols"
    DROP CONSTRAINT IF EXISTS "chk_treatment_protocols_duration_min";
ALTER TABLE core."treatment_protocols"
    ADD CONSTRAINT "chk_treatment_protocols_duration_min"
    CHECK ("prescribed_duration_min" IS NULL
           OR ("prescribed_duration_min" > 0 AND "prescribed_duration_min" <= 120))
    NOT VALID;

ALTER TABLE core."treatment_protocols"
    DROP CONSTRAINT IF EXISTS "chk_treatment_protocols_ramp";
ALTER TABLE core."treatment_protocols"
    ADD CONSTRAINT "chk_treatment_protocols_ramp"
    CHECK ("ramp_seconds" >= 0 AND "ramp_seconds" <= 120)
    NOT VALID;

-- The exact set the wizard offers. Anything else means the UI and the schema
-- have drifted apart, which should fail loudly.
ALTER TABLE core."treatment_protocols"
    DROP CONSTRAINT IF EXISTS "chk_treatment_protocols_sessions_per_week";
ALTER TABLE core."treatment_protocols"
    ADD CONSTRAINT "chk_treatment_protocols_sessions_per_week"
    CHECK ("sessions_per_week" IS NULL OR "sessions_per_week" IN (1, 2, 3, 5, 7))
    NOT VALID;


-- ###########################################################################
-- 3  Amendment chain  (Step 8)
-- ###########################################################################
-- 'superseded' has been a legal status since 32 with nothing to point at.
-- Mirrors treatment_plans.parent_plan_id.

ALTER TABLE core."treatment_protocols"
    ADD COLUMN IF NOT EXISTS "supersedes_protocol_id" UUID;

COMMENT ON COLUMN core."treatment_protocols"."supersedes_protocol_id" IS
    'The protocol this one replaces. An active protocol is never edited in place — its appointments exist and the patient has been notified — so an amendment is a new protocol pointing back at the old one.';

ALTER TABLE core."treatment_protocols"
    DROP CONSTRAINT IF EXISTS "fk_treatment_protocols_supersedes";
ALTER TABLE core."treatment_protocols"
    ADD CONSTRAINT "fk_treatment_protocols_supersedes"
    FOREIGN KEY ("supersedes_protocol_id") REFERENCES core."treatment_protocols" ("protocol_id") ON DELETE RESTRICT
    NOT VALID;

-- A protocol cannot supersede itself.
ALTER TABLE core."treatment_protocols"
    DROP CONSTRAINT IF EXISTS "chk_treatment_protocols_no_self_supersede";
ALTER TABLE core."treatment_protocols"
    ADD CONSTRAINT "chk_treatment_protocols_no_self_supersede"
    CHECK ("supersedes_protocol_id" IS NULL OR "supersedes_protocol_id" <> "protocol_id")
    NOT VALID;

-- One live amendment per superseded protocol. Partial, so the many rows with
-- a NULL parent never collide.
DROP INDEX IF EXISTS core.uq_treatment_protocols_supersedes;
CREATE UNIQUE INDEX uq_treatment_protocols_supersedes
    ON core."treatment_protocols" ("supersedes_protocol_id")
    WHERE "supersedes_protocol_id" IS NOT NULL AND "status" <> 'cancelled';


-- ###########################################################################
-- 4  A live prescription must be complete
-- ###########################################################################
-- The columns above are nullable so a half-finished draft can be saved. That
-- must not extend to an ACTIVE protocol: the moment it is pushed, appointments
-- are booked and a clinical assistant will read these numbers off the screen
-- and set them on the machine. A NULL current at that point is not a missing
-- field, it is an unanswerable question at the bedside.
--
-- A trigger, not a CHECK, only because it reads the row's own status alongside
-- four other columns and produces a specific message naming what is missing —
-- a CHECK could express the rule but not which field failed.

CREATE OR REPLACE FUNCTION core.fn_check_protocol_prescription_complete()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    missing TEXT[] := ARRAY[]::TEXT[];
BEGIN
    IF NEW.status NOT IN ('active', 'completed') THEN
        RETURN NEW;
    END IF;

    IF NEW.prescribed_current_ma IS NULL THEN
        missing := missing || 'prescribed_current_ma';
    END IF;
    IF NEW.prescribed_duration_min IS NULL THEN
        missing := missing || 'prescribed_duration_min';
    END IF;
    IF NEW.sessions_per_week IS NULL THEN
        missing := missing || 'sessions_per_week';
    END IF;

    IF array_length(missing, 1) > 0 THEN
        RAISE EXCEPTION
            'Protocol % cannot be % — prescription incomplete: %',
            NEW.protocol_id, NEW.status, array_to_string(missing, ', ')
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_check_protocol_prescription_complete ON core."treatment_protocols";
CREATE TRIGGER trg_check_protocol_prescription_complete
    BEFORE INSERT OR UPDATE ON core."treatment_protocols"
    FOR EACH ROW EXECUTE FUNCTION core.fn_check_protocol_prescription_complete();


-- ###########################################################################
-- 5  Catalogue dosing as the sheet actually states it
-- ###########################################################################
-- The mapping sheet does not give tidy numbers. It gives:
--
--   Depression   1-2 mA   30 min   "1 per day x 10 days (20-30 days attempted)"
--   Anxiety      1-2 mA   "Not specified"   "Not specified"
--   Chronic Pain "Not specified" throughout, Evidence C
--
-- 32 already models the ranges (current_ma_min/max) and keeps the session
-- count as num_sessions_text precisely because "1 per day x 10 days (20-30
-- attempted)" is a protocol phrase, not an integer. What it has no field for
-- is the sheet's most common value: the explicit "Not specified".
--
-- That distinction is clinical, not cosmetic. A NULL duration currently means
-- two different things — "nobody has entered it yet" and "the evidence base
-- does not specify one" — and the wizard must behave differently for each:
-- the first is a gap to fill, the second is a documented absence the doctor
-- must resolve from judgement. Chronic Pain is Evidence C with every dosing
-- column unspecified; that is the whole point of the row, and it should be
-- readable as such rather than looking like an incomplete import.

CREATE TABLE IF NOT EXISTS reference."dosing_unspecified_notes" (
    "note_id"        UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"   UUID NOT NULL,
    "device_id"      UUID NOT NULL,
    -- Which dosing field the source sheet leaves open.
    "field_name"     TEXT NOT NULL,
    -- Verbatim from the sheet, e.g. 'Not specified', 'Varied / Not specified'.
    "source_text"    TEXT NOT NULL,
    "created_at"     TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE reference."dosing_unspecified_notes" IS
    'Records that the evidence base explicitly does NOT specify a dosing field for a condition+device, as opposed to the value merely being absent from our import. Retention: Bucket 3 — evidences why a doctor had to set a parameter by judgement.';
COMMENT ON COLUMN reference."dosing_unspecified_notes"."source_text" IS
    'Verbatim from the mapping sheet. "Not specified" (Anxiety, Chronic Pain) and "Varied / Not specified" (ADHD, ASD) are different statements and both are preserved.';

ALTER TABLE reference."dosing_unspecified_notes"
    DROP CONSTRAINT IF EXISTS "dosing_unspecified_notes_pkey" CASCADE;
ALTER TABLE reference."dosing_unspecified_notes"
    ADD CONSTRAINT "dosing_unspecified_notes_pkey" PRIMARY KEY ("note_id");

ALTER TABLE reference."dosing_unspecified_notes"
    DROP CONSTRAINT IF EXISTS "uq_dosing_unspecified_notes";
ALTER TABLE reference."dosing_unspecified_notes"
    ADD CONSTRAINT "uq_dosing_unspecified_notes"
    UNIQUE ("condition_id", "device_id", "field_name");

ALTER TABLE reference."dosing_unspecified_notes"
    DROP CONSTRAINT IF EXISTS "chk_dosing_unspecified_field";
ALTER TABLE reference."dosing_unspecified_notes"
    ADD CONSTRAINT "chk_dosing_unspecified_field"
    CHECK ("field_name" IN ('placement', 'current_ma', 'session_duration_min', 'num_sessions'));

ALTER TABLE reference."dosing_unspecified_notes"
    DROP CONSTRAINT IF EXISTS "fk_dosing_unspecified_condition";
ALTER TABLE reference."dosing_unspecified_notes"
    ADD CONSTRAINT "fk_dosing_unspecified_condition"
    FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;

ALTER TABLE reference."dosing_unspecified_notes"
    DROP CONSTRAINT IF EXISTS "fk_dosing_unspecified_device";
ALTER TABLE reference."dosing_unspecified_notes"
    ADD CONSTRAINT "fk_dosing_unspecified_device"
    FOREIGN KEY ("device_id") REFERENCES reference."neuromod_devices" ("device_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_dosing_unspecified_condition
    ON reference."dosing_unspecified_notes" USING btree ("condition_id", "device_id");


-- ###########################################################################
-- 6  Access control
-- ###########################################################################
-- Same split as every other catalogue table: everyone reads, only super_admin
-- writes. A booking-handler bug must not be able to rewrite what the evidence
-- base says.

ALTER TABLE reference."dosing_unspecified_notes" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."dosing_unspecified_notes" FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_dosing_unspecified_select" ON reference."dosing_unspecified_notes";
CREATE POLICY "rls_dosing_unspecified_select" ON reference."dosing_unspecified_notes"
    FOR SELECT TO public USING (true);

DROP POLICY IF EXISTS "rls_dosing_unspecified_insert" ON reference."dosing_unspecified_notes";
CREATE POLICY "rls_dosing_unspecified_insert" ON reference."dosing_unspecified_notes"
    FOR INSERT TO public WITH CHECK (rls_user_role() = 'super_admin'::text);

DROP POLICY IF EXISTS "rls_dosing_unspecified_update" ON reference."dosing_unspecified_notes";
CREATE POLICY "rls_dosing_unspecified_update" ON reference."dosing_unspecified_notes"
    FOR UPDATE TO public USING (rls_user_role() = 'super_admin'::text);

GRANT SELECT ON reference."dosing_unspecified_notes" TO anava_app;
REVOKE INSERT, UPDATE, DELETE ON reference."dosing_unspecified_notes" FROM anava_app;
GRANT SELECT ON reference."dosing_unspecified_notes" TO anava_readonly;


-- ###########################################################################
-- 7  Validate
-- ###########################################################################
-- Every constraint above landed NOT VALID (v2 Layer 6: no ACCESS EXCLUSIVE
-- lock + full scan during clinic hours). treatment_protocols is small and was
-- created by 32, so validating immediately is instant. If it has grown by
-- apply time, these seven lines move to a low-traffic window without touching
-- anything above them.

ALTER TABLE core."treatment_protocols" VALIDATE CONSTRAINT "fk_treatment_protocols_authored_in_appointment_id";
ALTER TABLE core."treatment_protocols" VALIDATE CONSTRAINT "chk_treatment_protocols_current_ma";
ALTER TABLE core."treatment_protocols" VALIDATE CONSTRAINT "chk_treatment_protocols_duration_min";
ALTER TABLE core."treatment_protocols" VALIDATE CONSTRAINT "chk_treatment_protocols_ramp";
ALTER TABLE core."treatment_protocols" VALIDATE CONSTRAINT "chk_treatment_protocols_sessions_per_week";
ALTER TABLE core."treatment_protocols" VALIDATE CONSTRAINT "fk_treatment_protocols_supersedes";
ALTER TABLE core."treatment_protocols" VALIDATE CONSTRAINT "chk_treatment_protocols_no_self_supersede";


COMMIT;


-- ###########################################################################
-- SEED NOTE — reference.dosing_unspecified_notes
-- ###########################################################################
-- From the mapping sheet, for the tDCS device:
--
--   Anxiety Disorders   session_duration_min  'Not specified'
--   Anxiety Disorders   num_sessions          'Not specified'
--   Chronic Pain        placement             'Not specified'
--   Chronic Pain        current_ma            'Not specified'
--   Chronic Pain        session_duration_min  'Not specified'
--   Chronic Pain        num_sessions          'Not specified'
--   ADHD                placement             'Varied / Not specified'
--   ADHD                session_duration_min  'Not specified'
--   ADHD                num_sessions          'Not specified'
--   ASD                 placement             'Varied / Not specified'
--   ASD                 session_duration_min  'Not specified'
--   ASD                 num_sessions          'Not specified'
--
-- Not inserted here: the seed needs the real condition_id/device_id UUIDs,
-- and seeding clinical reference data is a separate reviewable step. A wrong
-- montage or dose arriving silently with a schema migration is a clinical
-- safety problem, not a schema problem.
--
--
-- ###########################################################################
-- STILL OPEN AFTER THIS FILE
-- ###########################################################################
--
--   1. Multi-device protocols. The prototype says "Select up to 3 devices"
--      while the same screen says "Phase 1 supports tDCS only". The schema
--      stores one device per protocol, which is correct for Phase 1. Three
--      means either three protocol rows or a child table carrying its own
--      placement/dosing pair — a design decision, not a column to bolt on.
--      (Restated from 38; unchanged.)
--
--   2. appointment_type CHECK still deferred, per 30/31/33. When it lands,
--      'device_session' and 'protocol_followup' must both be in it or the
--      generator breaks.
--
--   3. The backend does not yet write ANY of this: not 38's
--      protocol_conditions / protocol_diagnoses / protocol_scales /
--      protocol_custom_montages, and not the columns added here. The module
--      currently accepts diagnosis_ids and scale cadences in its request
--      bodies and silently discards them. That wiring is the next task and is
--      larger than this migration.
-- ###########################################################################
