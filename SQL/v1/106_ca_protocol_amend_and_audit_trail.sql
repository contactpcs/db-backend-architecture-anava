-- 106_ca_protocol_amend_and_audit_trail.sql
--
-- Clinical-assistant (CA) parity with the doctor's patient workspace
-- (requirement 2026-10-04). PRS, anamnesis and device sessions were already
-- open to the CA. Treatment protocols were not: every write policy below
-- named super_admin / clinic_admin / doctor only.
--
-- RULE, enforced here as well as in ProtocolService:
--   * A CA can never CREATE a protocol. She may only AMEND one, which is a
--     new protocol_plan row carrying supersedes_protocol_id (version x.N+1).
--   * A CA may edit a draft and may activate HER OWN amendment.
--   * A CA can never cancel or complete a protocol, and never opens or
--     changes a protocol_instance. Those policies are untouched.
--
-- An amendment is one transaction that writes protocol_plan (new row, old
-- row to 'superseded'), protocol_conditions / protocol_diagnoses /
-- protocol_scales, appointments, protocol_device_sessions and
-- protocol_followup. appointments and device_session_scales already allow
-- the CA. Every other table in that list is opened here, because a missing
-- one fails the whole amendment (INSERT) or silently updates 0 rows (UPDATE).
--
-- AUDIT TRAIL, second half of the same requirement (who did what, when):
--   * audit_logs gets changed_by_role, written by ops.fn_audit_trigger (107).
--   * trg_audit_anamnesis_assessments was found DISABLED on the live DB
--     (Data Capture Audit 2026-09-21, a manual change never in the repo).
--     Re-enabled here.
--   * protocol_instances, protocol_conditions and protocol_scales had no
--     audit trigger at all.
--   * anamnesis_responses and prs_responses had none either. A consultation's
--     anamnesis is ONE record shared by the doctor and the clinical assistant,
--     and the record only names whoever opened it first. The per-answer
--     audit row is what says who gave or changed each answer.
--   * A doctor could not read activity_logs. One narrow extra SELECT policy
--     lets a doctor read protocol events of their own clinic, nothing else.
--
-- No statement in this file contains a semicolon inside its body and there
-- are no dollar-quoted blocks: alembic 0059 splits it on the semicolon.
--
-- APPLY ORDER: after 105, before 107.

DROP POLICY IF EXISTS "rls_protocol_plan_insert" ON core."protocol_plan";
CREATE POLICY "rls_protocol_plan_insert" ON core."protocol_plan" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text]))
        OR (
            rls_user_role() = 'clinical_assistant'::text
            AND supersedes_protocol_id IS NOT NULL
            AND set_by = rls_user_id()
            AND status = 'draft'::text
        )
    );

DROP POLICY IF EXISTS "rls_protocol_plan_update" ON core."protocol_plan";
CREATE POLICY "rls_protocol_plan_update" ON core."protocol_plan" FOR UPDATE TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text]))
        OR (
            rls_user_role() = 'clinical_assistant'::text
            AND status = ANY (ARRAY['draft'::text, 'active'::text])
        )
    )
    WITH CHECK (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text]))
        OR (
            rls_user_role() = 'clinical_assistant'::text
            AND (
                status = ANY (ARRAY['draft'::text, 'superseded'::text])
                OR (
                    status = 'active'::text
                    AND set_by = rls_user_id()
                    AND supersedes_protocol_id IS NOT NULL
                )
            )
        )
    );

DROP POLICY IF EXISTS "rls_protocol_conditions_insert" ON core."protocol_conditions";
CREATE POLICY "rls_protocol_conditions_insert" ON core."protocol_conditions" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text])
    );

DROP POLICY IF EXISTS "rls_protocol_diagnoses_insert" ON core."protocol_diagnoses";
CREATE POLICY "rls_protocol_diagnoses_insert" ON core."protocol_diagnoses" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text])
    );

DROP POLICY IF EXISTS "rls_protocol_scales_insert" ON core."protocol_scales";
CREATE POLICY "rls_protocol_scales_insert" ON core."protocol_scales" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text])
    );

DROP POLICY IF EXISTS "rls_protocol_device_sessions_insert" ON core."protocol_device_sessions";
CREATE POLICY "rls_protocol_device_sessions_insert" ON core."protocol_device_sessions" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'system'::text])
    );

DROP POLICY IF EXISTS "rls_protocol_device_sessions_update" ON core."protocol_device_sessions";
CREATE POLICY "rls_protocol_device_sessions_update" ON core."protocol_device_sessions" FOR UPDATE TO public
    USING (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'system'::text])
    );

DROP POLICY IF EXISTS "rls_protocol_followup_insert" ON core."protocol_followup";
CREATE POLICY "rls_protocol_followup_insert" ON core."protocol_followup" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'system'::text])
    );

DROP POLICY IF EXISTS "rls_protocol_followup_update" ON core."protocol_followup";
CREATE POLICY "rls_protocol_followup_update" ON core."protocol_followup" FOR UPDATE TO public
    USING (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'system'::text])
    );

ALTER TABLE compliance."audit_logs" ADD COLUMN IF NOT EXISTS "changed_by_role" TEXT;

COMMENT ON COLUMN compliance."audit_logs"."changed_by_role" IS 'Role the actor held when the row changed (app.current_user_role). NULL on rows written before 106 and on worker or script writes with no request.';

ALTER TABLE core."anamnesis_assessments" ENABLE TRIGGER "trg_audit_anamnesis_assessments";

DROP TRIGGER IF EXISTS trg_audit_protocol_instances ON core."protocol_instances";
CREATE TRIGGER trg_audit_protocol_instances
    AFTER INSERT OR DELETE OR UPDATE ON core."protocol_instances"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('instance_id');

DROP TRIGGER IF EXISTS trg_audit_protocol_conditions ON core."protocol_conditions";
CREATE TRIGGER trg_audit_protocol_conditions
    AFTER INSERT OR DELETE OR UPDATE ON core."protocol_conditions"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('protocol_condition_id');

DROP TRIGGER IF EXISTS trg_audit_protocol_scales ON core."protocol_scales";
CREATE TRIGGER trg_audit_protocol_scales
    AFTER INSERT OR DELETE OR UPDATE ON core."protocol_scales"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('protocol_scale_id');

DROP TRIGGER IF EXISTS trg_audit_anamnesis_responses ON core."anamnesis_responses";
CREATE TRIGGER trg_audit_anamnesis_responses
    AFTER INSERT OR DELETE OR UPDATE ON core."anamnesis_responses"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('response_id');

DROP TRIGGER IF EXISTS trg_audit_prs_responses ON core."prs_responses";
CREATE TRIGGER trg_audit_prs_responses
    AFTER INSERT OR DELETE OR UPDATE ON core."prs_responses"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('response_id');

DROP POLICY IF EXISTS "rls_actlog_select_protocol_events" ON compliance."activity_logs";
CREATE POLICY "rls_actlog_select_protocol_events" ON compliance."activity_logs" FOR SELECT TO public
    USING (
        rls_user_role() = 'doctor'::text
        AND entity_type = 'treatment_protocol'::text
        AND clinic_id = rls_clinic_id()
    );
