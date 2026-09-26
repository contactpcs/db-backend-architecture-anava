-- 86_device_session_doctor_execution.sql
--
-- Lets a Doctor run a device session end-to-end, exactly like a Clinical
-- Assistant does today, while keeping device-session scheduling fully
-- independent of the doctor's own consultation calendar (deliberate product
-- decision — a doctor's device session and their own follow-up/consultation
-- are allowed to overlap; excl_doctor_overlap and excl_ca_overlap stay two
-- separate, untouched guards, see 31_appointments_payment_states.sql).
--
-- Also lets a patient self-log a device_session_activity from their own
-- portal (games/cognitive activities), alongside CA/doctor logging it
-- during the session — same "either portal" pattern device_session_scales
-- already has for PRS delivery.
--
-- APPLY ORDER: after 85. Depends on 56 (device_sessions + children) and
-- 52 (appointments.ca_id / excl_ca_overlap semantics).
--
--
-- ###########################################################################
-- 1  appointments.executor_role — role snapshot, not a live join
-- ###########################################################################
-- appointments.ca_id (52) already holds whoever actually ran a device
-- session, late-bound at start. Once a doctor can also land in that column,
-- "ca_id" alone no longer tells you which role ran it. Snapshotting the role
-- at the same write (not deriving it later via a live join to profiles.role)
-- matches this schema's own established pattern for this exact problem —
-- appointment_audit_logs.changed_by_role and device_session_events.actor_role
-- both do this already, specifically so history stays correct even if that
-- person's role changes later.

ALTER TABLE core."appointments" ADD COLUMN IF NOT EXISTS "executor_role" TEXT;

ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "chk_appointments_executor_role";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_executor_role"
    CHECK ("executor_role" IS NULL OR "executor_role" IN ('doctor', 'clinical_assistant', 'super_admin'));

COMMENT ON COLUMN core."appointments"."executor_role" IS 'Role of whoever is in ca_id, snapshotted at the same moment ca_id is late-bound (session start) — not derived from profiles.role, so it stays accurate if that person''s role changes later. NULL until a device_session actually starts; always NULL for doctor-scheduled types (initial/follow_up/protocol_followup), which use doctor_id instead.';

-- Update the stale part of ca_id's own semantics comment (52) — it no longer
-- assumes only a clinical assistant can occupy this column.
COMMENT ON COLUMN core."appointments"."ca_id" IS 'Whoever actually ran/is running a device_session — a clinical assistant or a doctor, late-bound at start (scheduling/service.py update_status), never set at booking. Paired with executor_role above to know which. Excluded from excl_doctor_overlap by design; protected only by excl_ca_overlap (device-session-vs-device-session overlap for the same executor), never cross-checked against that same person''s own doctor_id appointments — device-session scheduling is deliberately independent of a doctor''s consultation calendar.';


