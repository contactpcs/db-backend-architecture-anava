-- ============================================================
-- Anava Clinic — DB Schema
-- File 15: Row Level Security (RLS) Policies
--
-- HOW THIS WORKS:
-- FastAPI middleware runs at the start of every request:
--   await db.execute("SET LOCAL app.current_user_id = :uid",  {"uid": str(user.id)})
--   await db.execute("SET LOCAL app.current_user_role = :role", {"role": user.role})
--   await db.execute("SET LOCAL app.current_clinic_id = :cid", {"cid": str(user.clinic_id)})
--   await db.execute("SET LOCAL app.current_region_id = :rid", {"rid": str(user.region_id)})
--
-- RLS policies read these settings via current_setting().
-- 'TRUE' as second arg = return '' (not error) if setting not set.
--
-- NOTE: RLS does NOT replace application-level permission checks.
-- Both layers run: RLS is the final safety net at DB level.
-- ============================================================

-- Enable RLS on all user-facing tables.
-- FORCE ROW LEVEL SECURITY: ensures policies apply even to the table OWNER.
-- Without FORCE, the DB owner (e.g. the application role granted ownership)
-- silently bypasses ALL policies — a full PHI exposure vulnerability.
ALTER TABLE profiles                    ENABLE ROW LEVEL SECURITY;
ALTER TABLE profiles                    FORCE ROW LEVEL SECURITY;
ALTER TABLE admins                      ENABLE ROW LEVEL SECURITY;
ALTER TABLE admins                      FORCE ROW LEVEL SECURITY;
ALTER TABLE regions                     ENABLE ROW LEVEL SECURITY;
ALTER TABLE regions                     FORCE ROW LEVEL SECURITY;
ALTER TABLE clinics                     ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinics                     FORCE ROW LEVEL SECURITY;
ALTER TABLE clinic_staff_assignments    ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinic_staff_assignments    FORCE ROW LEVEL SECURITY;
ALTER TABLE doctors                     ENABLE ROW LEVEL SECURITY;
ALTER TABLE doctors                     FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical_assistants         ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical_assistants         FORCE ROW LEVEL SECURITY;
ALTER TABLE receptionists               ENABLE ROW LEVEL SECURITY;
ALTER TABLE receptionists               FORCE ROW LEVEL SECURITY;
ALTER TABLE patients                    ENABLE ROW LEVEL SECURITY;
ALTER TABLE patients                    FORCE ROW LEVEL SECURITY;
ALTER TABLE patient_disease_selection   ENABLE ROW LEVEL SECURITY;
ALTER TABLE patient_disease_selection   FORCE ROW LEVEL SECURITY;
ALTER TABLE clinic_requests             ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinic_requests             FORCE ROW LEVEL SECURITY;
ALTER TABLE staff_requests              ENABLE ROW LEVEL SECURITY;
ALTER TABLE staff_requests              FORCE ROW LEVEL SECURITY;
ALTER TABLE doctor_patient_assignments  ENABLE ROW LEVEL SECURITY;
ALTER TABLE doctor_patient_assignments  FORCE ROW LEVEL SECURITY;
ALTER TABLE treatment_cycles          ENABLE ROW LEVEL SECURITY;
ALTER TABLE treatment_cycles          FORCE ROW LEVEL SECURITY;
ALTER TABLE assessment_protocol_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE assessment_protocol_requests FORCE ROW LEVEL SECURITY;
ALTER TABLE sessions                    ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions                    FORCE ROW LEVEL SECURITY;
ALTER TABLE treatment_plans             ENABLE ROW LEVEL SECURITY;
ALTER TABLE treatment_plans             FORCE ROW LEVEL SECURITY;
ALTER TABLE treatment_sessions          ENABLE ROW LEVEL SECURITY;
ALTER TABLE treatment_sessions          FORCE ROW LEVEL SECURITY;
ALTER TABLE prs_diseases                ENABLE ROW LEVEL SECURITY;
ALTER TABLE prs_diseases                FORCE ROW LEVEL SECURITY;
ALTER TABLE prs_scales                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE prs_scales                  FORCE ROW LEVEL SECURITY;
ALTER TABLE prs_questions               ENABLE ROW LEVEL SECURITY;
ALTER TABLE prs_questions               FORCE ROW LEVEL SECURITY;
ALTER TABLE patient_scale_assignments   ENABLE ROW LEVEL SECURITY;
ALTER TABLE patient_scale_assignments   FORCE ROW LEVEL SECURITY;
ALTER TABLE prs_assessment_instances    ENABLE ROW LEVEL SECURITY;
ALTER TABLE prs_assessment_instances    FORCE ROW LEVEL SECURITY;
ALTER TABLE prs_responses               ENABLE ROW LEVEL SECURITY;
ALTER TABLE prs_responses               FORCE ROW LEVEL SECURITY;
ALTER TABLE anamnesis_assessments       ENABLE ROW LEVEL SECURITY;
ALTER TABLE anamnesis_assessments       FORCE ROW LEVEL SECURITY;
ALTER TABLE consent_templates           ENABLE ROW LEVEL SECURITY;
ALTER TABLE consent_templates           FORCE ROW LEVEL SECURITY;
ALTER TABLE consent_records             ENABLE ROW LEVEL SECURITY;
ALTER TABLE consent_records             FORCE ROW LEVEL SECURITY;
ALTER TABLE patient_clinic_transfers    ENABLE ROW LEVEL SECURITY;
ALTER TABLE patient_clinic_transfers    FORCE ROW LEVEL SECURITY;
ALTER TABLE products                    ENABLE ROW LEVEL SECURITY;
ALTER TABLE products                    FORCE ROW LEVEL SECURITY;
ALTER TABLE store_orders                ENABLE ROW LEVEL SECURITY;
ALTER TABLE store_orders                FORCE ROW LEVEL SECURITY;
ALTER TABLE order_items                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_items                 FORCE ROW LEVEL SECURITY;
ALTER TABLE inventory                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory                   FORCE ROW LEVEL SECURITY;
ALTER TABLE stock_transfers             ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_transfers             FORCE ROW LEVEL SECURITY;
ALTER TABLE device_assignments          ENABLE ROW LEVEL SECURITY;
ALTER TABLE device_assignments          FORCE ROW LEVEL SECURITY;
ALTER TABLE payments                    ENABLE ROW LEVEL SECURITY;
ALTER TABLE payments                    FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_logs                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs                  FORCE ROW LEVEL SECURITY;
ALTER TABLE activity_logs               ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_logs               FORCE ROW LEVEL SECURITY;

