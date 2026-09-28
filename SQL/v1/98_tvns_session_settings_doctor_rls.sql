-- The device-session API allows doctors to execute sessions, matching the
-- doctor-widened policies in 86_device_session_doctor_execution.sql. The
-- tVNS child table was added later in 90 and initially omitted that role.
DROP POLICY IF EXISTS "rls_tvns_session_settings_insert" ON core."tvns_session_settings";
CREATE POLICY "rls_tvns_session_settings_insert" ON core."tvns_session_settings" FOR INSERT TO public
    WITH CHECK (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text, 'system'::text])
    );

DROP POLICY IF EXISTS "rls_tvns_session_settings_update" ON core."tvns_session_settings";
CREATE POLICY "rls_tvns_session_settings_update" ON core."tvns_session_settings" FOR UPDATE TO public
    USING (
        rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text, 'system'::text])
    );