-- ###########################################################################
-- 2  device_sessions.performed_by_id / performed_by_role — denormalised
-- ###########################################################################
-- Same denormalisation precedent device_sessions.protocol_id already set
-- (56's own comment: "lets this row be read without joining appointments
-- for the common case") — a durable, queryable "who performed this session"
-- field, set once at the same start-of-session write as #1 above, instead of
-- requiring a join to appointments or a scan of device_session_events.

ALTER TABLE core."device_sessions" ADD COLUMN IF NOT EXISTS "performed_by_id" UUID;
ALTER TABLE core."device_sessions" ADD COLUMN IF NOT EXISTS "performed_by_role" TEXT;

ALTER TABLE core."device_sessions" DROP CONSTRAINT IF EXISTS "chk_device_sessions_performed_by_role";
ALTER TABLE core."device_sessions"
    ADD CONSTRAINT "chk_device_sessions_performed_by_role"
    CHECK ("performed_by_role" IS NULL OR "performed_by_role" IN ('doctor', 'clinical_assistant', 'super_admin'));

ALTER TABLE core."device_sessions" DROP CONSTRAINT IF EXISTS "fk_device_sessions_performed_by";
ALTER TABLE core."device_sessions" ADD CONSTRAINT "fk_device_sessions_performed_by"
    FOREIGN KEY ("performed_by_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

COMMENT ON COLUMN core."device_sessions"."performed_by_id" IS 'Denormalised from appointments.ca_id at session start — who actually ran this session.';
COMMENT ON COLUMN core."device_sessions"."performed_by_role" IS 'Denormalised from appointments.executor_role at session start — Doctor or Clinical Assistant. Same role-snapshot-not-live-join pattern as appointments.executor_role; see that column''s comment.';

COMMENT ON COLUMN core."device_sessions"."ca_declaration" IS 'Same shape as patient_consent, for the session executor''s own 4-statement declaration (checklist completed, scalp inspected at both sites, montage verified, will monitor and stop on adverse event) — filed by whoever is running the session (see performed_by_role), a clinical assistant or a doctor. Column name kept for continuity with existing app code; not renamed.';


-- ###########################################################################
-- 3  device_session_scales — new 'frozen' status (protocol-supersede freeze)
-- ###########################################################################
-- Closes a real analytics-integrity gap: without this, a PRS answer for a
-- protocol version's device session could be submitted arbitrarily late,
-- after the doctor has already amended that protocol multiple times, and
-- would retroactively change that (already reported on) old version's
-- outcome trend. 'frozen' is set the moment a protocol is superseded, on any
-- of its sessions' still-'pending' scale rows (application-layer sweep, see
-- treatment_protocols/service.py — not implemented in this file), and the
-- submission endpoint must hard-reject a write against a frozen row or this
-- is cosmetic only.

ALTER TABLE core."device_session_scales" DROP CONSTRAINT IF EXISTS "chk_dss2_status";
ALTER TABLE core."device_session_scales"
    ADD CONSTRAINT "chk_dss2_status" CHECK ("status" IN ('pending', 'in_progress', 'completed', 'frozen'));

COMMENT ON COLUMN core."device_session_scales"."status" IS 'pending -> in_progress -> completed, OR pending -> frozen if the owning protocol is superseded before the patient answers (application-layer sweep on amendment + a completion-time sweep for a session still in flight at that moment). A frozen row can never move to completed — the submission endpoint must reject that attempt outright, not just leave the row alone.';


-- ###########################################################################
-- 4  RLS — add 'doctor' everywhere a clinical_assistant could already write
-- ###########################################################################
-- Same set of tables/policies as 56_device_session_records.sql, doctor added
-- to every array that already had clinical_assistant. Re-stating each policy
-- in full (DROP + CREATE), matching this codebase's own convention for
-- patching an existing RLS policy (see 48/49/50 patching 47).

DROP POLICY IF EXISTS "rls_device_sessions_insert" ON core."device_sessions";
CREATE POLICY "rls_device_sessions_insert" ON core."device_sessions" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text, 'system'::text])
    );

DROP POLICY IF EXISTS "rls_device_sessions_update" ON core."device_sessions";
CREATE POLICY "rls_device_sessions_update" ON core."device_sessions" FOR UPDATE TO public
    USING (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text, 'system'::text])
    );

