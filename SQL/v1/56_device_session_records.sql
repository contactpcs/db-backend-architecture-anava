-- 56_device_session_records.sql
--
-- Persists what actually happens DURING a tDCS device session — the CA's
-- pre-session safety checklist and everything logged live (impedance,
-- symptoms, adverse events, notes, cognitive activities, scale delivery,
-- patient feedback, media, the session log, patient-raised SOS events).
--
-- SCOPE: nine new tables, all children of one new core.device_sessions
-- header row keyed 1:1 to an appointments row. Nothing on core.appointments,
-- core.protocol_plan, or core.protocol_device_sessions changes.
--
-- APPLY ORDER: after 55. Renumbered from its original "53" during
-- development — that number collided with 53_billable_items_clinic_pricing.sql,
-- an unrelated file already applied to the target DB before this one was
-- merged in. Independent of 48-51's PRS/scale rework except for the two FKs
-- into reference.prs_scales / core.protocol_scales it reads, both already
-- final as of 51. Also independent of 54/55's custom-montage work.
--
-- NOT YET EXECUTED ANYWHERE.
--
--
-- ###########################################################################
-- WHY
-- ###########################################################################
--
-- core.protocol_device_sessions (47) is the doctor's setup record — which
-- ordinal, which unit, what date was prescribed. core.appointments (30) is
-- the calendar spine — status, start_time, who ran it. Neither carries any
-- of the clinical detail a live tDCS session produces: impedance readings,
-- symptoms observed, adverse events, clinical notes, which scales were
-- administered by whom, patient feedback, media consent, or the timestamped
-- audit trail a post-session summary reads from. That data has never had a
-- table. This file adds one.
--
--   core.device_sessions                 the header — 1:1 with an
--                                         appointment, pre-session checklist
--                                         + live session state
--       ├─ core.device_session_symptoms          append-only log
--       ├─ core.device_session_adverse_events    append-only log
--       ├─ core.device_session_notes             append-only log (CA notes,
--       │                                        distinct from
--       │                                        doctor_session_notes —
--       │                                        see NAMING below)
--       ├─ core.device_session_activities        append-only log
--       ├─ core.device_session_scales            one row per scale due this
--       │                                        session, CA-administered vs
--       │                                        sent to the patient app
--       ├─ core.device_session_feedback          filed once
--       ├─ core.device_session_media             attachments, gated by a
--       │                                        recording-consent flag
--       ├─ core.device_session_events            the audit trail — every
--       │                                        state transition, written
--       │                                        only by the service layer
--       └─ core.device_session_sos_events        patient-raised, CA-acked
--
--
-- ###########################################################################
-- WHY NINE TABLES, NOT ONE JSONB BLOB
-- ###########################################################################
--
-- Everything under device_sessions besides the checklist/live-state header is
-- genuinely append-only: a CA logs three symptoms across a session, each at
-- its own moment, each needing its own timestamp and author for the summary
-- screen's audit trail. A JSONB array column on the header row can hold that
-- shape, but every element would inherit the row's own updated_at rather than
-- its own — the moment two symptoms are logged a minute apart, the array
-- can no longer say which happened when without smuggling a timestamp inside
-- each element by hand. This project already has that argument settled:
-- protocol_conditions / protocol_diagnoses / protocol_scales are separate
-- child tables off protocol_plan for exactly this reason (38), not JSONB
-- columns on it. Same reasoning, same shape, applied here.
--
-- Small FIXED-shape substructures stay JSONB on the header row itself
-- (contraindication_checklist, device_fit_checklist, patient_consent,
-- ca_declaration, next_session_confirmation) — each is filed once, as a
-- whole, with one timestamp (the header's own updated_at), so there is no
-- audit granularity to lose by not splitting them out.
--
--
-- ###########################################################################
-- NAMING: device_session_notes vs core.doctor_session_notes
-- ###########################################################################
--
-- core.doctor_session_notes (05_tables_core.sql) already exists, keyed
-- UNIQUE(session_id, doctor_id, session_phase) — it is a DOCTOR's note,
-- one per phase per doctor, not an append-only timeline, and its FK is to
-- core.sessions (the pre-30 concept), not core.appointments directly (30
-- added an appointment_id column onto it but did not repurpose its shape).
-- A clinical assistant's running commentary during a live device session is
-- a different author, a different cardinality (many timestamped entries,
-- not one per phase), and a different owner. Reusing that table would mean
-- either violating its unique constraint or misattributing CA notes as a
-- doctor's. core.device_session_notes is therefore a new table, not a reuse.
--
--
-- ###########################################################################
-- WHAT THIS FILE DELIBERATELY DOES NOT DO
-- ###########################################################################
--
-- It does not add a 'paused' value to appointments.status. That FSM
-- (scheduling/service.py _ALLOWED_FROM) is shared by every appointment type
-- and tightly role-guarded; widening it for a state only device sessions
-- have would ripple into unrelated booking flows. Pause/resume live entirely
-- in device_sessions.session_status. appointments.status is only ever moved
-- by this feature's two coarse transitions — in_progress on start, completed
-- on complete-or-stop-early — through the EXISTING
-- scheduling.AppointmentRepository.update_status, not duplicated here.
--
-- It does not add a per-unit device serial number to core.clinic_devices.
-- clinic_devices tracks a QUANTITY of a device model the clinic owns, not
-- individually serialized units (37) — turning that into per-unit inventory
-- is a separate feature. device_serial_number below is free text, scoped to
-- the session it was typed into, exactly the way the pre-session checklist
-- captures it today.
--
-- It does not touch reference.prs_scales, core.protocol_scales, or
-- core.prs_assessment_instances. device_session_scales POINTS at rows in
-- those tables (protocol_scale_id, prs_instance_id) rather than duplicating
-- their content — the questionnaire engine remains the one place an answer
-- is ever stored.
--
--
-- ###########################################################################
-- ALEMBIC GAP — READ BEFORE WRAPPING THIS FILE
-- ###########################################################################
--
-- alembic's head (backend/alembic/versions/) is 0033, mirroring this
-- directory's file 41. Files 42-52 — including 47's protocol_device_sessions,
-- the table this migration's RLS policies join against — have no alembic
-- wrapper yet. This is a pre-existing gap, not something introduced or fixed
-- here. The wrapper for this file (0034_device_session_records.py) carries an
-- explicit docstring assuming 41-52 are already hand-applied to the target
-- database, the same precedent 0033 itself set for the gap it inherited.
-- Backfilling 0034-0044 for files 42-52 is a separate piece of migration-
-- hygiene work, not a prerequisite this file's own logic depends on.
--
--
BEGIN;


-- ###########################################################################
-- 1  core.device_sessions — the header
-- ###########################################################################

CREATE TABLE IF NOT EXISTS core."device_sessions" (
    "device_session_record_id"     UUID NOT NULL DEFAULT gen_random_uuid(),
    "appointment_id"                UUID NOT NULL,
    "protocol_id"                   UUID NOT NULL,

    -- Pre-session checklist -------------------------------------------------
    "payment_verified"              BOOLEAN NOT NULL DEFAULT FALSE,
    "payment_override_reason"       TEXT,
    "device_brand"                  TEXT,
    "device_serial_number"          TEXT,
    "actual_intensity_ma"           NUMERIC(4,2),
    "intensity_deviates"            BOOLEAN NOT NULL DEFAULT FALSE,
    "intensity_deviation_reason"    TEXT,
    "actual_duration_min"           INTEGER,
    "duration_deviates"             BOOLEAN NOT NULL DEFAULT FALSE,
    "duration_deviation_reason"     TEXT,
    "actual_ramp_up_sec"            INTEGER,
    "ramp_up_deviates"              BOOLEAN NOT NULL DEFAULT FALSE,
    "ramp_up_deviation_reason"      TEXT,
    "actual_ramp_down_sec"          INTEGER,
    "ramp_down_deviates"            BOOLEAN NOT NULL DEFAULT FALSE,
    "ramp_down_deviation_reason"    TEXT,
    "montage_verified"              BOOLEAN NOT NULL DEFAULT FALSE,
    "contraindication_checklist"    JSONB NOT NULL DEFAULT '{}'::jsonb,
    "patient_consent"               JSONB,
    "ca_declaration"                JSONB,

    -- Live session state ------------------------------------------------------
    "session_status"                TEXT NOT NULL DEFAULT 'not_started',
    "device_fit_checklist"          JSONB NOT NULL DEFAULT '{}'::jsonb,
    "impedance_kohm"                NUMERIC(6,2),
    "started_at"                    TIMESTAMPTZ,
    "paused_at"                     TIMESTAMPTZ,
    "resumed_at"                    TIMESTAMPTZ,
    "stopped_at"                    TIMESTAMPTZ,
    "completed_at"                  TIMESTAMPTZ,
    "pause_stop_reason"             TEXT,
    "pause_stop_reason_detail"      TEXT,
    "next_session_confirmation"     JSONB,

    "created_by"                    UUID NOT NULL,
    "created_at"                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"                    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT "chk_device_sessions_status" CHECK ("session_status" IN
        ('not_started', 'in_progress', 'paused', 'completed', 'stopped_early')),
    CONSTRAINT "chk_device_sessions_pause_stop_reason" CHECK ("pause_stop_reason" IS NULL OR "pause_stop_reason" IN
        ('patient_discomfort', 'adverse_event', 'device_setup_issue', 'device_glitch', 'power_outage', 'other'))
);
COMMENT ON TABLE core."device_sessions" IS 'One row per device-session appointment: the CA''s pre-session safety checklist plus live-session state (timer anchors, pause/stop reason, checklist progress). Every append-only detail (symptoms, adverse events, notes, activities, scale delivery, feedback, media, the audit log, SOS events) lives in a child table keyed on device_session_record_id. Retention: clinical, Bucket 2 — anonymise with the patient profile, never hard-delete.';
COMMENT ON COLUMN core."device_sessions"."protocol_id" IS 'Denormalised from the appointment (core.appointments.protocol_id), same pattern protocol_device_sessions (47) already uses — lets this row be read without joining appointments for the common case.';
COMMENT ON COLUMN core."device_sessions"."payment_override_reason" IS 'Set only when the CA proceeds despite an unpaid/pending appointment. Required by the UI whenever payment_verified is left FALSE and the CA starts anyway; not enforced here as a CHECK because "proceed without payment" is a judgment call the checklist records, not a rule the schema should block.';
COMMENT ON COLUMN core."device_sessions"."device_serial_number" IS 'Free-text unit identifier typed at session time. No backing catalogue column exists (core.clinic_devices tracks quantity owned, not per-unit serials) — this is a session-scoped note, not inventory data.';
COMMENT ON COLUMN core."device_sessions"."actual_ramp_up_sec" IS 'Actual ramp-up used this session, compared against the single protocol_plan.ramp_seconds (prescribed) for the *_deviates flag. The prescription carries one symmetric ramp value; execution can differ up vs down, hence two actual columns against one prescribed number.';
COMMENT ON COLUMN core."device_sessions"."contraindication_checklist" IS 'Fixed 7-item CA safety checklist (skull injury/fracture, implanted metal/electronic devices, skin lesions at site, seizure history change, medication change, jewellery/hair prep, patient alert/hydrated/fed/consents), each a boolean keyed by a stable item code. Filed once as a whole, so no per-item timestamp is needed — device_sessions.updated_at covers it.';
COMMENT ON COLUMN core."device_sessions"."patient_consent" IS 'Shape: {"statements": [{"code": str, "confirmed": bool}, ...], "signature": str, "signed_at": timestamptz}. Five fixed statements per the pre-session UI; the signature is a captured signature-pad payload, not a cryptographic signature.';
COMMENT ON COLUMN core."device_sessions"."ca_declaration" IS 'Same shape as patient_consent, for the CA''s own 4-statement declaration (checklist completed, scalp inspected at both sites, montage verified, will monitor and stop on adverse event).';
COMMENT ON COLUMN core."device_sessions"."session_status" IS 'not_started -> in_progress -> (paused <-> in_progress) -> completed | stopped_early. Deliberately separate from appointments.status — see file header WHAT THIS FILE DELIBERATELY DOES NOT DO.';
COMMENT ON COLUMN core."device_sessions"."device_fit_checklist" IS 'Fixed 6-item live-session checklist (sponges saline-soaked, anode/cathode placement measured against montage map, headstrap/hair secured, cable routing checked, impedance checked pre-ramp, patient briefed to report pain), booleans keyed by item code.';
COMMENT ON COLUMN core."device_sessions"."impedance_kohm" IS 'Most recent impedance reading in kOhm. UI bands: <5 good, 5-10 acceptable, >10 too high (target is under 10).';
COMMENT ON COLUMN core."device_sessions"."pause_stop_reason" IS 'Reason for the most recent pause or the terminal stop. Fixed vocabulary shared by both actions (the UI''s Pause/Stop dialog is one shared component).';
COMMENT ON COLUMN core."device_sessions"."next_session_confirmation" IS 'Shape: {"patient_confirmed": bool, "requested_date": date|null, "requested_slot": str|null, "note": str|null}. A soft confirmation only — an actual reschedule still goes through the normal appointments booking path; this just records what the patient asked for so the receptionist can action it.';

