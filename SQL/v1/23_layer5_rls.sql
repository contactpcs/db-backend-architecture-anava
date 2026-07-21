-- Layer 5 — RLS. Verified against source: profiles.role and admins.admin_type
-- mirror each other for admin identities (both hold 'super_admin' etc.
-- consistently). Provisioning a Grievance Officer (Section 11) means setting
-- BOTH profiles.role='grievance_officer' AND admins.admin_type='grievance_officer',
-- matching the existing pattern — rls_user_role() reads from profiles.role.

ALTER TABLE compliance."erasure_requests" ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance."erasure_requests" FORCE ROW LEVEL SECURITY;
ALTER TABLE compliance."erasure_request_items" ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance."erasure_request_items" FORCE ROW LEVEL SECURITY;
ALTER TABLE compliance."data_portability_requests" ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance."data_portability_requests" FORCE ROW LEVEL SECURITY;
ALTER TABLE compliance."staff_termination_authorizations" ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance."staff_termination_authorizations" FORCE ROW LEVEL SECURITY;
ALTER TABLE compliance."compliance_incidents" ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance."compliance_incidents" FORCE ROW LEVEL SECURITY;
ALTER TABLE compliance."manual_snapshots" ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance."manual_snapshots" FORCE ROW LEVEL SECURITY;

-- erasure_requests: patient can file + see their own; super_admin and the
-- grievance officer (the named accountable role for these requests, Section 11)
-- see and process all.
CREATE POLICY "rls_erasure_req_select" ON compliance."erasure_requests" FOR SELECT TO public
    USING ((patient_id = rls_user_id()) OR (rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text])));
CREATE POLICY "rls_erasure_req_insert" ON compliance."erasure_requests" FOR INSERT TO public
    WITH CHECK ((patient_id = rls_user_id()) OR (rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text])));
CREATE POLICY "rls_erasure_req_update" ON compliance."erasure_requests" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text])));

-- erasure_request_items: classification-pass detail. Patient can see (transparency —
-- "patient is told what is retained, why, and when it will be deleted", Section 3.5),
-- but only the grievance officer / super_admin classify and write.
CREATE POLICY "rls_erasure_items_select" ON compliance."erasure_request_items" FOR SELECT TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text]))
        OR (EXISTS (SELECT 1 FROM compliance.erasure_requests er WHERE er.request_id = erasure_request_items.request_id AND er.patient_id = rls_user_id())));
CREATE POLICY "rls_erasure_items_insert" ON compliance."erasure_request_items" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text])));
CREATE POLICY "rls_erasure_items_update" ON compliance."erasure_request_items" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text])));

-- data_portability_requests: same self-service + grievance-officer pattern as erasure.
CREATE POLICY "rls_dpr_select" ON compliance."data_portability_requests" FOR SELECT TO public
    USING ((patient_id = rls_user_id()) OR (rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text])));
CREATE POLICY "rls_dpr_insert" ON compliance."data_portability_requests" FOR INSERT TO public
    WITH CHECK ((patient_id = rls_user_id()) OR (rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text])));
CREATE POLICY "rls_dpr_update" ON compliance."data_portability_requests" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text])));

-- staff_termination_authorizations: admin-tier only, never patient-visible.
-- clinic_admin included for insert (initiates voluntary/no-cause) but not select-all —
-- kept simple/conservative: only super_admin and regional_admin see the full log.
CREATE POLICY "rls_staff_term_select" ON compliance."staff_termination_authorizations" FOR SELECT TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])));
CREATE POLICY "rls_staff_term_insert" ON compliance."staff_termination_authorizations" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));
CREATE POLICY "rls_staff_term_update" ON compliance."staff_termination_authorizations" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])));

-- compliance_incidents: super_admin only — breach handling is the most sensitive
-- record type in this schema, deliberately narrow.
CREATE POLICY "rls_incidents_select" ON compliance."compliance_incidents" FOR SELECT TO public
    USING ((rls_user_role() = 'super_admin'::text));
CREATE POLICY "rls_incidents_insert" ON compliance."compliance_incidents" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
CREATE POLICY "rls_incidents_update" ON compliance."compliance_incidents" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

-- manual_snapshots: infra operation, admin-tier.
CREATE POLICY "rls_snapshots_select" ON compliance."manual_snapshots" FOR SELECT TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])));
CREATE POLICY "rls_snapshots_insert" ON compliance."manual_snapshots" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])));
CREATE POLICY "rls_snapshots_update" ON compliance."manual_snapshots" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])));