-- ============================================================
-- HELPER: current user context functions
-- ============================================================

CREATE OR REPLACE FUNCTION rls_user_id() RETURNS UUID AS $$
    SELECT NULLIF(current_setting('app.current_user_id', TRUE), '')::UUID;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION rls_user_role() RETURNS TEXT AS $$
    SELECT NULLIF(current_setting('app.current_user_role', TRUE), '');
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION rls_clinic_id() RETURNS UUID AS $$
    SELECT NULLIF(current_setting('app.current_clinic_id', TRUE), '')::UUID;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION rls_region_id() RETURNS UUID AS $$
    SELECT NULLIF(current_setting('app.current_region_id', TRUE), '')::UUID;
$$ LANGUAGE sql STABLE;

-- ============================================================
-- profiles
-- ============================================================

-- super_admin / regional_admin: full read
-- clinic_admin / doctor / ca / receptionist: own profile + clinic members
-- patient: own profile only

CREATE POLICY rls_profiles_select ON profiles FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR rls_user_role() = 'regional_admin'
    OR id = rls_user_id()
    OR (
        rls_user_role() IN ('clinic_admin', 'doctor', 'clinical_assistant', 'receptionist')
        AND id IN (
            SELECT profile_id FROM clinic_staff_assignments
            WHERE clinic_id = rls_clinic_id() AND is_active = TRUE
            UNION
            SELECT profile_id FROM patients
            WHERE primary_clinic_id = rls_clinic_id()
        )
    )
);

CREATE POLICY rls_profiles_insert ON profiles FOR INSERT
WITH CHECK (
    -- Admins and receptionists create profiles for staff and patients.
    -- Patient self-registration is handled by a dedicated endpoint where
    -- application validates Cognito JWT matches the profile being created.
    rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin', 'receptionist', 'patient')
);

CREATE POLICY rls_profiles_update ON profiles FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin')
    OR id = rls_user_id()
);

-- ============================================================
-- regions
-- ============================================================

CREATE POLICY rls_regions_select ON regions FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR region_id = rls_region_id()
);

CREATE POLICY rls_regions_insert ON regions FOR INSERT
WITH CHECK (rls_user_role() = 'super_admin');

CREATE POLICY rls_regions_update ON regions FOR UPDATE
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND region_id = rls_region_id())
);

-- ============================================================
-- clinics
-- ============================================================

CREATE POLICY rls_clinics_select ON clinics FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND region_id = rls_region_id())
    OR clinic_id = rls_clinic_id()
);

CREATE POLICY rls_clinics_insert ON clinics FOR INSERT
WITH CHECK (rls_user_role() = 'super_admin');

CREATE POLICY rls_clinics_update ON clinics FOR UPDATE
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND region_id = rls_region_id())
    OR (rls_user_role() = 'clinic_admin' AND clinic_id = rls_clinic_id())
);

-- ============================================================
-- clinic_staff_assignments
-- ============================================================

CREATE POLICY rls_csa_select ON clinic_staff_assignments FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND clinic_id IN (
        SELECT clinic_id FROM clinics WHERE region_id = rls_region_id()
    ))
    OR clinic_id = rls_clinic_id()
);

-- Tenant-scoped, not role-only (fixed post-review: role-only checks here let
-- a clinic_admin at Clinic A write staff assignments at Clinic B — matches
-- the tenant-scoped pattern already correct on treatment_cycles/sessions).
CREATE POLICY rls_csa_insert ON clinic_staff_assignments FOR INSERT
WITH CHECK (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND clinic_id IN (
        SELECT clinic_id FROM clinics WHERE region_id = rls_region_id()
    ))
    OR (rls_user_role() = 'clinic_admin' AND clinic_id = rls_clinic_id())
);

CREATE POLICY rls_csa_update ON clinic_staff_assignments FOR UPDATE
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND clinic_id IN (
        SELECT clinic_id FROM clinics WHERE region_id = rls_region_id()
    ))
    OR (rls_user_role() = 'clinic_admin' AND clinic_id = rls_clinic_id())
);

