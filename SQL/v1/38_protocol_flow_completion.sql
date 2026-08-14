-- 38_protocol_flow_completion.sql
--
-- Hand-written net-new DDL. Closes the gaps between the Treatment Protocol
-- wizard (tDCS Protocol Doctor prototype, 8 steps) and what the schema could
-- actually store.
--
-- SCOPE: four new tables. ADDITIVE ONLY — no existing table, column, constraint
-- or trigger is altered or dropped, so the protocol module keeps working
-- exactly as it does today and adopts these when its code is ready.
--
-- APPLY ORDER: after 32 (needs neuromod_* and treatment_protocols).
--              Independent of 33-37.
--
-- NOT YET EXECUTED ANYWHERE.
--
--
-- ###########################################################################
-- WHAT THE WIZARD DOES THAT THE SCHEMA COULD NOT RECORD
-- ###########################################################################
--
--   Step 2  conditions, one or more, plus an "Other" free-text box
--   Step 3  ICD-10 diagnosis codes, multiple
--   Step 4  Custom Montage — the doctor builds a placement, names it, gives
--           clinical reasoning, saves it, and reuses it later
--   Step 6  assessment scales with a per-scale cadence and window
--
-- Steps 2 and 3 fed the suggestion engine and were then thrown away: nothing
-- recorded what the patient was actually diagnosed with when the protocol was
-- prescribed. For a clinical record that is the wrong default.
--
-- Step 4 could not work at all. reference.tdcs_placements is the curated
-- library — superadmin-owned, and 32 REVOKEs INSERT/UPDATE/DELETE from
-- anava_app deliberately, so an application bug cannot silently alter a
-- validated montage. A doctor-authored montage is a different kind of object
-- with a different owner, and needs its own table rather than a loosened grant.
--
-- NOT ADDRESSED HERE: the prototype's "select up to 3 devices". The schema
-- stores one device per protocol, and the same screen says "Phase 1 supports
-- tDCS only — other devices can be added but are not configurable yet", so one
-- device is the correct Phase 1 behaviour. Supporting three means either three
-- protocol rows or a child table carrying its own placement/dosing pair, which
-- is a design decision for the protocol workstream, not a column to bolt on.


BEGIN;


-- ###########################################################################
-- 1  Custom montages  (Step 4)
-- ###########################################################################
-- Doctor-authored placements, kept strictly separate from the curated library.
-- The UI shows them in their own "Custom Montages" panel for exactly that
-- reason: a montage one doctor invented must never be mistaken for one the
-- catalogue validated.