ALTER TABLE core."device_sessions" DROP CONSTRAINT IF EXISTS "device_sessions_pkey" CASCADE;
ALTER TABLE core."device_sessions" ADD CONSTRAINT "device_sessions_pkey" PRIMARY KEY ("device_session_record_id");

ALTER TABLE core."device_sessions" DROP CONSTRAINT IF EXISTS "uq_device_sessions_appointment";
ALTER TABLE core."device_sessions" ADD CONSTRAINT "uq_device_sessions_appointment" UNIQUE ("appointment_id");

ALTER TABLE core."device_sessions" DROP CONSTRAINT IF EXISTS "fk_device_sessions_appointment_id";
ALTER TABLE core."device_sessions" ADD CONSTRAINT "fk_device_sessions_appointment_id"
    FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT;

ALTER TABLE core."device_sessions" DROP CONSTRAINT IF EXISTS "fk_device_sessions_protocol_id";
ALTER TABLE core."device_sessions" ADD CONSTRAINT "fk_device_sessions_protocol_id"
    FOREIGN KEY ("protocol_id") REFERENCES core."protocol_plan" ("protocol_id") ON DELETE RESTRICT;

ALTER TABLE core."device_sessions" DROP CONSTRAINT IF EXISTS "fk_device_sessions_created_by";
ALTER TABLE core."device_sessions" ADD CONSTRAINT "fk_device_sessions_created_by"
    FOREIGN KEY ("created_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_device_sessions_protocol ON core."device_sessions" USING btree ("protocol_id");

DROP TRIGGER IF EXISTS trg_device_sessions_updated_at ON core."device_sessions";
CREATE TRIGGER trg_device_sessions_updated_at
    BEFORE UPDATE ON core."device_sessions"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();

DROP TRIGGER IF EXISTS trg_audit_device_sessions ON core."device_sessions";
CREATE TRIGGER trg_audit_device_sessions
    AFTER INSERT OR DELETE OR UPDATE ON core."device_sessions"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('device_session_record_id');


-- ###########################################################################
-- 2  core.device_session_symptoms
-- ###########################################################################

CREATE TABLE IF NOT EXISTS core."device_session_symptoms" (
    "symptom_record_id"        UUID NOT NULL DEFAULT gen_random_uuid(),
    "device_session_record_id" UUID NOT NULL,
    "symptom"                  TEXT NOT NULL,
    "severity"                 TEXT NOT NULL,
    "note"                     TEXT,
    "recorded_by"              UUID NOT NULL,
    "recorded_at"              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_dss_symptom" CHECK ("symptom" IN
        ('tingling', 'itching', 'burning', 'headache', 'fatigue', 'sleepiness', 'dizziness', 'skin_redness', 'nausea', 'other')),
    CONSTRAINT "chk_dss_severity" CHECK ("severity" IN ('mild', 'moderate', 'severe'))
);
COMMENT ON TABLE core."device_session_symptoms" IS 'Append-only log of symptoms observed during a live session. One row per symptom logged, each with its own timestamp and author for the session audit trail.';