-- ============================================================
-- patients
-- ============================================================

CREATE POLICY rls_patients_select ON patients FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND primary_clinic_id IN (
        SELECT clinic_id FROM clinics WHERE region_id = rls_region_id()
    ))
    OR primary_clinic_id = rls_clinic_id()
    OR profile_id = rls_user_id()
    OR (
        rls_user_role() = 'doctor' AND primary_doctor_id = rls_user_id()
    )
);

CREATE POLICY rls_patients_insert ON patients FOR INSERT
WITH CHECK (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'receptionist')
);

CREATE POLICY rls_patients_update ON patients FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'receptionist')
    OR profile_id = rls_user_id()
);

-- ============================================================
-- treatment_cycles
-- ============================================================

CREATE POLICY rls_cycles_select ON treatment_cycles FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND clinic_id IN (
        SELECT clinic_id FROM clinics WHERE region_id = rls_region_id()
    ))
    OR clinic_id = rls_clinic_id()
    OR patient_id = rls_user_id()
);

CREATE POLICY rls_cycles_insert ON treatment_cycles FOR INSERT
WITH CHECK (clinic_id = rls_clinic_id() OR rls_user_role() = 'super_admin');

CREATE POLICY rls_cycles_update ON treatment_cycles FOR UPDATE
USING (clinic_id = rls_clinic_id() OR rls_user_role() = 'super_admin');

-- ============================================================
-- sessions
-- ============================================================

CREATE POLICY rls_sessions_select ON sessions FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND clinic_id IN (
        SELECT clinic_id FROM clinics WHERE region_id = rls_region_id()
    ))
    OR clinic_id = rls_clinic_id()
    OR patient_id = rls_user_id()
);

CREATE POLICY rls_sessions_insert ON sessions FOR INSERT
WITH CHECK (clinic_id = rls_clinic_id() OR rls_user_role() = 'super_admin');

CREATE POLICY rls_sessions_update ON sessions FOR UPDATE
USING (clinic_id = rls_clinic_id() OR rls_user_role() = 'super_admin');

-- ============================================================
-- treatment_plans
-- ============================================================

CREATE POLICY rls_tp_select ON treatment_plans FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR patient_id = rls_user_id()
    OR doctor_id = rls_user_id()
    OR cycle_id IN (
        SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id()
    )
);

-- Tenant/ownership-scoped, not role-only (fixed post-review): a Doctor could
-- otherwise write a treatment plan for a patient/cycle at any clinic, not
-- just their own — the role-only check never verified doctor_id or clinic.
CREATE POLICY rls_tp_insert ON treatment_plans FOR INSERT
WITH CHECK (
    rls_user_role() = 'super_admin'
    OR (
        rls_user_role() = 'doctor'
        AND doctor_id = rls_user_id()
        AND cycle_id IN (SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id())
    )
);

CREATE POLICY rls_tp_update ON treatment_plans FOR UPDATE
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'doctor' AND doctor_id = rls_user_id())
);

-- ============================================================
-- treatment_sessions
-- ============================================================

CREATE POLICY rls_ts_select ON treatment_sessions FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin')
    OR ca_id = rls_user_id()
    OR patient_id = rls_user_id()
    OR plan_id IN (
        SELECT plan_id FROM treatment_plans WHERE doctor_id = rls_user_id()
    )
);

-- Tenant/ownership-scoped, not role-only (fixed post-review): a CA or Clinic
-- Admin could otherwise write a treatment session at any clinic.
CREATE POLICY rls_ts_insert ON treatment_sessions FOR INSERT
WITH CHECK (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'clinical_assistant' AND ca_id = rls_user_id())
    OR (
        rls_user_role() = 'clinic_admin'
        AND plan_id IN (
            SELECT tp.plan_id FROM treatment_plans tp
            JOIN treatment_cycles tc ON tc.cycle_id = tp.cycle_id
            WHERE tc.clinic_id = rls_clinic_id()
        )
    )
);

CREATE POLICY rls_ts_update ON treatment_sessions FOR UPDATE
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'clinical_assistant' AND ca_id = rls_user_id())
    OR (
        rls_user_role() = 'clinic_admin'
        AND plan_id IN (
            SELECT tp.plan_id FROM treatment_plans tp
            JOIN treatment_cycles tc ON tc.cycle_id = tp.cycle_id
            WHERE tc.clinic_id = rls_clinic_id()
        )
    )
);

-- ============================================================
-- consent_records — never deleted; select broadly, insert + update admin/clinical only
-- ============================================================

CREATE POLICY rls_cr_select ON consent_records FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND clinic_id IN (
        SELECT clinic_id FROM clinics WHERE region_id = rls_region_id()
    ))
    OR clinic_id = rls_clinic_id()
    OR patient_id = rls_user_id()
    OR staff_id = rls_user_id()
);

CREATE POLICY rls_cr_insert ON consent_records FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin', 'receptionist'));

