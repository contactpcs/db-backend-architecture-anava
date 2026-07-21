-- Generated from live production schema introspection (2026-07-20). Do not hand-edit column/RLS/trigger/function bodies — regenerate from source instead.

CREATE POLICY "rls_actlog_insert" ON compliance."activity_logs" FOR INSERT TO public
    WITH CHECK (true);

CREATE POLICY "rls_actlog_select" ON compliance."activity_logs" FOR SELECT TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_admins_insert" ON core."admins" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_admins_select" ON core."admins" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (region_id = rls_region_id())) OR ((rls_user_role() = 'regional_admin'::text) AND (clinic_id IN ( SELECT clinics.clinic_id
   FROM clinics
  WHERE (clinics.region_id = rls_region_id())))) OR (profile_id = rls_user_id())));

CREATE POLICY "rls_admins_update" ON core."admins" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_anamnesis_insert" ON core."anamnesis_assessments" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'receptionist'::text, 'clinical_assistant'::text])) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_anamnesis_select" ON core."anamnesis_assessments" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (patient_id = rls_user_id()) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (patient_id IN ( SELECT patients.profile_id
   FROM patients
  WHERE (patients.primary_clinic_id = rls_clinic_id()))))));

CREATE POLICY "rls_anamnesis_update" ON core."anamnesis_assessments" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_anao_select" ON reference."anamnesis_options" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_anao_write" ON reference."anamnesis_options" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_anaq_select" ON reference."anamnesis_questions" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_anaq_write" ON reference."anamnesis_questions" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_anar_insert" ON core."anamnesis_responses" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (anamnesis_id IN ( SELECT anamnesis_assessments.anamnesis_id
   FROM anamnesis_assessments
  WHERE (anamnesis_assessments.patient_id = rls_user_id())))));

CREATE POLICY "rls_anar_select" ON core."anamnesis_responses" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (anamnesis_id IN ( SELECT anamnesis_assessments.anamnesis_id
   FROM anamnesis_assessments
  WHERE (anamnesis_assessments.patient_id = rls_user_id()))) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (anamnesis_id IN ( SELECT anamnesis_assessments.anamnesis_id
   FROM anamnesis_assessments
  WHERE (anamnesis_assessments.patient_id IN ( SELECT patients.profile_id
           FROM patients
          WHERE (patients.primary_clinic_id = rls_clinic_id()))))))));

CREATE POLICY "rls_anar_update" ON core."anamnesis_responses" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (anamnesis_id IN ( SELECT anamnesis_assessments.anamnesis_id
   FROM anamnesis_assessments
  WHERE (anamnesis_assessments.patient_id = rls_user_id())))));

CREATE POLICY "rls_apal_insert" ON core."appointment_audit_logs" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])));

CREATE POLICY "rls_apal_select" ON core."appointment_audit_logs" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (appointment_id IN ( SELECT appointments.appointment_id
   FROM appointments
  WHERE ((appointments.clinic_id = rls_clinic_id()) OR (appointments.patient_id = rls_user_id()))))));

CREATE POLICY "rls_areq_insert" ON core."appointment_requests" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text, 'patient'::text])) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_areq_select" ON core."appointment_requests" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_areq_update" ON core."appointment_requests" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'receptionist'::text])) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_appt_insert" ON core."appointments" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) OR (clinic_id = rls_clinic_id())));

CREATE POLICY "rls_appt_select" ON core."appointments" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id()) OR (doctor_id = rls_user_id()) OR (ca_id = rls_user_id())));

CREATE POLICY "rls_appt_update" ON core."appointments" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) OR (clinic_id = rls_clinic_id())));

CREATE POLICY "rls_apr_insert" ON core."assessment_protocol_requests" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text])) OR (clinical_assistant_id = rls_user_id())));