ALTER TABLE core."device_session_symptoms" DROP CONSTRAINT IF EXISTS "device_session_symptoms_pkey" CASCADE;
ALTER TABLE core."device_session_symptoms" ADD CONSTRAINT "device_session_symptoms_pkey" PRIMARY KEY ("symptom_record_id");

ALTER TABLE core."device_session_symptoms" DROP CONSTRAINT IF EXISTS "fk_dss_device_session";
ALTER TABLE core."device_session_symptoms" ADD CONSTRAINT "fk_dss_device_session"
    FOREIGN KEY ("device_session_record_id") REFERENCES core."device_sessions" ("device_session_record_id") ON DELETE RESTRICT;

ALTER TABLE core."device_session_symptoms" DROP CONSTRAINT IF EXISTS "fk_dss_recorded_by";
ALTER TABLE core."device_session_symptoms" ADD CONSTRAINT "fk_dss_recorded_by"
    FOREIGN KEY ("recorded_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_dss_device_session ON core."device_session_symptoms" USING btree ("device_session_record_id");


-- ###########################################################################
-- 3  core.device_session_adverse_events
-- ###########################################################################

CREATE TABLE IF NOT EXISTS core."device_session_adverse_events" (
    "ae_record_id"             UUID NOT NULL DEFAULT gen_random_uuid(),
    "device_session_record_id" UUID NOT NULL,
    "event_type"               TEXT NOT NULL,
    "severity"                 TEXT NOT NULL,
    "description"              TEXT NOT NULL,
    "action_taken"             TEXT,
    "recorded_by"              UUID NOT NULL,
    "recorded_at"              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_dsae_event_type" CHECK ("event_type" IN
        ('sharp_burning_pain', 'skin_burn_lesion', 'dizziness', 'severe_headache', 'nausea_vomiting', 'other')),
    CONSTRAINT "chk_dsae_severity" CHECK ("severity" IN ('mild', 'moderate', 'severe'))
);
COMMENT ON TABLE core."device_session_adverse_events" IS 'Append-only log of adverse events during a live session. A non-mild row here is the UI''s cue to consider pausing/stopping and notifying the doctor — enforced in the application layer, not the schema.';