CREATE POLICY rls_cr_update ON consent_records FOR UPDATE
USING (rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin'));

-- DELETE blocked by application convention: no DELETE policy defined = deny by default

-- ============================================================
-- prs_diseases / prs_scales / prs_questions — reference data, read by all roles
-- ============================================================

CREATE POLICY rls_prs_diseases_select ON prs_diseases FOR SELECT USING (TRUE);
CREATE POLICY rls_prs_scales_select   ON prs_scales   FOR SELECT USING (TRUE);
CREATE POLICY rls_prs_questions_select ON prs_questions FOR SELECT USING (TRUE);
CREATE POLICY rls_ct_select ON consent_templates FOR SELECT USING (TRUE);

-- Write only by super_admin
CREATE POLICY rls_prs_diseases_write ON prs_diseases FOR INSERT WITH CHECK (rls_user_role() = 'super_admin');
CREATE POLICY rls_prs_scales_write   ON prs_scales   FOR INSERT WITH CHECK (rls_user_role() = 'super_admin');
CREATE POLICY rls_prs_questions_write ON prs_questions FOR INSERT WITH CHECK (rls_user_role() = 'super_admin');
CREATE POLICY rls_ct_insert ON consent_templates FOR INSERT WITH CHECK (rls_user_role() = 'super_admin');
CREATE POLICY rls_ct_update ON consent_templates FOR UPDATE USING (rls_user_role() = 'super_admin');

-- ============================================================
-- prs_assessment_instances / prs_responses — patient clinical data
-- ============================================================

CREATE POLICY rls_pai_select ON prs_assessment_instances FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR patient_id = rls_user_id()
    OR cycle_id IN (SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id())
);

-- INSERT/UPDATE required or RLS implicitly denies all writes (P0 — clinical assessments uncreateable)
CREATE POLICY rls_pai_insert ON prs_assessment_instances FOR INSERT
WITH CHECK (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'clinical_assistant', 'receptionist')
    OR patient_id = rls_user_id()
);

CREATE POLICY rls_pai_update ON prs_assessment_instances FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'clinical_assistant', 'doctor')
    OR patient_id = rls_user_id()
);

CREATE POLICY rls_prs_resp_select ON prs_responses FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR instance_id IN (
        SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id()
    )
    OR instance_id IN (
        SELECT instance_id FROM prs_assessment_instances
        WHERE cycle_id IN (SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id())
    )
);

CREATE POLICY rls_prs_resp_insert ON prs_responses FOR INSERT
WITH CHECK (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'clinical_assistant', 'doctor')
    OR instance_id IN (
        SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id()
    )
);

CREATE POLICY rls_prs_resp_update ON prs_responses FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'clinical_assistant', 'doctor')
    OR instance_id IN (
        SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id()
    )
);

-- ============================================================
-- store_orders / order_items
-- ============================================================

CREATE POLICY rls_so_select ON store_orders FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR clinic_id = rls_clinic_id()
    OR patient_id = rls_user_id()
);

CREATE POLICY rls_so_insert ON store_orders FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'clinic_admin', 'receptionist'));

CREATE POLICY rls_so_update ON store_orders FOR UPDATE
USING (rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin', 'doctor', 'receptionist'));

CREATE POLICY rls_oi_select ON order_items FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR order_id IN (SELECT order_id FROM store_orders WHERE clinic_id = rls_clinic_id())
);

-- ============================================================
-- payments
-- ============================================================

CREATE POLICY rls_payments_select ON payments FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin')
    OR session_id IN (SELECT session_id FROM sessions WHERE clinic_id = rls_clinic_id())
    OR order_id IN (SELECT order_id FROM store_orders WHERE clinic_id = rls_clinic_id())
);

CREATE POLICY rls_payments_insert ON payments FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'clinic_admin', 'receptionist'));

CREATE POLICY rls_payments_update ON payments FOR UPDATE
USING (rls_user_role() IN ('super_admin', 'clinic_admin'));

-- ============================================================
-- audit_logs — read by admins only; INSERT by trigger (SECURITY DEFINER)
-- No UPDATE, no DELETE ever.
-- ============================================================

CREATE POLICY rls_audit_select ON audit_logs FOR SELECT
USING (rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin'));

-- audit_logs INSERT is performed by fn_audit_trigger() which is SECURITY DEFINER.
-- Application role must NOT have INSERT on audit_logs directly.
-- Achieve by: REVOKE INSERT ON audit_logs FROM anava_app_role;
-- (Run in environment-specific setup, not here.)

-- ============================================================
-- activity_logs — insert by app, read by admins
-- ============================================================

CREATE POLICY rls_actlog_select ON activity_logs FOR SELECT
USING (rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin'));

CREATE POLICY rls_actlog_insert ON activity_logs FOR INSERT
WITH CHECK (TRUE);  -- any authenticated user can insert their own events


-- ============================================================
-- admins
-- ============================================================

CREATE POLICY rls_admins_select ON admins FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND region_id = rls_region_id())
    OR profile_id = rls_user_id()
);

CREATE POLICY rls_admins_insert ON admins FOR INSERT
WITH CHECK (rls_user_role() = 'super_admin');

CREATE POLICY rls_admins_update ON admins FOR UPDATE
USING (rls_user_role() = 'super_admin');


-- ============================================================
-- doctors / clinical_assistants / receptionists
-- ============================================================

CREATE POLICY rls_doctors_select ON doctors FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR profile_id = rls_user_id()
    OR profile_id IN (
        SELECT profile_id FROM clinic_staff_assignments
        WHERE clinic_id = rls_clinic_id() AND is_active = TRUE
    )
);