CREATE POLICY "rls_apr_select" ON core."assessment_protocol_requests" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinical_assistant_id = rls_user_id()) OR (doctor_id = rls_user_id()) OR (patient_id = rls_user_id()) OR (cycle_id IN ( SELECT treatment_cycles.cycle_id
   FROM treatment_cycles
  WHERE (treatment_cycles.clinic_id = rls_clinic_id())))));

CREATE POLICY "rls_apr_update" ON core."assessment_protocol_requests" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text])) OR (clinical_assistant_id = rls_user_id()) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_audit_select" ON compliance."audit_logs" FOR SELECT TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_creq_insert" ON core."clinic_requests" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_creq_select" ON core."clinic_requests" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (region_id = rls_region_id())) OR (submitted_by = rls_user_id())));

CREATE POLICY "rls_creq_update" ON core."clinic_requests" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])));

CREATE POLICY "rls_csa_insert" ON core."clinic_staff_assignments" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (clinic_id IN ( SELECT clinics.clinic_id
   FROM clinics
  WHERE (clinics.region_id = rls_region_id())))) OR ((rls_user_role() = 'clinic_admin'::text) AND (clinic_id = rls_clinic_id()))));

CREATE POLICY "rls_csa_select" ON core."clinic_staff_assignments" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (clinic_id IN ( SELECT clinics.clinic_id
   FROM clinics
  WHERE (clinics.region_id = rls_region_id())))) OR (clinic_id = rls_clinic_id()) OR (profile_id = rls_user_id())));

CREATE POLICY "rls_csa_update" ON core."clinic_staff_assignments" FOR UPDATE TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (clinic_id IN ( SELECT clinics.clinic_id
   FROM clinics
  WHERE (clinics.region_id = rls_region_id())))) OR ((rls_user_role() = 'clinic_admin'::text) AND (clinic_id = rls_clinic_id()))));

CREATE POLICY "rls_ca_insert" ON core."clinical_assistants" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_ca_select" ON core."clinical_assistants" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (profile_id = rls_user_id()) OR (profile_id IN ( SELECT clinic_staff_assignments.profile_id
   FROM clinic_staff_assignments
  WHERE ((clinic_staff_assignments.clinic_id = rls_clinic_id()) AND (clinic_staff_assignments.is_active = true))))));

CREATE POLICY "rls_ca_update" ON core."clinical_assistants" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])) OR (profile_id = rls_user_id())));

CREATE POLICY "rls_clinics_insert" ON core."clinics" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_clinics_select" ON core."clinics" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (region_id = rls_region_id())) OR (clinic_id = rls_clinic_id()) OR (status <> ALL (ARRAY['pending_closure'::text, 'closed'::text]))));

CREATE POLICY "rls_clinics_update" ON core."clinics" FOR UPDATE TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (region_id = rls_region_id())) OR ((rls_user_role() = 'clinic_admin'::text) AND (clinic_id = rls_clinic_id()))));

CREATE POLICY "rls_cr_insert" ON compliance."consent_records" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text, 'receptionist'::text])) OR (rls_user_role() IS NULL)));

CREATE POLICY "rls_cr_select" ON compliance."consent_records" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND ((region_id = rls_region_id()) OR (clinic_id IN ( SELECT clinics.clinic_id
   FROM clinics
  WHERE (clinics.region_id = rls_region_id()))))) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (clinic_id = rls_clinic_id())) OR (patient_id = rls_user_id()) OR (staff_id = rls_user_id())));

CREATE POLICY "rls_cr_update" ON compliance."consent_records" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])) OR (patient_id = rls_user_id()) OR (staff_id = rls_user_id())));

CREATE POLICY "rls_ct_insert" ON reference."consent_templates" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_ct_select" ON reference."consent_templates" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_ct_update" ON reference."consent_templates" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_da_insert" ON core."device_assignments" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])));

CREATE POLICY "rls_da_select" ON core."device_assignments" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_da_update" ON core."device_assignments" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])));