ALTER TABLE core."device_session_adverse_events" DROP CONSTRAINT IF EXISTS "device_session_adverse_events_pkey" CASCADE;
ALTER TABLE core."device_session_adverse_events" ADD CONSTRAINT "device_session_adverse_events_pkey" PRIMARY KEY ("ae_record_id");

ALTER TABLE core."device_session_adverse_events" DROP CONSTRAINT IF EXISTS "fk_dsae_device_session";
ALTER TABLE core."device_session_adverse_events" ADD CONSTRAINT "fk_dsae_device_session"
    FOREIGN KEY ("device_session_record_id") REFERENCES core."device_sessions" ("device_session_record_id") ON DELETE RESTRICT;

ALTER TABLE core."device_session_adverse_events" DROP CONSTRAINT IF EXISTS "fk_dsae_recorded_by";
ALTER TABLE core."device_session_adverse_events" ADD CONSTRAINT "fk_dsae_recorded_by"
    FOREIGN KEY ("recorded_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_dsae_device_session ON core."device_session_adverse_events" USING btree ("device_session_record_id");


-- ###########################################################################
-- 4  core.device_session_notes
-- ###########################################################################
-- See file header NAMING section for why this is not doctor_session_notes.

CREATE TABLE IF NOT EXISTS core."device_session_notes" (
    "note_id"                  UUID NOT NULL DEFAULT gen_random_uuid(),
    "device_session_record_id" UUID NOT NULL,
    "note_text"                TEXT NOT NULL,
    "recorded_by"              UUID NOT NULL,
    "recorded_at"              TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE core."device_session_notes" IS 'Append-only CA clinical-notes timeline for a live device session. Distinct from core.doctor_session_notes (a doctor-authored, one-per-phase record on core.sessions) — see 53''s file header.';

ALTER TABLE core."device_session_notes" DROP CONSTRAINT IF EXISTS "device_session_notes_pkey" CASCADE;
ALTER TABLE core."device_session_notes" ADD CONSTRAINT "device_session_notes_pkey" PRIMARY KEY ("note_id");

ALTER TABLE core."device_session_notes" DROP CONSTRAINT IF EXISTS "fk_dsn_device_session";
ALTER TABLE core."device_session_notes" ADD CONSTRAINT "fk_dsn_device_session"
    FOREIGN KEY ("device_session_record_id") REFERENCES core."device_sessions" ("device_session_record_id") ON DELETE RESTRICT;

ALTER TABLE core."device_session_notes" DROP CONSTRAINT IF EXISTS "fk_dsn_recorded_by";
ALTER TABLE core."device_session_notes" ADD CONSTRAINT "fk_dsn_recorded_by"
    FOREIGN KEY ("recorded_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_dsn_device_session ON core."device_session_notes" USING btree ("device_session_record_id");


-- ###########################################################################
-- 5  core.device_session_activities
-- ###########################################################################

CREATE TABLE IF NOT EXISTS core."device_session_activities" (
    "activity_record_id"       UUID NOT NULL DEFAULT gen_random_uuid(),
    "device_session_record_id" UUID NOT NULL,
    "activities"                TEXT[] NOT NULL,
    "free_text"                 TEXT,
    "note"                      TEXT,
    "recorded_by"                UUID NOT NULL,
    "recorded_at"                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_dsa_activities_not_empty" CHECK (array_length("activities", 1) > 0)
);
COMMENT ON TABLE core."device_session_activities" IS 'Append-only log of cognitive activities the patient did during a session (sudoku, memory game, word recall, reading aloud, breathing exercise, sit-to-stand, drawing — plus free text for anything off the fixed list).';

ALTER TABLE core."device_session_activities" DROP CONSTRAINT IF EXISTS "device_session_activities_pkey" CASCADE;
ALTER TABLE core."device_session_activities" ADD CONSTRAINT "device_session_activities_pkey" PRIMARY KEY ("activity_record_id");

ALTER TABLE core."device_session_activities" DROP CONSTRAINT IF EXISTS "fk_dsa_device_session";
ALTER TABLE core."device_session_activities" ADD CONSTRAINT "fk_dsa_device_session"
    FOREIGN KEY ("device_session_record_id") REFERENCES core."device_sessions" ("device_session_record_id") ON DELETE RESTRICT;

ALTER TABLE core."device_session_activities" DROP CONSTRAINT IF EXISTS "fk_dsa_recorded_by";
ALTER TABLE core."device_session_activities" ADD CONSTRAINT "fk_dsa_recorded_by"
    FOREIGN KEY ("recorded_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_dsa_device_session ON core."device_session_activities" USING btree ("device_session_record_id");


-- ###########################################################################
-- 6  core.device_session_scales
-- ###########################################################################
-- Pointer/status per scale due this session — never stores an answer itself.
-- prs_instance_id is TEXT because core.prs_assessment_instances.instance_id
-- is TEXT (05_tables_core.sql), not UUID like every other id in this file.

CREATE TABLE IF NOT EXISTS core."device_session_scales" (
    "session_scale_id"         UUID NOT NULL DEFAULT gen_random_uuid(),
    "device_session_record_id" UUID NOT NULL,
    "protocol_scale_id"        UUID NOT NULL,
    "delivery_mode"            TEXT NOT NULL,
    "prs_instance_id"          TEXT,
    "status"                   TEXT NOT NULL DEFAULT 'pending',
    "created_at"               TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_dss2_delivery_mode" CHECK ("delivery_mode" IN ('ca_administered', 'patient_app')),
    CONSTRAINT "chk_dss2_status" CHECK ("status" IN ('pending', 'in_progress', 'completed'))
);
COMMENT ON TABLE core."device_session_scales" IS 'One row per (session, protocol_scale) due this visit. delivery_mode records whether the CA administered it inline or sent it to the patient app — mutually exclusive per scale, enforced in the application layer since either path is valid. Points at reference.prs_scales (via protocol_scale_id -> core.protocol_scales) and core.prs_assessment_instances (via prs_instance_id) rather than storing an answer — the questionnaire engine remains the one place a response lives.';
COMMENT ON COLUMN core."device_session_scales"."prs_instance_id" IS 'Set once the scale has actually been released/answered (core.prs_assessment_instances.instance_id, TEXT — that table predates the UUID convention used everywhere else in this schema). NULL while status = pending.';

ALTER TABLE core."device_session_scales" DROP CONSTRAINT IF EXISTS "device_session_scales_pkey" CASCADE;
ALTER TABLE core."device_session_scales" ADD CONSTRAINT "device_session_scales_pkey" PRIMARY KEY ("session_scale_id");

ALTER TABLE core."device_session_scales" DROP CONSTRAINT IF EXISTS "uq_dss2_session_scale";
ALTER TABLE core."device_session_scales" ADD CONSTRAINT "uq_dss2_session_scale" UNIQUE ("device_session_record_id", "protocol_scale_id");

ALTER TABLE core."device_session_scales" DROP CONSTRAINT IF EXISTS "fk_dss2_device_session";
ALTER TABLE core."device_session_scales" ADD CONSTRAINT "fk_dss2_device_session"
    FOREIGN KEY ("device_session_record_id") REFERENCES core."device_sessions" ("device_session_record_id") ON DELETE RESTRICT;

ALTER TABLE core."device_session_scales" DROP CONSTRAINT IF EXISTS "fk_dss2_protocol_scale";
ALTER TABLE core."device_session_scales" ADD CONSTRAINT "fk_dss2_protocol_scale"
    FOREIGN KEY ("protocol_scale_id") REFERENCES core."protocol_scales" ("protocol_scale_id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_dss2_device_session ON core."device_session_scales" USING btree ("device_session_record_id");

DROP TRIGGER IF EXISTS trg_device_session_scales_updated_at ON core."device_session_scales";
CREATE TRIGGER trg_device_session_scales_updated_at
    BEFORE UPDATE ON core."device_session_scales"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();


-- ###########################################################################
-- 7  core.device_session_feedback
-- ###########################################################################

CREATE TABLE IF NOT EXISTS core."device_session_feedback" (
    "feedback_id"              UUID NOT NULL DEFAULT gen_random_uuid(),
    "device_session_record_id" UUID NOT NULL,
    "answers"                  JSONB NOT NULL,
    "quote"                    TEXT,
    "recorded_by"              UUID NOT NULL,
    "recorded_at"              TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE core."device_session_feedback" IS 'Filed once per session. answers shape: {"comfort": "comfortable"|"tolerable"|"uncomfortable", "felt_after": "better"|"no_change"|"worse", "next_intensity": "decrease"|"keep_same"|"increase"}. Feeds the next session''s intensity recommendation.';

ALTER TABLE core."device_session_feedback" DROP CONSTRAINT IF EXISTS "device_session_feedback_pkey" CASCADE;
ALTER TABLE core."device_session_feedback" ADD CONSTRAINT "device_session_feedback_pkey" PRIMARY KEY ("feedback_id");

ALTER TABLE core."device_session_feedback" DROP CONSTRAINT IF EXISTS "uq_dsf_device_session";
ALTER TABLE core."device_session_feedback" ADD CONSTRAINT "uq_dsf_device_session" UNIQUE ("device_session_record_id");

ALTER TABLE core."device_session_feedback" DROP CONSTRAINT IF EXISTS "fk_dsf_device_session";
ALTER TABLE core."device_session_feedback" ADD CONSTRAINT "fk_dsf_device_session"
    FOREIGN KEY ("device_session_record_id") REFERENCES core."device_sessions" ("device_session_record_id") ON DELETE RESTRICT;

ALTER TABLE core."device_session_feedback" DROP CONSTRAINT IF EXISTS "fk_dsf_recorded_by";
ALTER TABLE core."device_session_feedback" ADD CONSTRAINT "fk_dsf_recorded_by"
    FOREIGN KEY ("recorded_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;


-- ###########################################################################
-- 8  core.device_session_media
-- ###########################################################################

CREATE TABLE IF NOT EXISTS core."device_session_media" (
    "media_id"                        UUID NOT NULL DEFAULT gen_random_uuid(),
    "device_session_record_id"        UUID NOT NULL,
    "recording_consent_confirmed"     BOOLEAN NOT NULL,
    "media_type"                      TEXT NOT NULL,
    "file_key"                        TEXT NOT NULL,
    "captured_at"                     TIMESTAMPTZ NOT NULL DEFAULT now(),
    "uploaded_by"                     UUID NOT NULL,
    CONSTRAINT "chk_dsm_media_type" CHECK ("media_type" IN ('photo', 'video')),
    CONSTRAINT "chk_dsm_consent_required" CHECK ("recording_consent_confirmed" = TRUE)
);
COMMENT ON TABLE core."device_session_media" IS 'Session media attachments. file_key points into the existing files module''s storage (app/modules/files/) — this table is the pointer + consent gate, not the blob store. chk_dsm_consent_required makes the gate a hard constraint, not just a UI check: no row can exist without recording_consent_confirmed = TRUE.';
COMMENT ON COLUMN core."device_session_media"."recording_consent_confirmed" IS 'Must be TRUE — see chk_dsm_consent_required. The consent itself is confirmed once per session via the service layer before the first media row is ever inserted.';

ALTER TABLE core."device_session_media" DROP CONSTRAINT IF EXISTS "device_session_media_pkey" CASCADE;
ALTER TABLE core."device_session_media" ADD CONSTRAINT "device_session_media_pkey" PRIMARY KEY ("media_id");

ALTER TABLE core."device_session_media" DROP CONSTRAINT IF EXISTS "fk_dsm_device_session";
ALTER TABLE core."device_session_media" ADD CONSTRAINT "fk_dsm_device_session"
    FOREIGN KEY ("device_session_record_id") REFERENCES core."device_sessions" ("device_session_record_id") ON DELETE RESTRICT;

ALTER TABLE core."device_session_media" DROP CONSTRAINT IF EXISTS "fk_dsm_uploaded_by";
ALTER TABLE core."device_session_media" ADD CONSTRAINT "fk_dsm_uploaded_by"
    FOREIGN KEY ("uploaded_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_dsm_device_session ON core."device_session_media" USING btree ("device_session_record_id");


-- ###########################################################################
-- 9  core.device_session_events — the audit trail
-- ###########################################################################
-- Written only by the service layer (never a direct client-supplied row) —
-- one entry per state transition or logged action, so the post-session
-- summary screen reads a ready-made timeline instead of reconstructing one
-- by unioning every other child table.

CREATE TABLE IF NOT EXISTS core."device_session_events" (
    "event_id"                  UUID NOT NULL DEFAULT gen_random_uuid(),
    "device_session_record_id"  UUID NOT NULL,
    "event_type"                TEXT NOT NULL,
    "payload"                   JSONB NOT NULL DEFAULT '{}'::jsonb,
    "actor_id"                  UUID NOT NULL,
    "actor_role"                TEXT NOT NULL,
    "occurred_at"                TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE core."device_session_events" IS 'Append-only, service-layer-written audit trail for one device session. event_type values include started/paused/resumed/stopped/completed/ae_recorded/symptom_recorded/note_added/... The Session Summary screen reads this table directly rather than reconstructing a timeline from the other child tables.';

ALTER TABLE core."device_session_events" DROP CONSTRAINT IF EXISTS "device_session_events_pkey" CASCADE;
ALTER TABLE core."device_session_events" ADD CONSTRAINT "device_session_events_pkey" PRIMARY KEY ("event_id");

ALTER TABLE core."device_session_events" DROP CONSTRAINT IF EXISTS "fk_dse_device_session";
ALTER TABLE core."device_session_events" ADD CONSTRAINT "fk_dse_device_session"
    FOREIGN KEY ("device_session_record_id") REFERENCES core."device_sessions" ("device_session_record_id") ON DELETE RESTRICT;

ALTER TABLE core."device_session_events" DROP CONSTRAINT IF EXISTS "fk_dse_actor_id";
ALTER TABLE core."device_session_events" ADD CONSTRAINT "fk_dse_actor_id"
    FOREIGN KEY ("actor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_dse_device_session ON core."device_session_events" USING btree ("device_session_record_id", "occurred_at");


-- ###########################################################################
-- 10  core.device_session_sos_events
-- ###########################################################################
-- Patient-initiated, CA-acknowledged. Kept separate from device_session_events
-- (rather than one more event_type on that table) because the actor path is
-- different — a patient writes this one, not staff — and it needs its own
-- read/ack lifecycle (acknowledged_by/acknowledged_at) that no other event
-- type on that table needs.

CREATE TABLE IF NOT EXISTS core."device_session_sos_events" (
    "sos_id"                    UUID NOT NULL DEFAULT gen_random_uuid(),
    "device_session_record_id"  UUID NOT NULL,
    "sos_type"                  TEXT NOT NULL,
    "note"                      TEXT,
    "raised_by"                  UUID NOT NULL,
    "raised_at"                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    "acknowledged_by"             UUID,
    "acknowledged_at"              TIMESTAMPTZ,
    CONSTRAINT "chk_dsse_sos_type" CHECK ("sos_type" IN ('discomfort', 'unwell', 'other', 'emergency')),
    CONSTRAINT "chk_dsse_ack_pair" CHECK (("acknowledged_by" IS NULL) = ("acknowledged_at" IS NULL))
);
COMMENT ON TABLE core."device_session_sos_events" IS 'Patient-raised help requests during a live session (the patient app''s SOS button). emergency is the visually-distinct, whole-care-team-alerting variant; the other three route to the assigned CA.';

ALTER TABLE core."device_session_sos_events" DROP CONSTRAINT IF EXISTS "device_session_sos_events_pkey" CASCADE;
ALTER TABLE core."device_session_sos_events" ADD CONSTRAINT "device_session_sos_events_pkey" PRIMARY KEY ("sos_id");

ALTER TABLE core."device_session_sos_events" DROP CONSTRAINT IF EXISTS "fk_dsse_device_session";
ALTER TABLE core."device_session_sos_events" ADD CONSTRAINT "fk_dsse_device_session"
    FOREIGN KEY ("device_session_record_id") REFERENCES core."device_sessions" ("device_session_record_id") ON DELETE RESTRICT;

ALTER TABLE core."device_session_sos_events" DROP CONSTRAINT IF EXISTS "fk_dsse_raised_by";
ALTER TABLE core."device_session_sos_events" ADD CONSTRAINT "fk_dsse_raised_by"
    FOREIGN KEY ("raised_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

ALTER TABLE core."device_session_sos_events" DROP CONSTRAINT IF EXISTS "fk_dsse_acknowledged_by";
ALTER TABLE core."device_session_sos_events" ADD CONSTRAINT "fk_dsse_acknowledged_by"
    FOREIGN KEY ("acknowledged_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_dsse_device_session ON core."device_session_sos_events" USING btree ("device_session_record_id");
-- The CA queue's "open SOS" panel filters on this; partial index keeps it
-- cheap regardless of how many acknowledged events accumulate over time.
CREATE INDEX IF NOT EXISTS idx_dsse_unacked ON core."device_session_sos_events" USING btree ("device_session_record_id") WHERE "acknowledged_at" IS NULL;


-- ###########################################################################
-- 11  RLS
-- ###########################################################################
-- Same shape as 47's protocol_device_sessions/protocol_followup policies:
-- reachable through the appointment (clinic staff, assigned doctor/CA, the
-- patient it belongs to) or as clinic staff generally. Every child table
-- reaches the appointment by joining back through device_sessions rather
-- than duplicating appointment_id onto each one.

ALTER TABLE core."device_sessions"              ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."device_sessions"              FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."device_session_symptoms"       ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."device_session_symptoms"       FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."device_session_adverse_events" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."device_session_adverse_events" FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."device_session_notes"          ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."device_session_notes"          FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."device_session_activities"     ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."device_session_activities"     FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."device_session_scales"         ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."device_session_scales"         FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."device_session_feedback"       ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."device_session_feedback"       FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."device_session_media"          ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."device_session_media"          FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."device_session_events"         ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."device_session_events"         FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."device_session_sos_events"     ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."device_session_sos_events"     FORCE  ROW LEVEL SECURITY;

-- device_sessions: SELECT reachable via the appointment; INSERT/UPDATE is a
-- CA-or-above action (never the patient — the CA runs the checklist and the
-- live session, the patient never writes this header row directly).
DROP POLICY IF EXISTS "rls_device_sessions_select" ON core."device_sessions";
CREATE POLICY "rls_device_sessions_select" ON core."device_sessions" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR (appointment_id IN (
            SELECT a.appointment_id FROM appointments a
            WHERE a.patient_id = rls_user_id()
               OR a.clinic_id  = rls_clinic_id()
               OR a.doctor_id  = rls_user_id()
               OR a.ca_id      = rls_user_id()))
        OR (appointment_id IN (
            SELECT a.appointment_id FROM appointments a
            JOIN clinic_staff_assignments s ON s.clinic_id = a.clinic_id
            WHERE s.profile_id = rls_user_id() AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_device_sessions_insert" ON core."device_sessions";
CREATE POLICY "rls_device_sessions_insert" ON core."device_sessions" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'system'::text])
    );

DROP POLICY IF EXISTS "rls_device_sessions_update" ON core."device_sessions";
CREATE POLICY "rls_device_sessions_update" ON core."device_sessions" FOR UPDATE TO public
    USING (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'system'::text])
    );

-- Reusable pattern for every child table below: reachable through
-- device_session_record_id -> device_sessions.appointment_id -> appointments,
-- same three-way OR as above. Only device_session_scales, device_session_
-- feedback and device_session_sos_events additionally allow role='patient'
-- to write their own row, per the file header's scoping.

DO $$
DECLARE
    child TEXT;
    children TEXT[] := ARRAY[
        'device_session_symptoms', 'device_session_adverse_events', 'device_session_notes',
        'device_session_activities', 'device_session_media', 'device_session_events'
    ];
BEGIN
    FOREACH child IN ARRAY children LOOP
        EXECUTE format(
            'DROP POLICY IF EXISTS %I ON core.%I', 'rls_' || child || '_select', child
        );
        EXECUTE format($f$
            CREATE POLICY %I ON core.%I FOR SELECT TO public
            USING (
                (rls_user_role() = ANY (ARRAY['super_admin', 'regional_admin', 'system']))
                OR (device_session_record_id IN (
                    SELECT ds.device_session_record_id FROM device_sessions ds
                    JOIN appointments a ON a.appointment_id = ds.appointment_id
                    WHERE a.patient_id = rls_user_id()
                       OR a.clinic_id  = rls_clinic_id()
                       OR a.doctor_id  = rls_user_id()
                       OR a.ca_id      = rls_user_id()))
                OR (device_session_record_id IN (
                    SELECT ds.device_session_record_id FROM device_sessions ds
                    JOIN appointments a ON a.appointment_id = ds.appointment_id
                    JOIN clinic_staff_assignments s ON s.clinic_id = a.clinic_id
                    WHERE s.profile_id = rls_user_id() AND s.is_active))
            )
        $f$, 'rls_' || child || '_select', child);

        EXECUTE format(
            'DROP POLICY IF EXISTS %I ON core.%I', 'rls_' || child || '_insert', child
        );
        EXECUTE format($f$
            CREATE POLICY %I ON core.%I FOR INSERT TO public
            WITH CHECK (
                rls_user_role() = ANY (ARRAY['super_admin', 'clinic_admin', 'clinical_assistant', 'system'])
            )
        $f$, 'rls_' || child || '_insert', child);
    END LOOP;
END $$;

-- device_session_scales: CA/admin write (delivery mode, status), plus the
-- patient may update their own row's status (answering on the patient app
-- moves patient_app rows to in_progress/completed) but never insert one —
-- rows are seeded by the CA/service layer from the protocol's scale list.
DROP POLICY IF EXISTS "rls_device_session_scales_select" ON core."device_session_scales";
CREATE POLICY "rls_device_session_scales_select" ON core."device_session_scales" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR (device_session_record_id IN (
            SELECT ds.device_session_record_id FROM device_sessions ds
            JOIN appointments a ON a.appointment_id = ds.appointment_id
            WHERE a.patient_id = rls_user_id()
               OR a.clinic_id  = rls_clinic_id()
               OR a.doctor_id  = rls_user_id()
               OR a.ca_id      = rls_user_id()))
        OR (device_session_record_id IN (
            SELECT ds.device_session_record_id FROM device_sessions ds
            JOIN appointments a ON a.appointment_id = ds.appointment_id
            JOIN clinic_staff_assignments s ON s.clinic_id = a.clinic_id
            WHERE s.profile_id = rls_user_id() AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_device_session_scales_insert" ON core."device_session_scales";
CREATE POLICY "rls_device_session_scales_insert" ON core."device_session_scales" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'system'::text])
    );

DROP POLICY IF EXISTS "rls_device_session_scales_update" ON core."device_session_scales";
CREATE POLICY "rls_device_session_scales_update" ON core."device_session_scales" FOR UPDATE TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'system'::text]))
        OR (device_session_record_id IN (
            SELECT ds.device_session_record_id FROM device_sessions ds
            JOIN appointments a ON a.appointment_id = ds.appointment_id
            WHERE a.patient_id = rls_user_id()))
    );

-- device_session_feedback: CA/admin write on the patient's behalf during the
-- session, or the patient files it themselves from the patient app.
DROP POLICY IF EXISTS "rls_device_session_feedback_select" ON core."device_session_feedback";
CREATE POLICY "rls_device_session_feedback_select" ON core."device_session_feedback" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR (device_session_record_id IN (
            SELECT ds.device_session_record_id FROM device_sessions ds
            JOIN appointments a ON a.appointment_id = ds.appointment_id
            WHERE a.patient_id = rls_user_id()
               OR a.clinic_id  = rls_clinic_id()
               OR a.doctor_id  = rls_user_id()
               OR a.ca_id      = rls_user_id()))
        OR (device_session_record_id IN (
            SELECT ds.device_session_record_id FROM device_sessions ds
            JOIN appointments a ON a.appointment_id = ds.appointment_id
            JOIN clinic_staff_assignments s ON s.clinic_id = a.clinic_id
            WHERE s.profile_id = rls_user_id() AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_device_session_feedback_insert" ON core."device_session_feedback";
CREATE POLICY "rls_device_session_feedback_insert" ON core."device_session_feedback" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'system'::text]))
        OR (device_session_record_id IN (
            SELECT ds.device_session_record_id FROM device_sessions ds
            JOIN appointments a ON a.appointment_id = ds.appointment_id
            WHERE a.patient_id = rls_user_id()))
    );

-- device_session_sos_events: only the patient raises one (for their own
-- session); CA/admin acknowledge via UPDATE.
DROP POLICY IF EXISTS "rls_device_session_sos_events_select" ON core."device_session_sos_events";
CREATE POLICY "rls_device_session_sos_events_select" ON core."device_session_sos_events" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR (device_session_record_id IN (
            SELECT ds.device_session_record_id FROM device_sessions ds
            JOIN appointments a ON a.appointment_id = ds.appointment_id
            WHERE a.patient_id = rls_user_id()
               OR a.clinic_id  = rls_clinic_id()
               OR a.doctor_id  = rls_user_id()
               OR a.ca_id      = rls_user_id()))
        OR (device_session_record_id IN (
            SELECT ds.device_session_record_id FROM device_sessions ds
            JOIN appointments a ON a.appointment_id = ds.appointment_id
            JOIN clinic_staff_assignments s ON s.clinic_id = a.clinic_id
            WHERE s.profile_id = rls_user_id() AND s.is_active))
    );