CREATE POLICY rls_doctors_insert ON doctors FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin'));

CREATE POLICY rls_doctors_update ON doctors FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin')
    OR profile_id = rls_user_id()
);

CREATE POLICY rls_ca_select ON clinical_assistants FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR profile_id = rls_user_id()
    OR profile_id IN (
        SELECT profile_id FROM clinic_staff_assignments
        WHERE clinic_id = rls_clinic_id() AND is_active = TRUE
    )
);

CREATE POLICY rls_ca_insert ON clinical_assistants FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin'));

CREATE POLICY rls_ca_update ON clinical_assistants FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin')
    OR profile_id = rls_user_id()
);

CREATE POLICY rls_recep_select ON receptionists FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR profile_id = rls_user_id()
    OR profile_id IN (
        SELECT profile_id FROM clinic_staff_assignments
        WHERE clinic_id = rls_clinic_id() AND is_active = TRUE
    )
);

CREATE POLICY rls_recep_insert ON receptionists FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin'));

CREATE POLICY rls_recep_update ON receptionists FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin')
    OR profile_id = rls_user_id()
);


-- ============================================================
-- patient_disease_selection
-- ============================================================

CREATE POLICY rls_pds_select ON patient_disease_selection FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR patient_id = rls_user_id()
    OR patient_id IN (
        SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id()
    )
);

CREATE POLICY rls_pds_insert ON patient_disease_selection FOR INSERT
WITH CHECK (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'receptionist')
    OR patient_id = rls_user_id()
);

CREATE POLICY rls_pds_update ON patient_disease_selection FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'receptionist')
    OR patient_id = rls_user_id()
);


-- ============================================================
-- clinic_requests
-- ============================================================

CREATE POLICY rls_creq_select ON clinic_requests FOR SELECT
USING (
    rls_user_role() = 'super_admin'
    OR (rls_user_role() = 'regional_admin' AND region_id = rls_region_id())
    OR submitted_by = rls_user_id()
);

CREATE POLICY rls_creq_insert ON clinic_requests FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin'));

CREATE POLICY rls_creq_update ON clinic_requests FOR UPDATE
USING (rls_user_role() IN ('super_admin', 'regional_admin'));


-- ============================================================
-- staff_requests
-- ============================================================

CREATE POLICY rls_sreq_select ON staff_requests FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR clinic_id = rls_clinic_id()
    OR submitted_by = rls_user_id()
);

CREATE POLICY rls_sreq_insert ON staff_requests FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin'));

CREATE POLICY rls_sreq_update ON staff_requests FOR UPDATE
USING (rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin'));


-- ============================================================
-- doctor_patient_assignments
-- ============================================================

CREATE POLICY rls_dpa_select ON doctor_patient_assignments FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR clinic_id = rls_clinic_id()
    OR doctor_id = rls_user_id()
    OR patient_id = rls_user_id()
);

CREATE POLICY rls_dpa_insert ON doctor_patient_assignments FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'clinic_admin', 'receptionist'));

CREATE POLICY rls_dpa_update ON doctor_patient_assignments FOR UPDATE
USING (rls_user_role() IN ('super_admin', 'clinic_admin', 'receptionist'));


-- ============================================================
-- assessment_protocol_requests
-- ============================================================

CREATE POLICY rls_apr_select ON assessment_protocol_requests FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR clinical_assistant_id = rls_user_id()
    OR doctor_id = rls_user_id()
    OR patient_id = rls_user_id()
    OR cycle_id IN (
        SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id()
    )
);

CREATE POLICY rls_apr_insert ON assessment_protocol_requests FOR INSERT
WITH CHECK (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'clinical_assistant')
    OR clinical_assistant_id = rls_user_id()
);

CREATE POLICY rls_apr_update ON assessment_protocol_requests FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'doctor')
    OR clinical_assistant_id = rls_user_id()
    OR doctor_id = rls_user_id()
);


-- ============================================================
-- anamnesis_assessments / anamnesis_responses
-- anamnesis_questions / anamnesis_options — reference, read-all
-- ============================================================

ALTER TABLE anamnesis_questions           ENABLE ROW LEVEL SECURITY;
ALTER TABLE anamnesis_questions           FORCE ROW LEVEL SECURITY;
ALTER TABLE anamnesis_options             ENABLE ROW LEVEL SECURITY;
ALTER TABLE anamnesis_options             FORCE ROW LEVEL SECURITY;
ALTER TABLE anamnesis_responses           ENABLE ROW LEVEL SECURITY;
ALTER TABLE anamnesis_responses           FORCE ROW LEVEL SECURITY;

CREATE POLICY rls_anaq_select ON anamnesis_questions FOR SELECT USING (TRUE);
CREATE POLICY rls_anao_select ON anamnesis_options   FOR SELECT USING (TRUE);
CREATE POLICY rls_anaq_write  ON anamnesis_questions FOR INSERT
WITH CHECK (rls_user_role() = 'super_admin');
CREATE POLICY rls_anao_write  ON anamnesis_options   FOR INSERT
WITH CHECK (rls_user_role() = 'super_admin');

CREATE POLICY rls_anamnesis_select ON anamnesis_assessments FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR patient_id = rls_user_id()
    OR patient_id IN (
        SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id()
    )
);