CREATE POLICY "rls_dpa_insert" ON core."doctor_patient_assignments" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_dpa_select" ON core."doctor_patient_assignments" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (doctor_id = rls_user_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_dpa_update" ON core."doctor_patient_assignments" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])));

CREATE POLICY "rls_dso_delete" ON core."doctor_schedule_overrides" FOR DELETE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_dso_insert" ON core."doctor_schedule_overrides" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_dso_select" ON core."doctor_schedule_overrides" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_dso_update" ON core."doctor_schedule_overrides" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_dsn_insert" ON core."doctor_session_notes" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'doctor'::text])) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_dsn_select" ON core."doctor_session_notes" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (doctor_id = rls_user_id()) OR (patient_id = rls_user_id()) OR (cycle_id IN ( SELECT treatment_cycles.cycle_id
   FROM treatment_cycles
  WHERE (treatment_cycles.clinic_id = rls_clinic_id())))));

CREATE POLICY "rls_dsn_update" ON core."doctor_session_notes" FOR UPDATE TO public
    USING (((doctor_id = rls_user_id()) OR (rls_user_role() = 'super_admin'::text)));

CREATE POLICY "rls_dws_delete" ON core."doctor_weekly_schedules" FOR DELETE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_dws_insert" ON core."doctor_weekly_schedules" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_dws_select" ON core."doctor_weekly_schedules" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_dws_update" ON core."doctor_weekly_schedules" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_doctors_insert" ON core."doctors" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_doctors_select" ON core."doctors" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (profile_id = rls_user_id()) OR (profile_id IN ( SELECT clinic_staff_assignments.profile_id
   FROM clinic_staff_assignments
  WHERE ((clinic_staff_assignments.clinic_id = rls_clinic_id()) AND (clinic_staff_assignments.is_active = true))))));

CREATE POLICY "rls_doctors_update" ON core."doctors" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])) OR (profile_id = rls_user_id())));

CREATE POLICY "rls_inventory_insert" ON core."inventory" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_inventory_select" ON core."inventory" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id())));

CREATE POLICY "rls_inventory_update" ON core."inventory" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_notif_insert" ON core."notifications" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])));

CREATE POLICY "rls_notif_select" ON core."notifications" FOR SELECT TO public
    USING (((recipient_id = rls_user_id()) OR (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR ((rls_user_role() = 'clinic_admin'::text) AND (clinic_id = rls_clinic_id()))));

CREATE POLICY "rls_notif_update" ON core."notifications" FOR UPDATE TO public
    USING (((recipient_id = rls_user_id()) OR (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text]))));

CREATE POLICY "rls_oi_select" ON core."order_items" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (order_id IN ( SELECT store_orders.order_id
   FROM store_orders
  WHERE (store_orders.clinic_id = rls_clinic_id())))));

CREATE POLICY "rls_pct_insert" ON core."patient_clinic_transfers" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_pct_select" ON core."patient_clinic_transfers" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (from_clinic_id = rls_clinic_id()) OR (to_clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_pct_update" ON core."patient_clinic_transfers" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_pds_insert" ON core."patient_disease_selection" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_pds_select" ON core."patient_disease_selection" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (patient_id = rls_user_id()) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (patient_id IN ( SELECT patients.profile_id
   FROM patients
  WHERE (patients.primary_clinic_id = rls_clinic_id()))))));

CREATE POLICY "rls_pds_update" ON core."patient_disease_selection" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_eeg_insert" ON core."patient_eeg_files" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text])) OR (clinic_id = rls_clinic_id())));

CREATE POLICY "rls_eeg_select" ON core."patient_eeg_files" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_eeg_update" ON core."patient_eeg_files" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (clinic_id = rls_clinic_id())));

CREATE POLICY "rls_mhf_insert" ON core."patient_medical_history_files" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text, 'clinical_assistant'::text])) OR (patient_id = rls_user_id()) OR (clinic_id = rls_clinic_id())));

