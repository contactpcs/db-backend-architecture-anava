-- 99_drop_legacy_sessions_add_clinical_notes.sql
--
-- THE PROBLEM
--
-- core.sessions and core.doctor_session_notes have been dead since commit
-- b341817 (confirmed in 58_protocol_instances_absorb_cycle.sql's own
-- comments: "sessions, doctor_session_notes, patient_eeg_files are 0-row
-- and have had no code path writing or reading them since ..."). Nothing
-- in the live backend creates a core.sessions row, and doctor_session_notes
-- additionally required a session_id satisfying a NOT NULL FK into that
-- same dead table, making it un-insertable even if application code were
-- written against it. Its shape (chief_complaint/clinical_observations/
-- assessment/treatment_plan_notes/follow_up_instructions/referrals, one row
-- per doctor+session_phase) also doesn't match the doctor's-notes feature
-- actually needed: a free-form, categorized, chronological note log per
-- patient, independent of the retired session/cycle model.
--
-- The doctor portal's "Doctor's Notes" UI has been running on purely
-- client-side fake state as a result (resets on every reload) — this
-- migration gives it a real table to persist to.
--
-- THE FIX
--
-- 1. Drop the 6 FK constraints elsewhere in the schema that reference
--    core.sessions (appointments, doctor_session_notes, patient_eeg_files,
--    payments, prs_assessment_instances, treatment_sessions) — all but
--    treatment_sessions already carry a nullable, unpopulated session_id
--    column; treatment_sessions is itself dead (only touched by the
--    generic retention-purge worker, never by feature code) and is left
--    in place with just its dangling constraint dropped, same pattern
--    58 already used for doctor_session_notes.cycle_id.
-- 2. Drop core.doctor_session_notes and core.sessions (CASCADE takes their
--    own indexes/triggers/RLS policies with them — nothing else references
--    doctor_session_notes).
-- 3. Create core.patient_clinical_notes: patient_id, doctor_id,
--    appointment_id (nullable — optional visit context, live FK),
--    category (CHECK against the same fixed list the frontend already
--    uses), note_text, created_at. One row per note, append-only from the
--    UI's perspective (no edit/delete endpoints planned — matches how the
--    fake client-side list behaved).

BEGIN;

-- ── 1. Strip dangling FKs into core.sessions ────────────────────────────
ALTER TABLE IF EXISTS core."appointments" DROP CONSTRAINT IF EXISTS "fk_appointments_session_id";
ALTER TABLE IF EXISTS core."doctor_session_notes" DROP CONSTRAINT IF EXISTS "fk_doctor_session_notes_session_id";
ALTER TABLE IF EXISTS core."patient_eeg_files" DROP CONSTRAINT IF EXISTS "fk_patient_eeg_files_session_id";
ALTER TABLE IF EXISTS core."payments" DROP CONSTRAINT IF EXISTS "fk_payments_session_id";
ALTER TABLE IF EXISTS core."prs_assessment_instances" DROP CONSTRAINT IF EXISTS "fk_prs_assessment_instances_session_id";
ALTER TABLE IF EXISTS core."treatment_sessions" DROP CONSTRAINT IF EXISTS "fk_treatment_sessions_session_id";

-- ── 2. Drop the two dead tables ──────────────────────────────────────────
DROP TABLE IF EXISTS core."doctor_session_notes" CASCADE;
DROP TABLE IF EXISTS core."sessions" CASCADE;

-- ── 3. New table for doctor's clinical notes ────────────────────────────
CREATE TABLE IF NOT EXISTS core."patient_clinical_notes" (
    "note_id"        UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id"     UUID NOT NULL REFERENCES core."profiles" ("id") ON DELETE RESTRICT,
    "doctor_id"      UUID NOT NULL REFERENCES core."profiles" ("id") ON DELETE RESTRICT,
    "appointment_id" UUID REFERENCES core."appointments" ("appointment_id") ON DELETE SET NULL,
    "category"       TEXT NOT NULL CHECK ("category" IN (
                          'Consultation', 'Assessment Review', 'Treatment Review',
                          'Session Review', 'Follow-up', 'General'
                      )),
    "note_text"      TEXT NOT NULL,
    "created_at"     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "patient_clinical_notes_pkey" PRIMARY KEY ("note_id")
);

CREATE INDEX IF NOT EXISTS idx_pcn_patient_id ON core."patient_clinical_notes" USING btree ("patient_id");
CREATE INDEX IF NOT EXISTS idx_pcn_doctor_id ON core."patient_clinical_notes" USING btree ("doctor_id");
CREATE INDEX IF NOT EXISTS idx_pcn_appointment_id ON core."patient_clinical_notes" USING btree ("appointment_id");
CREATE INDEX IF NOT EXISTS idx_pcn_patient_created ON core."patient_clinical_notes" USING btree ("patient_id", "created_at" DESC);

COMMENT ON TABLE core."patient_clinical_notes" IS 'Doctor-authored chronological clinical note log per patient — categorized free text, optionally tied to the visit it was written during. Replaces the dead core.doctor_session_notes (retired session/cycle model) as the backing store for the doctor portal''s Doctor''s Notes tab.';

DROP TRIGGER IF EXISTS trg_audit_patient_clinical_notes ON core."patient_clinical_notes";
CREATE TRIGGER trg_audit_patient_clinical_notes AFTER INSERT OR DELETE OR UPDATE ON core."patient_clinical_notes"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('note_id');

ALTER TABLE core."patient_clinical_notes" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."patient_clinical_notes" FORCE ROW LEVEL SECURITY;

-- Same shape as prs_assessment_instances' policies (17_rls_policies.sql) —
-- patient_id here is profiles.id, and clinic scoping goes through
-- patients.primary_clinic_id, not a nonexistent patients.clinic_id.
-- Notes are doctor/staff-authored only — never the patient themselves,
-- unlike PRS instances which a patient can self-write.
DROP POLICY IF EXISTS "rls_pcn_select" ON core."patient_clinical_notes";
CREATE POLICY "rls_pcn_select" ON core."patient_clinical_notes" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (doctor_id = rls_user_id())
        OR (
            (rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text]))
            AND (patient_id IN (SELECT patients.profile_id FROM patients WHERE patients.primary_clinic_id = rls_clinic_id()))
        )
    );

DROP POLICY IF EXISTS "rls_pcn_insert" ON core."patient_clinical_notes";
CREATE POLICY "rls_pcn_insert" ON core."patient_clinical_notes" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text, 'system'::text])
    );

DROP POLICY IF EXISTS "rls_pcn_update" ON core."patient_clinical_notes";
CREATE POLICY "rls_pcn_update" ON core."patient_clinical_notes" FOR UPDATE TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'system'::text]))
        OR (doctor_id = rls_user_id())
    );

-- Grants: read/insert/update for the app, deletion never (history only) —
-- same convention as prescribed_medicines (96).
GRANT SELECT, INSERT, UPDATE ON core."patient_clinical_notes" TO anava_app;
REVOKE DELETE ON core."patient_clinical_notes" FROM anava_app;
GRANT SELECT ON core."patient_clinical_notes" TO anava_readonly;
GRANT SELECT ON core."patient_clinical_notes" TO anava_compliance;

COMMIT;