CREATE POLICY rls_anamnesis_insert ON anamnesis_assessments FOR INSERT
WITH CHECK (
    rls_user_role() IN ('super_admin', 'receptionist', 'clinical_assistant')
    OR patient_id = rls_user_id()
);

CREATE POLICY rls_anamnesis_update ON anamnesis_assessments FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'receptionist', 'clinical_assistant', 'doctor')
    OR patient_id = rls_user_id()
);

CREATE POLICY rls_anar_select ON anamnesis_responses FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR anamnesis_id IN (
        SELECT anamnesis_id FROM anamnesis_assessments WHERE patient_id = rls_user_id()
    )
    OR anamnesis_id IN (
        SELECT anamnesis_id FROM anamnesis_assessments
        WHERE patient_id IN (
            SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id()
        )
    )
);

CREATE POLICY rls_anar_insert ON anamnesis_responses FOR INSERT
WITH CHECK (
    rls_user_role() IN ('super_admin', 'clinical_assistant', 'doctor')
    OR anamnesis_id IN (
        SELECT anamnesis_id FROM anamnesis_assessments WHERE patient_id = rls_user_id()
    )
);

CREATE POLICY rls_anar_update ON anamnesis_responses FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'clinical_assistant', 'doctor')
    OR anamnesis_id IN (
        SELECT anamnesis_id FROM anamnesis_assessments WHERE patient_id = rls_user_id()
    )
);


-- ============================================================
-- patient_clinic_transfers
-- ============================================================

CREATE POLICY rls_pct_select ON patient_clinic_transfers FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR from_clinic_id = rls_clinic_id()
    OR to_clinic_id = rls_clinic_id()
    OR patient_id = rls_user_id()
);

CREATE POLICY rls_pct_insert ON patient_clinic_transfers FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin'));

CREATE POLICY rls_pct_update ON patient_clinic_transfers FOR UPDATE
USING (rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin'));


-- ============================================================
-- products / inventory / stock_transfers / device_assignments
-- ============================================================

CREATE POLICY rls_products_select ON products FOR SELECT USING (TRUE);
CREATE POLICY rls_products_insert ON products FOR INSERT
WITH CHECK (rls_user_role() = 'super_admin');
CREATE POLICY rls_products_update ON products FOR UPDATE
USING (rls_user_role() = 'super_admin');

CREATE POLICY rls_inventory_select ON inventory FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR clinic_id = rls_clinic_id()
);
CREATE POLICY rls_inventory_insert ON inventory FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'clinic_admin'));
CREATE POLICY rls_inventory_update ON inventory FOR UPDATE
USING (rls_user_role() IN ('super_admin', 'clinic_admin'));

CREATE POLICY rls_st_select ON stock_transfers FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR from_clinic_id = rls_clinic_id()
    OR to_clinic_id = rls_clinic_id()
);
CREATE POLICY rls_st_insert ON stock_transfers FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'clinic_admin'));
CREATE POLICY rls_st_update ON stock_transfers FOR UPDATE
USING (rls_user_role() IN ('super_admin', 'clinic_admin'));

CREATE POLICY rls_da_select ON device_assignments FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR clinic_id = rls_clinic_id()
    OR patient_id = rls_user_id()
);
CREATE POLICY rls_da_insert ON device_assignments FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'clinic_admin', 'receptionist'));
CREATE POLICY rls_da_update ON device_assignments FOR UPDATE
USING (rls_user_role() IN ('super_admin', 'clinic_admin', 'receptionist'));


-- ============================================================
-- patient_scale_assignments
-- ============================================================

CREATE POLICY rls_psa_select ON patient_scale_assignments FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR patient_id = rls_user_id()
    OR assigned_by = rls_user_id()
    OR patient_id IN (
        SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id()
    )
);
CREATE POLICY rls_psa_insert ON patient_scale_assignments FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'clinic_admin', 'doctor', 'clinical_assistant'));
CREATE POLICY rls_psa_update ON patient_scale_assignments FOR UPDATE
USING (rls_user_role() IN ('super_admin', 'clinic_admin', 'doctor'));


-- ============================================================
-- PRS reference tables (read-all + super_admin write)
-- ============================================================

ALTER TABLE prs_options              ENABLE ROW LEVEL SECURITY;
ALTER TABLE prs_options              FORCE ROW LEVEL SECURITY;
ALTER TABLE prs_disease_scale_map    ENABLE ROW LEVEL SECURITY;
ALTER TABLE prs_disease_scale_map    FORCE ROW LEVEL SECURITY;
ALTER TABLE prs_scale_question_map   ENABLE ROW LEVEL SECURITY;
ALTER TABLE prs_scale_question_map   FORCE ROW LEVEL SECURITY;
ALTER TABLE prs_disease_question_map ENABLE ROW LEVEL SECURITY;
ALTER TABLE prs_disease_question_map FORCE ROW LEVEL SECURITY;
ALTER TABLE prs_scale_results        ENABLE ROW LEVEL SECURITY;
ALTER TABLE prs_scale_results        FORCE ROW LEVEL SECURITY;
ALTER TABLE prs_final_results        ENABLE ROW LEVEL SECURITY;
ALTER TABLE prs_final_results        FORCE ROW LEVEL SECURITY;