CREATE POLICY "rls_mhf_select" ON core."patient_medical_history_files" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_mhf_update" ON core."patient_medical_history_files" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])) OR (clinic_id = rls_clinic_id())));

CREATE POLICY "rls_psa_insert" ON core."patient_scale_assignments" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'patient'::text])));

CREATE POLICY "rls_psa_select" ON core."patient_scale_assignments" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (patient_id = rls_user_id()) OR (assigned_by = rls_user_id()) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (patient_id IN ( SELECT patients.profile_id
   FROM patients
  WHERE (patients.primary_clinic_id = rls_clinic_id()))))));

CREATE POLICY "rls_psa_update" ON core."patient_scale_assignments" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text])));

CREATE POLICY "rls_patients_insert" ON core."patients" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])) OR (rls_user_role() IS NULL)));

CREATE POLICY "rls_patients_select" ON core."patients" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (primary_clinic_id IN ( SELECT clinics.clinic_id
   FROM clinics
  WHERE (clinics.region_id = rls_region_id())))) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (primary_clinic_id = rls_clinic_id())) OR (profile_id = rls_user_id()) OR ((rls_user_role() = 'doctor'::text) AND (primary_doctor_id = rls_user_id()))));

CREATE POLICY "rls_patients_update" ON core."patients" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text, 'clinical_assistant'::text])) OR (profile_id = rls_user_id())));

CREATE POLICY "rls_payments_insert" ON core."payments" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])));

CREATE POLICY "rls_payments_select" ON core."payments" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])) OR (session_id IN ( SELECT sessions.session_id
   FROM sessions
  WHERE (sessions.clinic_id = rls_clinic_id()))) OR (order_id IN ( SELECT store_orders.order_id
   FROM store_orders
  WHERE (store_orders.clinic_id = rls_clinic_id())))));

CREATE POLICY "rls_payments_update" ON core."payments" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_products_insert" ON reference."products" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_products_select" ON reference."products" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_products_update" ON reference."products" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_profiles_insert" ON core."profiles" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text, 'receptionist'::text, 'patient'::text])) OR ((rls_user_role() IS NULL) AND (role = 'patient'::text))));

CREATE POLICY "rls_profiles_select" ON core."profiles" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR (rls_user_role() = 'regional_admin'::text) OR (id = rls_user_id()) OR (cognito_sub = rls_cognito_sub()) OR (email = rls_email()) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (id IN ( SELECT clinic_staff_assignments.profile_id
   FROM clinic_staff_assignments
  WHERE ((clinic_staff_assignments.clinic_id = rls_clinic_id()) AND (clinic_staff_assignments.is_active = true))
UNION
 SELECT patients.profile_id
   FROM patients
  WHERE (patients.primary_clinic_id = rls_clinic_id())))) OR ((rls_user_role() = 'patient'::text) AND (id IN ( SELECT clinic_staff_assignments.profile_id
   FROM clinic_staff_assignments
  WHERE ((clinic_staff_assignments.clinic_id = rls_clinic_id()) AND (clinic_staff_assignments.is_active = true)))))));

CREATE POLICY "rls_profiles_update" ON core."profiles" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])) OR (id = rls_user_id()) OR ((rls_user_role() = ANY (ARRAY['receptionist'::text, 'clinical_assistant'::text])) AND (id IN ( SELECT patients.profile_id
   FROM patients
  WHERE (patients.primary_clinic_id = rls_clinic_id()))))));

CREATE POLICY "rls_pai_insert" ON core."prs_assessment_instances" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'receptionist'::text, 'doctor'::text])) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_pai_select" ON core."prs_assessment_instances" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (patient_id = rls_user_id()) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND ((patient_id IN ( SELECT patients.profile_id
   FROM patients
  WHERE (patients.primary_clinic_id = rls_clinic_id()))) OR (cycle_id IN ( SELECT treatment_cycles.cycle_id
   FROM treatment_cycles
  WHERE (treatment_cycles.clinic_id = rls_clinic_id())))))));