-- device_session_symptoms / adverse_events / notes / media / events: doctor
-- added to the shared INSERT-only pattern (56's DO $$ loop). device_session_
-- activities is deliberately NOT in this loop — it gets its own block below,
-- since it also needs 'patient' now (games/activities from the patient
-- portal), which none of these five do.
DO $$
DECLARE
    child TEXT;
    children TEXT[] := ARRAY[
        'device_session_symptoms', 'device_session_adverse_events', 'device_session_notes',
        'device_session_media', 'device_session_events'
    ];
BEGIN
    FOREACH child IN ARRAY children LOOP
        EXECUTE format(
            'DROP POLICY IF EXISTS %I ON core.%I', 'rls_' || child || '_insert', child
        );
        EXECUTE format($f$
            CREATE POLICY %I ON core.%I FOR INSERT TO public
            WITH CHECK (
                rls_user_role() = ANY (ARRAY['super_admin', 'clinic_admin', 'clinical_assistant', 'doctor', 'system'])
            )
        $f$, 'rls_' || child || '_insert', child);
    END LOOP;
END $$;

-- device_session_activities: doctor AND patient can now insert (a patient
-- logging their own game/activity from the patient portal, same "either
-- portal" shape device_session_scales already has for PRS delivery). Scoped
-- to the patient's own session via the appointment reachability check, same
-- pattern device_session_feedback/scales use for their patient-insert case.
DROP POLICY IF EXISTS "rls_device_session_activities_insert" ON core."device_session_activities";
CREATE POLICY "rls_device_session_activities_insert" ON core."device_session_activities" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text, 'system'::text]))
        OR (device_session_record_id IN (
            SELECT ds.device_session_record_id FROM device_sessions ds
            JOIN appointments a ON a.appointment_id = ds.appointment_id
            WHERE a.patient_id = rls_user_id()))
    );

-- device_session_scales
DROP POLICY IF EXISTS "rls_device_session_scales_insert" ON core."device_session_scales";
CREATE POLICY "rls_device_session_scales_insert" ON core."device_session_scales" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text, 'system'::text])
    );

DROP POLICY IF EXISTS "rls_device_session_scales_update" ON core."device_session_scales";
CREATE POLICY "rls_device_session_scales_update" ON core."device_session_scales" FOR UPDATE TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text, 'system'::text]))
        OR (device_session_record_id IN (
            SELECT ds.device_session_record_id FROM device_sessions ds
            JOIN appointments a ON a.appointment_id = ds.appointment_id
            WHERE a.patient_id = rls_user_id()))
    );

-- device_session_feedback
DROP POLICY IF EXISTS "rls_device_session_feedback_insert" ON core."device_session_feedback";
CREATE POLICY "rls_device_session_feedback_insert" ON core."device_session_feedback" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text, 'system'::text]))
        OR (device_session_record_id IN (
            SELECT ds.device_session_record_id FROM device_sessions ds
            JOIN appointments a ON a.appointment_id = ds.appointment_id
            WHERE a.patient_id = rls_user_id()))
    );

-- device_session_sos_events: doctor added to the acknowledge (UPDATE) side
-- only — raising an SOS (INSERT) stays patient-or-super_admin/system, unchanged.
DROP POLICY IF EXISTS "rls_device_session_sos_events_update" ON core."device_session_sos_events";
CREATE POLICY "rls_device_session_sos_events_update" ON core."device_session_sos_events" FOR UPDATE TO public
    USING (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text, 'system'::text])
    );


-- ###########################################################################
-- 5  One-time backfill — freeze pending scales on protocols already
--    superseded BEFORE this migration runs
-- ###########################################################################
-- The freeze-on-amendment sweep (application code, ProtocolService — not
-- this file) only fires going forward, at the moment a protocol is
-- superseded. Any protocol that was already amended in the past, before
-- this ships, would otherwise keep its stale pending PRS answerable
-- indefinitely — exactly the retroactive-report-change risk this whole
-- mechanism exists to close. Run once, here, so nothing is left exposed
-- from before this feature existed. Matches this schema's own precedent for
-- a corrective one-time UPDATE inside a migration (28_consent_redesign.sql).

UPDATE core."device_session_scales" ss
SET status = 'frozen', updated_at = NOW()
FROM core."device_sessions" ds
JOIN core."appointments" a ON a.appointment_id = ds.appointment_id
JOIN core."protocol_plan" pp ON pp.protocol_id = ds.protocol_id
WHERE ss.device_session_record_id = ds.device_session_record_id
  AND pp.status = 'superseded'
  AND a.status = 'completed'
  AND ss.status = 'pending';