DROP POLICY IF EXISTS "rls_device_session_sos_events_insert" ON core."device_session_sos_events";
CREATE POLICY "rls_device_session_sos_events_insert" ON core."device_session_sos_events" FOR INSERT TO public
    WITH CHECK (
        (device_session_record_id IN (
            SELECT ds.device_session_record_id FROM device_sessions ds
            JOIN appointments a ON a.appointment_id = ds.appointment_id
            WHERE a.patient_id = rls_user_id()))
        OR (rls_user_role() = ANY (ARRAY['super_admin'::text, 'system'::text]))
    );

DROP POLICY IF EXISTS "rls_device_session_sos_events_update" ON core."device_session_sos_events";
CREATE POLICY "rls_device_session_sos_events_update" ON core."device_session_sos_events" FOR UPDATE TO public
    USING (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'system'::text])
    );


-- ###########################################################################
-- 12  Grants
-- ###########################################################################
-- Same split every clinical table in this schema uses: the app reads,
-- inserts and updates; deletion belongs to the purge worker, never a request.

GRANT SELECT, INSERT, UPDATE ON
    core."device_sessions", core."device_session_symptoms", core."device_session_adverse_events",
    core."device_session_notes", core."device_session_activities", core."device_session_scales",
    core."device_session_feedback", core."device_session_media", core."device_session_events",
    core."device_session_sos_events"
    TO anava_app;