CREATE POLICY rls_prs_opts_select   ON prs_options              FOR SELECT USING (TRUE);
CREATE POLICY rls_prs_dsmap_select  ON prs_disease_scale_map    FOR SELECT USING (TRUE);
CREATE POLICY rls_prs_sqmap_select  ON prs_scale_question_map   FOR SELECT USING (TRUE);
CREATE POLICY rls_prs_dqmap_select  ON prs_disease_question_map FOR SELECT USING (TRUE);

CREATE POLICY rls_prs_opts_write    ON prs_options              FOR INSERT WITH CHECK (rls_user_role() = 'super_admin');
CREATE POLICY rls_prs_dsmap_write   ON prs_disease_scale_map    FOR INSERT WITH CHECK (rls_user_role() = 'super_admin');
CREATE POLICY rls_prs_sqmap_write   ON prs_scale_question_map   FOR INSERT WITH CHECK (rls_user_role() = 'super_admin');
CREATE POLICY rls_prs_dqmap_write   ON prs_disease_question_map FOR INSERT WITH CHECK (rls_user_role() = 'super_admin');

CREATE POLICY rls_psr_select ON prs_scale_results FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR instance_id IN (
        SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id()
    )
    OR instance_id IN (
        SELECT instance_id FROM prs_assessment_instances
        WHERE cycle_id IN (
            SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id()
        )
    )
);

-- prs_scale_results written by recalculate_final_result trigger (SECURITY DEFINER)
-- and by scoring engine (clinical assistant / doctor role).
CREATE POLICY rls_psr_insert ON prs_scale_results FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'clinic_admin', 'clinical_assistant', 'doctor'));

CREATE POLICY rls_psr_update ON prs_scale_results FOR UPDATE
USING (rls_user_role() IN ('super_admin', 'doctor'));

CREATE POLICY rls_pfr_select ON prs_final_results FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR instance_id IN (
        SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id()
    )
    OR instance_id IN (
        SELECT instance_id FROM prs_assessment_instances
        WHERE cycle_id IN (
            SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id()
        )
    )
);

-- prs_final_results upserted by recalculate_final_result trigger (SECURITY DEFINER).
-- Also allow super_admin for manual correction.
CREATE POLICY rls_pfr_insert ON prs_final_results FOR INSERT
WITH CHECK (rls_user_role() = 'super_admin');

CREATE POLICY rls_pfr_update ON prs_final_results FOR UPDATE
USING (rls_user_role() = 'super_admin');


-- ============================================================
-- doctor_session_notes
-- ============================================================

ALTER TABLE doctor_session_notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE doctor_session_notes FORCE ROW LEVEL SECURITY;

CREATE POLICY rls_dsn_select ON doctor_session_notes FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR doctor_id = rls_user_id()
    OR patient_id = rls_user_id()
    OR cycle_id IN (
        SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id()
    )
);

CREATE POLICY rls_dsn_insert ON doctor_session_notes FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'doctor') OR doctor_id = rls_user_id());

CREATE POLICY rls_dsn_update ON doctor_session_notes FOR UPDATE
USING (doctor_id = rls_user_id() OR rls_user_role() = 'super_admin');


-- ============================================================
-- patient_eeg_files / patient_medical_history_files
-- ============================================================

ALTER TABLE patient_eeg_files             ENABLE ROW LEVEL SECURITY;
ALTER TABLE patient_eeg_files             FORCE ROW LEVEL SECURITY;
ALTER TABLE patient_medical_history_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE patient_medical_history_files FORCE ROW LEVEL SECURITY;

CREATE POLICY rls_eeg_select ON patient_eeg_files FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR clinic_id = rls_clinic_id()
    OR patient_id = rls_user_id()
);
CREATE POLICY rls_eeg_insert ON patient_eeg_files FOR INSERT
WITH CHECK (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'clinical_assistant')
    OR clinic_id = rls_clinic_id()
);
CREATE POLICY rls_eeg_update ON patient_eeg_files FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'clinical_assistant', 'doctor')
    OR clinic_id = rls_clinic_id()
);

CREATE POLICY rls_mhf_select ON patient_medical_history_files FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR clinic_id = rls_clinic_id()
    OR patient_id = rls_user_id()
);
CREATE POLICY rls_mhf_insert ON patient_medical_history_files FOR INSERT
WITH CHECK (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'receptionist', 'clinical_assistant')
    OR patient_id = rls_user_id()
    OR clinic_id = rls_clinic_id()
);
CREATE POLICY rls_mhf_update ON patient_medical_history_files FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'clinic_admin')
    OR clinic_id = rls_clinic_id()
);


-- ============================================================
-- notifications
-- ============================================================

ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications FORCE ROW LEVEL SECURITY;

CREATE POLICY rls_notif_select ON notifications FOR SELECT
USING (
    recipient_id = rls_user_id()
    OR rls_user_role() IN ('super_admin', 'regional_admin')
    OR (rls_user_role() = 'clinic_admin' AND clinic_id = rls_clinic_id())
);
CREATE POLICY rls_notif_insert ON notifications FOR INSERT
WITH CHECK (
    rls_user_role() IN (
        'super_admin', 'regional_admin', 'clinic_admin',
        'doctor', 'clinical_assistant', 'receptionist'
    )
);
-- only recipient toggles is_read; admins can update for bulk operations
CREATE POLICY rls_notif_update ON notifications FOR UPDATE
USING (
    recipient_id = rls_user_id()
    OR rls_user_role() IN ('super_admin', 'clinic_admin')
);


