-- Fix: 16_rls_enable.sql blanket-enabled FORCE ROW LEVEL SECURITY on all 61
-- tables in SCHEMA_MAP without checking which ones source deliberately left
-- RLS off for. Found via testing the payments webhook fix, which cascaded
-- through emit_event() into outbox_events and surfaced this. 4 tables ended
-- up with RLS forced and ZERO policies — completely locked out for anava_app
-- (only the postgres/bypass role could touch them, which is exactly why the
-- earlier data migration into these tables succeeded despite this bug: it
-- ran as postgres, never as anava_app). alembic_version/schema_migrations
-- also hit this but are unused in v1 (excluded from migration) — not fixed,
-- not a real gap.

-- outbox_events: pure internal event-bus plumbing, written by every module's
-- emit_event() call on behalf of whichever actor is currently acting
-- (including 'system', post-webhook-fix). Not patient-sensitive data itself —
-- no need to scope WHO wrote an event, only that something did.
CREATE POLICY "rls_outbox_insert" ON ops."outbox_events" FOR INSERT TO public
    WITH CHECK (rls_user_role() IS NOT NULL);
CREATE POLICY "rls_outbox_select" ON ops."outbox_events" FOR SELECT TO public
    USING (rls_user_role() = ANY (ARRAY['super_admin'::text, 'system'::text]));

-- prs_option_translations / prs_question_translations: mirrors the exact
-- pattern already used by their non-translation siblings (prs_options,
-- prs_questions) — public read (i18n catalogue data, not sensitive),
-- super_admin-only write.
CREATE POLICY "rls_pot_select" ON reference."prs_option_translations" FOR SELECT TO public
    USING (true);
CREATE POLICY "rls_pot_write" ON reference."prs_option_translations" FOR INSERT TO public
    WITH CHECK (rls_user_role() = 'super_admin'::text);
CREATE POLICY "rls_pqt_select" ON reference."prs_question_translations" FOR SELECT TO public
    USING (true);
CREATE POLICY "rls_pqt_write" ON reference."prs_question_translations" FOR INSERT TO public
    WITH CHECK (rls_user_role() = 'super_admin'::text);

-- ca_doctor_assignments: this was a STATED design decision (NOTES.md — "gets
-- RLS here, it did NOT have RLS in production... uses the same policy pattern
-- as clinic_staff_assignments/doctor_patient_assignments") that was only
-- half-implemented — RLS got enabled but the actual policies were never
-- written. Mirrors doctor_patient_assignments, the closer sibling in shape.
CREATE POLICY "rls_cda_select" ON core."ca_doctor_assignments" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (clinic_id = rls_clinic_id())
        OR (ca_id = rls_user_id())
        OR (doctor_id = rls_user_id())
    );
CREATE POLICY "rls_cda_insert" ON core."ca_doctor_assignments" FOR INSERT TO public
    WITH CHECK (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text]));
CREATE POLICY "rls_cda_update" ON core."ca_doctor_assignments" FOR UPDATE TO public
    USING (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text]));