CREATE POLICY "rls_pai_update" ON core."prs_assessment_instances" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_prs_dqmap_select" ON reference."prs_disease_question_map" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_prs_dqmap_write" ON reference."prs_disease_question_map" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_prs_dsmap_select" ON reference."prs_disease_scale_map" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_prs_dsmap_write" ON reference."prs_disease_scale_map" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_prs_diseases_select" ON reference."prs_diseases" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_prs_diseases_write" ON reference."prs_diseases" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_pfr_insert" ON core."prs_final_results" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id())))));

CREATE POLICY "rls_pfr_select" ON core."prs_final_results" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id()))) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE ((prs_assessment_instances.patient_id IN ( SELECT patients.profile_id
           FROM patients
          WHERE (patients.primary_clinic_id = rls_clinic_id()))) OR (prs_assessment_instances.cycle_id IN ( SELECT treatment_cycles.cycle_id
           FROM treatment_cycles
          WHERE (treatment_cycles.clinic_id = rls_clinic_id())))))))));

CREATE POLICY "rls_pfr_update" ON core."prs_final_results" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id())))));

CREATE POLICY "rls_prs_opts_select" ON reference."prs_options" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_prs_opts_write" ON reference."prs_options" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_prs_questions_select" ON reference."prs_questions" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_prs_questions_write" ON reference."prs_questions" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_prs_resp_insert" ON core."prs_responses" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id())))));

CREATE POLICY "rls_prs_resp_select" ON core."prs_responses" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id()))) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE ((prs_assessment_instances.patient_id IN ( SELECT patients.profile_id
           FROM patients
          WHERE (patients.primary_clinic_id = rls_clinic_id()))) OR (prs_assessment_instances.cycle_id IN ( SELECT treatment_cycles.cycle_id
           FROM treatment_cycles
          WHERE (treatment_cycles.clinic_id = rls_clinic_id())))))))));

CREATE POLICY "rls_prs_resp_update" ON core."prs_responses" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id())))));

CREATE POLICY "rls_prs_sqmap_select" ON reference."prs_scale_question_map" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_prs_sqmap_write" ON reference."prs_scale_question_map" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_psr_insert" ON core."prs_scale_results" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id())))));

CREATE POLICY "rls_psr_select" ON core."prs_scale_results" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id()))) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE ((prs_assessment_instances.patient_id IN ( SELECT patients.profile_id
           FROM patients
          WHERE (patients.primary_clinic_id = rls_clinic_id()))) OR (prs_assessment_instances.cycle_id IN ( SELECT treatment_cycles.cycle_id
           FROM treatment_cycles
          WHERE (treatment_cycles.clinic_id = rls_clinic_id())))))))));

CREATE POLICY "rls_psr_update" ON core."prs_scale_results" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'doctor'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id())))));

CREATE POLICY "rls_prs_scales_select" ON reference."prs_scales" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_prs_scales_write" ON reference."prs_scales" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_recep_insert" ON core."receptionists" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_recep_select" ON core."receptionists" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (profile_id = rls_user_id()) OR (profile_id IN ( SELECT clinic_staff_assignments.profile_id
   FROM clinic_staff_assignments
  WHERE ((clinic_staff_assignments.clinic_id = rls_clinic_id()) AND (clinic_staff_assignments.is_active = true))))));

CREATE POLICY "rls_recep_update" ON core."receptionists" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])) OR (profile_id = rls_user_id())));

CREATE POLICY "rls_regions_insert" ON core."regions" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_regions_select" ON core."regions" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR (region_id = rls_region_id()) OR (is_active = true)));

CREATE POLICY "rls_regions_update" ON core."regions" FOR UPDATE TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (region_id = rls_region_id()))));

CREATE POLICY "rls_sessions_insert" ON core."sessions" FOR INSERT TO public
    WITH CHECK (((clinic_id = rls_clinic_id()) OR (rls_user_role() = 'super_admin'::text)));