REVOKE DELETE ON
    core."device_sessions", core."device_session_symptoms", core."device_session_adverse_events",
    core."device_session_notes", core."device_session_activities", core."device_session_scales",
    core."device_session_feedback", core."device_session_media", core."device_session_events",
    core."device_session_sos_events"
    FROM anava_app;

GRANT SELECT ON
    core."device_sessions", core."device_session_symptoms", core."device_session_adverse_events",
    core."device_session_notes", core."device_session_activities", core."device_session_scales",
    core."device_session_feedback", core."device_session_media", core."device_session_events",
    core."device_session_sos_events"
    TO anava_readonly;

-- Bucket 2 (patient data, anonymise never hard-delete): an erasure or
-- portability request has to be able to see what it is reporting on.
GRANT SELECT ON
    core."device_sessions", core."device_session_symptoms", core."device_session_adverse_events",
    core."device_session_notes", core."device_session_activities", core."device_session_scales",
    core."device_session_feedback", core."device_session_media", core."device_session_events",
    core."device_session_sos_events"
    TO anava_compliance;


COMMIT;


-- ###########################################################################
-- VERIFY — run after COMMIT
-- ###########################################################################
--
-- SELECT to_regclass('core.device_sessions');                 -- not null
-- SELECT count(*) FROM core.device_sessions;                  -- 0, new table
-- SELECT conname FROM pg_constraint
--  WHERE conrelid = 'core.device_sessions'::regclass AND contype = 'c';
--                                                               -- 2 CHECKs present
-- SELECT polname FROM pg_policies WHERE schemaname = 'core' AND tablename LIKE 'device_session%';
--                                                               -- 3 policies per table (most tables), 22+ total
--
--
-- ###########################################################################
-- STILL OPEN AFTER THIS FILE
-- ###########################################################################
--
--   1. backend/app/modules/device_sessions/* (repository.py, schemas.py,
--      service.py, router.py) — the module that actually reads/writes these
--      tables. This file is schema-only.
--
--   2. backend/alembic/versions/0036_device_session_records.py — the
--      wrapper, chained after 0035 (custom_montage_device_trigger_fix).
--
--   3. Alembic backfill for files 42-52 (pre-existing gap, not introduced
--      here) — separate migration-hygiene work.
-- ###########################################################################