-- ============================================================
-- 06b: Appointment Scheduling Tables
-- ============================================================

ALTER TABLE doctor_weekly_schedules    ENABLE ROW LEVEL SECURITY;
ALTER TABLE doctor_weekly_schedules    FORCE ROW LEVEL SECURITY;
ALTER TABLE doctor_schedule_overrides  ENABLE ROW LEVEL SECURITY;
ALTER TABLE doctor_schedule_overrides  FORCE ROW LEVEL SECURITY;
ALTER TABLE appointment_requests       ENABLE ROW LEVEL SECURITY;
ALTER TABLE appointment_requests       FORCE ROW LEVEL SECURITY;
ALTER TABLE appointments               ENABLE ROW LEVEL SECURITY;
ALTER TABLE appointments               FORCE ROW LEVEL SECURITY;
ALTER TABLE appointment_audit_logs     ENABLE ROW LEVEL SECURITY;
ALTER TABLE appointment_audit_logs     FORCE ROW LEVEL SECURITY;

-- ============================================================
-- doctor_weekly_schedules
-- Doctors manage own schedule; clinic staff read for slot display
-- ============================================================

CREATE POLICY rls_dws_select ON doctor_weekly_schedules FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR clinic_id = rls_clinic_id()
    OR doctor_id = rls_user_id()
);

CREATE POLICY rls_dws_insert ON doctor_weekly_schedules FOR INSERT
WITH CHECK (
    rls_user_role() IN ('super_admin', 'clinic_admin')
    OR doctor_id = rls_user_id()
);

CREATE POLICY rls_dws_update ON doctor_weekly_schedules FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'clinic_admin')
    OR doctor_id = rls_user_id()
);

CREATE POLICY rls_dws_delete ON doctor_weekly_schedules FOR DELETE
USING (
    rls_user_role() IN ('super_admin', 'clinic_admin')
    OR doctor_id = rls_user_id()
);

-- ============================================================
-- doctor_schedule_overrides
-- ============================================================

CREATE POLICY rls_dso_select ON doctor_schedule_overrides FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR clinic_id = rls_clinic_id()
    OR doctor_id = rls_user_id()
);

CREATE POLICY rls_dso_insert ON doctor_schedule_overrides FOR INSERT
WITH CHECK (
    rls_user_role() IN ('super_admin', 'clinic_admin')
    OR doctor_id = rls_user_id()
);

CREATE POLICY rls_dso_update ON doctor_schedule_overrides FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'clinic_admin')
    OR doctor_id = rls_user_id()
);

CREATE POLICY rls_dso_delete ON doctor_schedule_overrides FOR DELETE
USING (
    rls_user_role() IN ('super_admin', 'clinic_admin')
    OR doctor_id = rls_user_id()
);

-- ============================================================
-- appointment_requests
-- Patients create own requests; clinic staff approve/reject
-- ============================================================

CREATE POLICY rls_areq_select ON appointment_requests FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR clinic_id = rls_clinic_id()
    OR patient_id = rls_user_id()
);

CREATE POLICY rls_areq_insert ON appointment_requests FOR INSERT
WITH CHECK (
    rls_user_role() IN (
        'super_admin', 'clinic_admin', 'doctor',
        'clinical_assistant', 'receptionist', 'patient'
    )
    OR patient_id = rls_user_id()
);

CREATE POLICY rls_areq_update ON appointment_requests FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'clinic_admin', 'doctor', 'receptionist')
    OR clinic_id = rls_clinic_id()
    OR patient_id = rls_user_id()
);

-- ============================================================
-- appointments
-- Patients see own; clinic staff see own clinic's; admins see all
-- ============================================================

CREATE POLICY rls_appt_select ON appointments FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR clinic_id = rls_clinic_id()
    OR patient_id = rls_user_id()
    OR doctor_id = rls_user_id()
    OR ca_id     = rls_user_id()
);

CREATE POLICY rls_appt_insert ON appointments FOR INSERT
WITH CHECK (
    rls_user_role() IN (
        'super_admin', 'clinic_admin', 'doctor',
        'clinical_assistant', 'receptionist'
    )
    OR clinic_id = rls_clinic_id()
);

CREATE POLICY rls_appt_update ON appointments FOR UPDATE
USING (
    rls_user_role() IN (
        'super_admin', 'clinic_admin', 'doctor',
        'clinical_assistant', 'receptionist'
    )
    OR clinic_id = rls_clinic_id()
);

-- ============================================================
-- appointment_audit_logs
-- Read: clinic staff and own patient; Write: app layer only (no direct)
-- ============================================================

CREATE POLICY rls_apal_select ON appointment_audit_logs FOR SELECT
USING (
    rls_user_role() IN ('super_admin', 'regional_admin')
    OR appointment_id IN (
        SELECT appointment_id FROM appointments
        WHERE clinic_id = rls_clinic_id()
           OR patient_id = rls_user_id()
    )
);

CREATE POLICY rls_apal_insert ON appointment_audit_logs FOR INSERT
WITH CHECK (
    rls_user_role() IN (
        'super_admin', 'clinic_admin', 'doctor',
        'clinical_assistant', 'receptionist'
    )
);