CREATE POLICY "rls_sessions_select" ON core."sessions" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (clinic_id IN ( SELECT clinics.clinic_id
   FROM clinics
  WHERE (clinics.region_id = rls_region_id())))) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_sessions_update" ON core."sessions" FOR UPDATE TO public
    USING (((clinic_id = rls_clinic_id()) OR (rls_user_role() = 'super_admin'::text)));

CREATE POLICY "rls_sreq_insert" ON core."staff_requests" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_sreq_select" ON core."staff_requests" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (submitted_by = rls_user_id())));

CREATE POLICY "rls_sreq_update" ON core."staff_requests" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_st_insert" ON core."stock_transfers" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_st_select" ON core."stock_transfers" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (from_clinic_id = rls_clinic_id()) OR (to_clinic_id = rls_clinic_id())));

CREATE POLICY "rls_st_update" ON core."stock_transfers" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_so_insert" ON core."store_orders" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])));

CREATE POLICY "rls_so_select" ON core."store_orders" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_so_update" ON core."store_orders" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'receptionist'::text])));

CREATE POLICY "rls_cycles_insert" ON core."treatment_cycles" FOR INSERT TO public
    WITH CHECK (((clinic_id = rls_clinic_id()) OR (rls_user_role() = 'super_admin'::text)));

CREATE POLICY "rls_cycles_select" ON core."treatment_cycles" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (clinic_id IN ( SELECT clinics.clinic_id
   FROM clinics
  WHERE (clinics.region_id = rls_region_id())))) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_cycles_update" ON core."treatment_cycles" FOR UPDATE TO public
    USING (((clinic_id = rls_clinic_id()) OR (rls_user_role() = 'super_admin'::text)));

CREATE POLICY "rls_tp_insert" ON core."treatment_plans" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'doctor'::text) AND (doctor_id = rls_user_id()) AND (cycle_id IN ( SELECT treatment_cycles.cycle_id
   FROM treatment_cycles
  WHERE (treatment_cycles.clinic_id = rls_clinic_id()))))));

CREATE POLICY "rls_tp_select" ON core."treatment_plans" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR (patient_id = rls_user_id()) OR (doctor_id = rls_user_id()) OR (cycle_id IN ( SELECT treatment_cycles.cycle_id
   FROM treatment_cycles
  WHERE (treatment_cycles.clinic_id = rls_clinic_id())))));

CREATE POLICY "rls_tp_update" ON core."treatment_plans" FOR UPDATE TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'doctor'::text) AND (doctor_id = rls_user_id()))));

CREATE POLICY "rls_ts_insert" ON core."treatment_sessions" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'clinical_assistant'::text) AND (ca_id = rls_user_id())) OR ((rls_user_role() = 'clinic_admin'::text) AND (plan_id IN ( SELECT tp.plan_id
   FROM (treatment_plans tp
     JOIN treatment_cycles tc ON ((tc.cycle_id = tp.cycle_id)))
  WHERE (tc.clinic_id = rls_clinic_id()))))));

CREATE POLICY "rls_ts_select" ON core."treatment_sessions" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])) OR (ca_id = rls_user_id()) OR (patient_id = rls_user_id()) OR (plan_id IN ( SELECT treatment_plans.plan_id
   FROM treatment_plans
  WHERE (treatment_plans.doctor_id = rls_user_id())))));

CREATE POLICY "rls_ts_update" ON core."treatment_sessions" FOR UPDATE TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'clinical_assistant'::text) AND (ca_id = rls_user_id())) OR ((rls_user_role() = 'clinic_admin'::text) AND (plan_id IN ( SELECT tp.plan_id
   FROM (treatment_plans tp
     JOIN treatment_cycles tc ON ((tc.cycle_id = tp.cycle_id)))
  WHERE (tc.clinic_id = rls_clinic_id()))))));