CREATE TABLE IF NOT EXISTS core."protocol_custom_montages" (
    "custom_montage_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "created_by"         UUID NOT NULL,
    "clinic_id"          UUID,
    "device_id"          UUID NOT NULL,
    "condition_id"       UUID,
    "montage_name"       TEXT NOT NULL,
    "anode_sites"        TEXT[] NOT NULL,
    "cathode_sites"      TEXT[] NOT NULL,
    "description"        TEXT,
    "clinical_reasoning" TEXT NOT NULL,
    "is_active"          BOOLEAN NOT NULL DEFAULT true,
    "created_at"         TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"         TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE core."protocol_custom_montages" IS 'Doctor-authored electrode placements (wizard Step 4, "Custom Montage"). Deliberately NOT in reference.tdcs_placements: that library is curated and superadmin-owned, and 32 revokes application writes to it so a bug cannot alter a validated montage. Retention: clinical reasoning is part of the prescribing record — deactivate, never delete.';
COMMENT ON COLUMN core."protocol_custom_montages"."clinical_reasoning" IS 'Required by the form and required here. A custom montage departs from the validated library, so the reason it was chosen is part of the clinical record, not optional metadata.';
COMMENT ON COLUMN core."protocol_custom_montages"."condition_id" IS 'What the montage was built for. Lets the picker offer a doctor their own montages relevant to the condition in front of them. NULL means general-purpose.';

ALTER TABLE core."protocol_custom_montages" DROP CONSTRAINT IF EXISTS "protocol_custom_montages_pkey" CASCADE;
ALTER TABLE core."protocol_custom_montages" ADD CONSTRAINT "protocol_custom_montages_pkey" PRIMARY KEY ("custom_montage_id");

-- One doctor may not have two montages with the same name — the picker shows
-- names, so duplicates make it impossible to tell them apart.
ALTER TABLE core."protocol_custom_montages" DROP CONSTRAINT IF EXISTS "protocol_custom_montages_created_by_montage_name_key";
ALTER TABLE core."protocol_custom_montages"
    ADD CONSTRAINT "protocol_custom_montages_created_by_montage_name_key"
    UNIQUE ("created_by", "montage_name");

-- The electrode shape both supported modalities agree on: exactly one anode,
-- and between one and four cathodes (tDCS uses 1, an HD-tDCS 4x1 ring up to 4).
-- COALESCE because array_length on an empty array returns NULL, not 0 — a CHECK
-- that evaluates to NULL PASSES, which is exactly the hole that let an
-- anode-with-no-returns montage through in 32 before it was fixed.
ALTER TABLE core."protocol_custom_montages" DROP CONSTRAINT IF EXISTS "chk_pcm_electrode_shape";
ALTER TABLE core."protocol_custom_montages"
    ADD CONSTRAINT "chk_pcm_electrode_shape"
    CHECK (COALESCE(array_length("anode_sites", 1), 0) = 1
           AND COALESCE(array_length("cathode_sites", 1), 0) BETWEEN 1 AND 4);

-- The exact per-modality rule (tDCS must be 1+1, HD-tDCS 1 + up to 4) is
-- checked by POST /neuromod/placements/validate, which already knows the device.
-- This constraint is the shape both share, enforced where it cannot be skipped.

ALTER TABLE core."protocol_custom_montages" DROP CONSTRAINT IF EXISTS "fk_pcm_created_by";
ALTER TABLE core."protocol_custom_montages" ADD CONSTRAINT "fk_pcm_created_by"
    FOREIGN KEY ("created_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."protocol_custom_montages" DROP CONSTRAINT IF EXISTS "fk_pcm_clinic_id";
ALTER TABLE core."protocol_custom_montages" ADD CONSTRAINT "fk_pcm_clinic_id"
    FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."protocol_custom_montages" DROP CONSTRAINT IF EXISTS "fk_pcm_device_id";
ALTER TABLE core."protocol_custom_montages" ADD CONSTRAINT "fk_pcm_device_id"
    FOREIGN KEY ("device_id") REFERENCES reference."neuromod_devices" ("device_id") ON DELETE RESTRICT;
ALTER TABLE core."protocol_custom_montages" DROP CONSTRAINT IF EXISTS "fk_pcm_condition_id";
ALTER TABLE core."protocol_custom_montages" ADD CONSTRAINT "fk_pcm_condition_id"
    FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_pcm_created_by ON core."protocol_custom_montages" USING btree ("created_by") WHERE "is_active";
CREATE INDEX IF NOT EXISTS idx_pcm_device_condition ON core."protocol_custom_montages" USING btree ("device_id", "condition_id") WHERE "is_active";


-- ###########################################################################
-- 2  Protocol scales and cadence  (Step 6)
-- ###########################################################################
-- "Set who answers what and how often — these become patient tasks in the PRS
-- queue." A protocol prescribes assessment, not just stimulation, and the
-- cadence is the part that turns a scale into a schedule of patient work.

CREATE TABLE IF NOT EXISTS core."protocol_scales" (
    "protocol_scale_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "protocol_id"       UUID NOT NULL,
    "scale_id"          UUID NOT NULL,
    "cadence"           TEXT NOT NULL,
    "window_days"       INTEGER,
    "answered_by"       TEXT NOT NULL DEFAULT 'patient',
    "display_order"     INTEGER NOT NULL DEFAULT 0,
    "created_at"        TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE core."protocol_scales" IS 'Which assessment scales this protocol prescribes, how often, and who fills them in (wizard Step 6). Drives the patient PRS queue.';
COMMENT ON COLUMN core."protocol_scales"."cadence" IS 'baseline | per_checkpoint | weekly | fortnightly | end_of_treatment. Text + CHECK rather than an enum, per the project rule: a CHECK can be replaced in one statement, an enum value can never be removed.';
COMMENT ON COLUMN core."protocol_scales"."window_days" IS 'How many days the patient has to complete it once released. NULL = no deadline.';

ALTER TABLE core."protocol_scales" DROP CONSTRAINT IF EXISTS "protocol_scales_pkey" CASCADE;
ALTER TABLE core."protocol_scales" ADD CONSTRAINT "protocol_scales_pkey" PRIMARY KEY ("protocol_scale_id");

-- A scale appears once per protocol. Twice would release the same
-- questionnaire to the patient twice on the same trigger.
ALTER TABLE core."protocol_scales" DROP CONSTRAINT IF EXISTS "protocol_scales_protocol_id_scale_id_key";
ALTER TABLE core."protocol_scales"
    ADD CONSTRAINT "protocol_scales_protocol_id_scale_id_key" UNIQUE ("protocol_id", "scale_id");

ALTER TABLE core."protocol_scales" DROP CONSTRAINT IF EXISTS "chk_protocol_scales_cadence";
ALTER TABLE core."protocol_scales"
    ADD CONSTRAINT "chk_protocol_scales_cadence"
    CHECK ("cadence" IN ('baseline', 'per_checkpoint', 'weekly', 'fortnightly', 'end_of_treatment'));

ALTER TABLE core."protocol_scales" DROP CONSTRAINT IF EXISTS "chk_protocol_scales_answered_by";
ALTER TABLE core."protocol_scales"
    ADD CONSTRAINT "chk_protocol_scales_answered_by"
    CHECK ("answered_by" IN ('patient', 'clinician', 'either'));

ALTER TABLE core."protocol_scales" DROP CONSTRAINT IF EXISTS "chk_protocol_scales_window";
ALTER TABLE core."protocol_scales"
    ADD CONSTRAINT "chk_protocol_scales_window" CHECK ("window_days" IS NULL OR "window_days" > 0);

ALTER TABLE core."protocol_scales" DROP CONSTRAINT IF EXISTS "fk_protocol_scales_protocol_id";
ALTER TABLE core."protocol_scales" ADD CONSTRAINT "fk_protocol_scales_protocol_id"
    FOREIGN KEY ("protocol_id") REFERENCES core."treatment_protocols" ("protocol_id") ON DELETE RESTRICT;
ALTER TABLE core."protocol_scales" DROP CONSTRAINT IF EXISTS "fk_protocol_scales_scale_id";
ALTER TABLE core."protocol_scales" ADD CONSTRAINT "fk_protocol_scales_scale_id"
    FOREIGN KEY ("scale_id") REFERENCES reference."neuromod_scales" ("scale_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_protocol_scales_protocol ON core."protocol_scales" USING btree ("protocol_id");


-- ###########################################################################
-- 3  Conditions and diagnoses recorded on the protocol  (Steps 2 and 3)
-- ###########################################################################
-- Both were selected, used to rank suggestions, and discarded. That leaves the
-- record unable to answer "what was this patient being treated for" — which is
-- the first question anyone reviewing a prescription asks.

CREATE TABLE IF NOT EXISTS core."protocol_conditions" (
    "protocol_condition_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "protocol_id"           UUID NOT NULL,
    "condition_id"          UUID,
    "other_text"            TEXT,
    "created_at"            TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE core."protocol_conditions" IS 'Conditions this protocol treats (wizard Step 2, multi-select). A row carries either a catalogue condition_id or free text from the "Other condition" box — never both, never neither.';

ALTER TABLE core."protocol_conditions" DROP CONSTRAINT IF EXISTS "protocol_conditions_pkey" CASCADE;
ALTER TABLE core."protocol_conditions" ADD CONSTRAINT "protocol_conditions_pkey" PRIMARY KEY ("protocol_condition_id");

-- Exactly one of the two is set. num_nonnulls is the same device 30 used for
-- payments targeting exactly one thing.
ALTER TABLE core."protocol_conditions" DROP CONSTRAINT IF EXISTS "chk_protocol_conditions_shape";
ALTER TABLE core."protocol_conditions"
    ADD CONSTRAINT "chk_protocol_conditions_shape"
    CHECK (num_nonnulls("condition_id", "other_text") = 1);

-- A catalogue condition appears once per protocol. Free-text rows are exempt:
-- the partial index skips them, since two different "other" descriptions are
-- legitimately different conditions.
DROP INDEX IF EXISTS core.uq_protocol_conditions_catalogue;
CREATE UNIQUE INDEX uq_protocol_conditions_catalogue
    ON core."protocol_conditions" ("protocol_id", "condition_id")
    WHERE "condition_id" IS NOT NULL;

ALTER TABLE core."protocol_conditions" DROP CONSTRAINT IF EXISTS "fk_protocol_conditions_protocol_id";
ALTER TABLE core."protocol_conditions" ADD CONSTRAINT "fk_protocol_conditions_protocol_id"
    FOREIGN KEY ("protocol_id") REFERENCES core."treatment_protocols" ("protocol_id") ON DELETE RESTRICT;
ALTER TABLE core."protocol_conditions" DROP CONSTRAINT IF EXISTS "fk_protocol_conditions_condition_id";
ALTER TABLE core."protocol_conditions" ADD CONSTRAINT "fk_protocol_conditions_condition_id"
    FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_protocol_conditions_protocol ON core."protocol_conditions" USING btree ("protocol_id");


CREATE TABLE IF NOT EXISTS core."protocol_diagnoses" (
    "protocol_diagnosis_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "protocol_id"           UUID NOT NULL,
    "diagnosis_id"          UUID NOT NULL,
    "created_at"            TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE core."protocol_diagnoses" IS 'ICD-10 codes selected for this protocol (wizard Step 3, multiple allowed). The clinical justification for the prescription — kept because a protocol without its diagnosis cannot be reviewed later.';

ALTER TABLE core."protocol_diagnoses" DROP CONSTRAINT IF EXISTS "protocol_diagnoses_pkey" CASCADE;
ALTER TABLE core."protocol_diagnoses" ADD CONSTRAINT "protocol_diagnoses_pkey" PRIMARY KEY ("protocol_diagnosis_id");

ALTER TABLE core."protocol_diagnoses" DROP CONSTRAINT IF EXISTS "protocol_diagnoses_protocol_id_diagnosis_id_key";
ALTER TABLE core."protocol_diagnoses"
    ADD CONSTRAINT "protocol_diagnoses_protocol_id_diagnosis_id_key" UNIQUE ("protocol_id", "diagnosis_id");

ALTER TABLE core."protocol_diagnoses" DROP CONSTRAINT IF EXISTS "fk_protocol_diagnoses_protocol_id";
ALTER TABLE core."protocol_diagnoses" ADD CONSTRAINT "fk_protocol_diagnoses_protocol_id"
    FOREIGN KEY ("protocol_id") REFERENCES core."treatment_protocols" ("protocol_id") ON DELETE RESTRICT;
ALTER TABLE core."protocol_diagnoses" DROP CONSTRAINT IF EXISTS "fk_protocol_diagnoses_diagnosis_id";
ALTER TABLE core."protocol_diagnoses" ADD CONSTRAINT "fk_protocol_diagnoses_diagnosis_id"
    FOREIGN KEY ("diagnosis_id") REFERENCES reference."neuromod_diagnoses" ("diagnosis_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_protocol_diagnoses_protocol ON core."protocol_diagnoses" USING btree ("protocol_id");


-- ###########################################################################
-- 4  Triggers
-- ###########################################################################

DROP TRIGGER IF EXISTS trg_updated_at_protocol_custom_montages ON core."protocol_custom_montages";
CREATE TRIGGER trg_updated_at_protocol_custom_montages
    BEFORE UPDATE ON core."protocol_custom_montages"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();

-- Audited: a custom montage and the diagnoses behind a protocol are both part
-- of the prescribing record.
DROP TRIGGER IF EXISTS trg_audit_protocol_custom_montages ON core."protocol_custom_montages";
CREATE TRIGGER trg_audit_protocol_custom_montages
    AFTER INSERT OR DELETE OR UPDATE ON core."protocol_custom_montages"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('custom_montage_id');

DROP TRIGGER IF EXISTS trg_audit_protocol_diagnoses ON core."protocol_diagnoses";
CREATE TRIGGER trg_audit_protocol_diagnoses
    AFTER INSERT OR DELETE OR UPDATE ON core."protocol_diagnoses"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('protocol_diagnosis_id');


-- ###########################################################################
-- 5  RLS
-- ###########################################################################
-- ENABLE + FORCE with policies in the same file (the lesson from 16 and 26).

ALTER TABLE core."protocol_custom_montages" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."protocol_custom_montages" FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."protocol_scales"          ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."protocol_scales"          FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."protocol_conditions"      ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."protocol_conditions"      FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."protocol_diagnoses"       ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."protocol_diagnoses"       FORCE  ROW LEVEL SECURITY;

-- Custom montages: any clinician may READ them (a colleague's montage is
-- reference material, and the CA's read-only view renders it), but only the
-- author may change or retire their own. A montage carries its author's
-- clinical reasoning — editing someone else's would misattribute it.
DROP POLICY IF EXISTS "rls_pcm_select" ON core."protocol_custom_montages";
CREATE POLICY "rls_pcm_select" ON core."protocol_custom_montages" FOR SELECT TO public
    USING (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text,
                                        'doctor'::text, 'clinical_assistant'::text, 'system'::text]));

DROP POLICY IF EXISTS "rls_pcm_insert" ON core."protocol_custom_montages";
CREATE POLICY "rls_pcm_insert" ON core."protocol_custom_montages" FOR INSERT TO public
    WITH CHECK (rls_user_role() = ANY (ARRAY['super_admin'::text, 'doctor'::text])
                AND created_by = rls_user_id());

DROP POLICY IF EXISTS "rls_pcm_update" ON core."protocol_custom_montages";
CREATE POLICY "rls_pcm_update" ON core."protocol_custom_montages" FOR UPDATE TO public
    USING (rls_user_role() = 'super_admin'::text OR created_by = rls_user_id());

-- The three protocol child tables inherit the protocol's own visibility: if you
-- may see the protocol, you may see what it prescribes. Reached through
-- treatment_plans -> treatment_cycles, the same path 32's protocol policy walks.
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['protocol_scales', 'protocol_conditions', 'protocol_diagnoses']
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON core.%I', 'rls_' || t || '_select', t);
        EXECUTE format($p$
            CREATE POLICY %I ON core.%I FOR SELECT TO public USING (
                rls_user_role() = ANY (ARRAY['super_admin','regional_admin','system'])
                OR protocol_id IN (
                    SELECT tp.protocol_id FROM treatment_protocols tp
                    JOIN treatment_plans p  ON p.plan_id  = tp.plan_id
                    JOIN treatment_cycles c ON c.cycle_id = p.cycle_id
                    WHERE p.patient_id = rls_user_id()
                       OR p.doctor_id  = rls_user_id()
                       OR c.clinic_id  = rls_clinic_id()
                       OR c.clinic_id IN (SELECT s.clinic_id FROM clinic_staff_assignments s
                                          WHERE s.profile_id = rls_user_id() AND s.is_active)))
        $p$, 'rls_' || t || '_select', t);

        EXECUTE format('DROP POLICY IF EXISTS %I ON core.%I', 'rls_' || t || '_insert', t);
        EXECUTE format(
            'CREATE POLICY %I ON core.%I FOR INSERT TO public WITH CHECK '
            '(rls_user_role() = ANY (ARRAY[''super_admin'',''clinic_admin'',''doctor'']))',
            'rls_' || t || '_insert', t);

        EXECUTE format('DROP POLICY IF EXISTS %I ON core.%I', 'rls_' || t || '_update', t);
        EXECUTE format(
            'CREATE POLICY %I ON core.%I FOR UPDATE TO public USING '
            '(rls_user_role() = ANY (ARRAY[''super_admin'',''clinic_admin'',''doctor'']))',
            'rls_' || t || '_update', t);

        -- DELETE: a draft protocol's selections are edited by replacement, so
        -- the rows must be removable while it is still a draft.
        EXECUTE format('DROP POLICY IF EXISTS %I ON core.%I', 'rls_' || t || '_delete', t);
        EXECUTE format(
            'CREATE POLICY %I ON core.%I FOR DELETE TO public USING '
            '(rls_user_role() = ANY (ARRAY[''super_admin'',''clinic_admin'',''doctor'']))',
            'rls_' || t || '_delete', t);
    END LOOP;
END
$$;


-- ###########################################################################
-- 6  Grants
-- ###########################################################################

GRANT SELECT, INSERT, UPDATE ON core."protocol_custom_montages" TO anava_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON
    core."protocol_scales", core."protocol_conditions", core."protocol_diagnoses" TO anava_app;

-- A custom montage carries clinical reasoning and is referenced by the
-- protocols built on it — retire with is_active, never delete.
REVOKE DELETE ON core."protocol_custom_montages" FROM anava_app;

GRANT SELECT ON
    core."protocol_custom_montages", core."protocol_scales",
    core."protocol_conditions", core."protocol_diagnoses" TO anava_readonly;

GRANT SELECT ON
    core."protocol_custom_montages", core."protocol_scales",
    core."protocol_conditions", core."protocol_diagnoses" TO anava_compliance;


COMMIT;


-- ###########################################################################
-- OPEN ITEMS
-- ###########################################################################
--
--  1. Multi-device (the wizard's "up to 3") is NOT addressed. One device per
--     protocol remains, which matches "Phase 1 supports tDCS only". Supporting
--     three needs either three protocol rows or a child table carrying its own
--     placement and dosing pair — a protocol-workstream design decision.
--
--  2. "Save as Template" has no table. A template is a protocol shape with no
--     patient, so it is a sibling of treatment_protocols rather than a flag on
--     it. Not built until someone confirms templates are shared across doctors
--     or private to one.
--
--  3. Assistant and patient availability (the Step 7 panel's other two rows)
--     are still not modelled. Clinic hours and device slots come from 36/37.
--
--  4. protocol_scales does not itself create PRS assignments. It records WHAT
--     was prescribed; releasing a questionnaire to a patient at a checkpoint is
--     application work against the existing PRS tables.
