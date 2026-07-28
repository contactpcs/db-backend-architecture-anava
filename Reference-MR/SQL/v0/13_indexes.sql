-- ============================================================
-- Anava Clinic — DB Schema
-- File 13: Indexes
-- FK indexes (PostgreSQL does NOT auto-index FK columns),
-- status/filter indexes, date range indexes
-- ============================================================

-- ------------------------------------------------------------
-- profiles
-- ------------------------------------------------------------
CREATE INDEX idx_profiles_role       ON profiles (role);
CREATE INDEX idx_profiles_is_active  ON profiles (is_active);
-- cognito_sub and email are UNIQUE — auto-indexed

-- ------------------------------------------------------------
-- regions
-- ------------------------------------------------------------
CREATE INDEX idx_regions_regional_admin ON regions (regional_admin_id);
CREATE INDEX idx_regions_is_active      ON regions (is_active);

-- ------------------------------------------------------------
-- clinics
-- ------------------------------------------------------------
CREATE INDEX idx_clinics_region_id      ON clinics (region_id);
CREATE INDEX idx_clinics_status         ON clinics (status);
CREATE INDEX idx_clinics_clinic_admin   ON clinics (clinic_admin_id);
CREATE INDEX idx_clinics_is_main_branch ON clinics (is_main_branch);

-- ------------------------------------------------------------
-- admins
-- ------------------------------------------------------------
CREATE INDEX idx_admins_region_id  ON admins (region_id);
CREATE INDEX idx_admins_clinic_id  ON admins (clinic_id);
CREATE INDEX idx_admins_admin_type ON admins (admin_type);

-- ------------------------------------------------------------
-- clinic_staff_assignments
-- (UNIQUE(clinic_id, profile_id) auto-indexed by the constraint)
-- Partial index for active assignments covers 99% of queries.
-- ------------------------------------------------------------
CREATE INDEX idx_csa_clinic_id   ON clinic_staff_assignments (clinic_id);
CREATE INDEX idx_csa_profile_id  ON clinic_staff_assignments (profile_id);
CREATE INDEX idx_csa_is_active   ON clinic_staff_assignments (is_active);
CREATE INDEX idx_csa_staff_role  ON clinic_staff_assignments (staff_role);
-- Covers "list active staff at clinic" — most common lookup
CREATE INDEX idx_csa_active ON clinic_staff_assignments (clinic_id, profile_id)
    WHERE is_active = TRUE;

-- ------------------------------------------------------------
-- doctors
-- (active_patient_count column removed — use v_doctor_active_patient_counts view)
-- ------------------------------------------------------------
CREATE INDEX idx_doctors_profile_id           ON doctors (profile_id);
CREATE INDEX idx_doctors_availability_status  ON doctors (availability_status);

-- ------------------------------------------------------------
-- clinical_assistants
-- ------------------------------------------------------------
CREATE INDEX idx_ca_profile_id ON clinical_assistants (profile_id);
-- supervising_doctor_id index removed: column dropped in 03_staff_role_tables.sql
-- (replaced by ca_doctor_assignments junction table). Use idx_cda_ca_id /
-- idx_cda_doctor_id below for CA<->Doctor lookups instead.

-- ------------------------------------------------------------
-- ca_doctor_assignments
-- (UNIQUE(ca_id, doctor_id) auto-indexed by the constraint)
-- ------------------------------------------------------------
CREATE INDEX idx_cda_ca_id     ON ca_doctor_assignments (ca_id);
CREATE INDEX idx_cda_doctor_id ON ca_doctor_assignments (doctor_id);
CREATE INDEX idx_cda_clinic_id ON ca_doctor_assignments (clinic_id);

-- ------------------------------------------------------------
-- patients
-- ------------------------------------------------------------
CREATE INDEX idx_patients_profile_id           ON patients (profile_id);
CREATE INDEX idx_patients_registration_status  ON patients (registration_status);
CREATE INDEX idx_patients_primary_clinic_id    ON patients (primary_clinic_id);
CREATE INDEX idx_patients_primary_doctor_id    ON patients (primary_doctor_id);

-- ------------------------------------------------------------
-- patient_disease_selection
-- ------------------------------------------------------------
CREATE INDEX idx_pds_patient_id  ON patient_disease_selection (patient_id);
CREATE INDEX idx_pds_disease_id  ON patient_disease_selection (disease_id);

-- ------------------------------------------------------------
-- clinic_requests
-- ------------------------------------------------------------
CREATE INDEX idx_clinic_req_region_id    ON clinic_requests (region_id);
CREATE INDEX idx_clinic_req_clinic_id    ON clinic_requests (clinic_id);
CREATE INDEX idx_clinic_req_submitted_by ON clinic_requests (submitted_by);
CREATE INDEX idx_clinic_req_status       ON clinic_requests (status);
-- Pending requests dashboard (most frequent admin query)
CREATE INDEX idx_clinic_req_pending ON clinic_requests (region_id, created_at DESC)
    WHERE status = 'pending';
-- GIN for payload search
CREATE INDEX idx_clinic_req_payload_gin ON clinic_requests USING GIN (payload);

-- ------------------------------------------------------------
-- staff_requests
-- ------------------------------------------------------------
CREATE INDEX idx_staff_req_clinic_id     ON staff_requests (clinic_id);
CREATE INDEX idx_staff_req_submitted_by  ON staff_requests (submitted_by);
CREATE INDEX idx_staff_req_status        ON staff_requests (status);
CREATE INDEX idx_staff_req_position_role ON staff_requests (position_role);

-- ------------------------------------------------------------
-- doctor_patient_assignments
-- ------------------------------------------------------------
CREATE INDEX idx_dpa_doctor_id   ON doctor_patient_assignments (doctor_id);
CREATE INDEX idx_dpa_patient_id  ON doctor_patient_assignments (patient_id);
CREATE INDEX idx_dpa_clinic_id   ON doctor_patient_assignments (clinic_id);
CREATE INDEX idx_dpa_status      ON doctor_patient_assignments (status);
-- Unique active assignment per doctor-patient pair (partial — no unique constraint on table)
CREATE UNIQUE INDEX idx_dpa_active_unique
    ON doctor_patient_assignments (doctor_id, patient_id)
    WHERE status = 'active';
-- Fast capacity check: active patients per doctor
CREATE INDEX idx_dpa_active_doctor ON doctor_patient_assignments (doctor_id)
    WHERE status = 'active';

-- ------------------------------------------------------------
-- treatment_cycles
-- ------------------------------------------------------------
CREATE INDEX idx_cycles_patient_id   ON treatment_cycles (patient_id);
CREATE INDEX idx_cycles_doctor_id    ON treatment_cycles (doctor_id);
CREATE INDEX idx_cycles_clinic_id    ON treatment_cycles (clinic_id);
CREATE INDEX idx_cycles_status       ON treatment_cycles (status);
CREATE INDEX idx_cycles_cycle_type   ON treatment_cycles (cycle_type);

-- ------------------------------------------------------------
-- assessment_protocol_requests
-- ------------------------------------------------------------
CREATE INDEX idx_apr_patient_id  ON assessment_protocol_requests (patient_id);
CREATE INDEX idx_apr_ca_id       ON assessment_protocol_requests (clinical_assistant_id);
CREATE INDEX idx_apr_doctor_id   ON assessment_protocol_requests (doctor_id);
CREATE INDEX idx_apr_cycle_id    ON assessment_protocol_requests (cycle_id);
CREATE INDEX idx_apr_status      ON assessment_protocol_requests (status);

-- ------------------------------------------------------------
-- sessions
-- ------------------------------------------------------------
CREATE INDEX idx_sessions_cycle_id       ON sessions (cycle_id);
CREATE INDEX idx_sessions_clinic_id      ON sessions (clinic_id);
CREATE INDEX idx_sessions_patient_id     ON sessions (patient_id);
CREATE INDEX idx_sessions_doctor_id      ON sessions (doctor_id);
CREATE INDEX idx_sessions_ca_id          ON sessions (ca_id);
CREATE INDEX idx_sessions_status         ON sessions (status);
CREATE INDEX idx_sessions_session_phase  ON sessions (session_phase);
CREATE INDEX idx_sessions_payment_status ON sessions (payment_status);
CREATE INDEX idx_sessions_session_date   ON sessions (session_date);
CREATE INDEX idx_sessions_session_type   ON sessions (session_type);
-- Composite: most common clinical query pattern
CREATE INDEX idx_sessions_patient_date ON sessions (patient_id, session_date DESC);

-- ------------------------------------------------------------
-- treatment_plans
-- ------------------------------------------------------------
CREATE INDEX idx_tp_patient_id      ON treatment_plans (patient_id);
CREATE INDEX idx_tp_doctor_id       ON treatment_plans (doctor_id);
CREATE INDEX idx_tp_cycle_id        ON treatment_plans (cycle_id);
CREATE INDEX idx_tp_status          ON treatment_plans (status);
CREATE INDEX idx_tp_parent_plan_id  ON treatment_plans (parent_plan_id);

-- ------------------------------------------------------------
-- treatment_sessions
-- ------------------------------------------------------------
CREATE INDEX idx_ts_plan_id         ON treatment_sessions (plan_id);
CREATE INDEX idx_ts_session_id      ON treatment_sessions (session_id);
CREATE INDEX idx_ts_patient_id      ON treatment_sessions (patient_id);
CREATE INDEX idx_ts_ca_id           ON treatment_sessions (ca_id);
CREATE INDEX idx_ts_status          ON treatment_sessions (status);
CREATE INDEX idx_ts_payment_status  ON treatment_sessions (payment_status);
CREATE INDEX idx_ts_billing_type    ON treatment_sessions (billing_type);

-- ------------------------------------------------------------
-- doctor_session_notes
-- ------------------------------------------------------------
CREATE INDEX idx_dsn_session_id    ON doctor_session_notes (session_id);
CREATE INDEX idx_dsn_cycle_id      ON doctor_session_notes (cycle_id);
CREATE INDEX idx_dsn_patient_id    ON doctor_session_notes (patient_id);
CREATE INDEX idx_dsn_doctor_id     ON doctor_session_notes (doctor_id);
CREATE INDEX idx_dsn_session_phase ON doctor_session_notes (session_phase);
-- Fast "all notes for patient ordered by session" query
CREATE INDEX idx_dsn_patient_session ON doctor_session_notes (patient_id, session_number);

-- ------------------------------------------------------------
-- prs_scales
-- prs_scales has no disease_id or is_active column
-- disease → prs_disease_scale_map; is_active not in v6 schema
-- ------------------------------------------------------------
CREATE INDEX idx_prs_scales_applicable_for  ON prs_scales (applicable_for);
CREATE INDEX idx_prs_scales_is_common       ON prs_scales (is_common_scale);

-- ------------------------------------------------------------
-- prs_questions
-- ------------------------------------------------------------
CREATE INDEX idx_prs_questions_scale_id ON prs_questions (scale_id);

-- ------------------------------------------------------------
-- patient_scale_assignments
-- ------------------------------------------------------------
CREATE INDEX idx_psa_patient_id       ON patient_scale_assignments (patient_id);
CREATE INDEX idx_psa_scale_id         ON patient_scale_assignments (scale_id);
CREATE INDEX idx_psa_assessment_stage ON patient_scale_assignments (assessment_stage);

-- ------------------------------------------------------------
-- prs_assessment_instances
-- ------------------------------------------------------------
CREATE INDEX idx_pai_patient_id       ON prs_assessment_instances (patient_id);
CREATE INDEX idx_pai_disease_id       ON prs_assessment_instances (disease_id);
CREATE INDEX idx_pai_cycle_id         ON prs_assessment_instances (cycle_id);
CREATE INDEX idx_pai_session_id       ON prs_assessment_instances (session_id);
CREATE INDEX idx_pai_assessment_stage ON prs_assessment_instances (assessment_stage);
CREATE INDEX idx_pai_status           ON prs_assessment_instances (status);

-- ------------------------------------------------------------
-- prs_responses
-- ------------------------------------------------------------
CREATE INDEX idx_prs_resp_instance_id ON prs_responses (instance_id);
CREATE INDEX idx_prs_resp_question_id ON prs_responses (question_id);

-- ------------------------------------------------------------
-- anamnesis_assessments
-- ------------------------------------------------------------
CREATE INDEX idx_anamnesis_patient_id   ON anamnesis_assessments (patient_id);
CREATE INDEX idx_anamnesis_cycle_id     ON anamnesis_assessments (cycle_id);
CREATE INDEX idx_anamnesis_status       ON anamnesis_assessments (status);
CREATE INDEX idx_anamnesis_submitted_by ON anamnesis_assessments (submitted_by);

-- ------------------------------------------------------------
-- anamnesis_questions (section-based navigation)
-- ------------------------------------------------------------
CREATE INDEX idx_anaq_section_number ON anamnesis_questions (section_number);
CREATE INDEX idx_anaq_status         ON anamnesis_questions (status);
CREATE INDEX idx_anaq_depends_on     ON anamnesis_questions (depends_on_question_id);

-- ------------------------------------------------------------
-- anamnesis_options
-- ------------------------------------------------------------
CREATE INDEX idx_anao_question_id ON anamnesis_options (question_id);

-- ------------------------------------------------------------
-- anamnesis_responses
-- ------------------------------------------------------------
CREATE INDEX idx_anar_anamnesis_id  ON anamnesis_responses (anamnesis_id);
CREATE INDEX idx_anar_question_id   ON anamnesis_responses (question_id);

-- ------------------------------------------------------------
-- patient_eeg_files
-- ------------------------------------------------------------
CREATE INDEX idx_eeg_patient_id    ON patient_eeg_files (patient_id);
CREATE INDEX idx_eeg_clinic_id     ON patient_eeg_files (clinic_id);
CREATE INDEX idx_eeg_cycle_id      ON patient_eeg_files (cycle_id);
CREATE INDEX idx_eeg_session_id    ON patient_eeg_files (session_id);
CREATE INDEX idx_eeg_performed_by  ON patient_eeg_files (performed_by);
CREATE INDEX idx_eeg_reviewed_by   ON patient_eeg_files (reviewed_by);
CREATE INDEX idx_eeg_status        ON patient_eeg_files (status);
CREATE INDEX idx_eeg_eeg_type      ON patient_eeg_files (eeg_type);
CREATE INDEX idx_eeg_is_abnormal   ON patient_eeg_files (is_abnormal);
CREATE INDEX idx_eeg_performed_at  ON patient_eeg_files (performed_at DESC);

-- ------------------------------------------------------------
-- patient_medical_history_files
-- ------------------------------------------------------------
CREATE INDEX idx_mhf_patient_id     ON patient_medical_history_files (patient_id);
CREATE INDEX idx_mhf_clinic_id      ON patient_medical_history_files (clinic_id);
CREATE INDEX idx_mhf_cycle_id       ON patient_medical_history_files (cycle_id);
CREATE INDEX idx_mhf_uploaded_by    ON patient_medical_history_files (uploaded_by);
CREATE INDEX idx_mhf_document_type  ON patient_medical_history_files (document_type);
CREATE INDEX idx_mhf_is_deleted     ON patient_medical_history_files (is_deleted);
CREATE INDEX idx_mhf_document_date  ON patient_medical_history_files (document_date DESC);

-- ------------------------------------------------------------
-- consent_templates
-- ------------------------------------------------------------
CREATE INDEX idx_ct_consent_type ON consent_templates (consent_type);
CREATE INDEX idx_ct_is_active    ON consent_templates (is_active);

-- ------------------------------------------------------------
-- consent_records
-- ------------------------------------------------------------
CREATE INDEX idx_cr_patient_id   ON consent_records (patient_id);
CREATE INDEX idx_cr_staff_id     ON consent_records (staff_id);
CREATE INDEX idx_cr_clinic_id    ON consent_records (clinic_id);
CREATE INDEX idx_cr_consent_type ON consent_records (consent_type);
CREATE INDEX idx_cr_status       ON consent_records (status);
CREATE INDEX idx_cr_template_id  ON consent_records (template_id);

-- ------------------------------------------------------------
-- patient_clinic_transfers
-- ------------------------------------------------------------
CREATE INDEX idx_pct_patient_id     ON patient_clinic_transfers (patient_id);
CREATE INDEX idx_pct_from_clinic_id ON patient_clinic_transfers (from_clinic_id);
CREATE INDEX idx_pct_to_clinic_id   ON patient_clinic_transfers (to_clinic_id);
CREATE INDEX idx_pct_status         ON patient_clinic_transfers (status);
CREATE INDEX idx_pct_transfer_reason ON patient_clinic_transfers (transfer_reason);
CREATE INDEX idx_pct_active_cycle_id ON patient_clinic_transfers (active_cycle_id);

-- ------------------------------------------------------------
-- products
-- ------------------------------------------------------------
CREATE INDEX idx_products_category  ON products (category);
CREATE INDEX idx_products_is_active ON products (is_active);

-- ------------------------------------------------------------
-- store_orders
-- ------------------------------------------------------------
CREATE INDEX idx_so_patient_id  ON store_orders (patient_id);
CREATE INDEX idx_so_clinic_id   ON store_orders (clinic_id);
CREATE INDEX idx_so_initiated_by ON store_orders (initiated_by);
CREATE INDEX idx_so_order_type  ON store_orders (order_type);
CREATE INDEX idx_so_status      ON store_orders (status);
CREATE INDEX idx_so_plan_id     ON store_orders (treatment_plan_id);

-- ------------------------------------------------------------
-- order_items
-- ------------------------------------------------------------
CREATE INDEX idx_oi_order_id   ON order_items (order_id);
CREATE INDEX idx_oi_product_id ON order_items (product_id);

-- ------------------------------------------------------------
-- inventory
-- ------------------------------------------------------------
CREATE INDEX idx_inventory_clinic_id   ON inventory (clinic_id);
CREATE INDEX idx_inventory_product_id  ON inventory (product_id);

-- ------------------------------------------------------------
-- stock_transfers
-- ------------------------------------------------------------
CREATE INDEX idx_st_product_id     ON stock_transfers (product_id);
CREATE INDEX idx_st_from_clinic_id ON stock_transfers (from_clinic_id);
CREATE INDEX idx_st_to_clinic_id   ON stock_transfers (to_clinic_id);
CREATE INDEX idx_st_order_id       ON stock_transfers (order_id);
CREATE INDEX idx_st_status         ON stock_transfers (status);

-- ------------------------------------------------------------
-- device_assignments
-- ------------------------------------------------------------
CREATE INDEX idx_da_patient_id      ON device_assignments (patient_id);
CREATE INDEX idx_da_clinic_id       ON device_assignments (clinic_id);
CREATE INDEX idx_da_plan_id         ON device_assignments (plan_id);
CREATE INDEX idx_da_order_id        ON device_assignments (order_id);
CREATE INDEX idx_da_purchase_status ON device_assignments (purchase_status);

-- ------------------------------------------------------------
-- payments
-- ------------------------------------------------------------
CREATE INDEX idx_payments_session_id ON payments (session_id);
CREATE INDEX idx_payments_order_id   ON payments (order_id);
CREATE INDEX idx_payments_status     ON payments (status);

-- ------------------------------------------------------------
-- notifications
-- ------------------------------------------------------------
CREATE INDEX idx_notif_recipient_id ON notifications (recipient_id);
CREATE INDEX idx_notif_sender_id    ON notifications (sender_id);
CREATE INDEX idx_notif_clinic_id    ON notifications (clinic_id);
CREATE INDEX idx_notif_type         ON notifications (type);
CREATE INDEX idx_notif_created_at   ON notifications (created_at DESC);
-- Partial index covers "show unread" — 95% smaller than full index, 10x faster
CREATE INDEX idx_notif_unread ON notifications (recipient_id, created_at DESC)
    WHERE is_read = FALSE;

-- ------------------------------------------------------------
-- audit_logs
-- append-only table: BRIN on changed_at is orders of magnitude
-- smaller than B-tree and nearly as fast for time-range queries.
-- record_id is TEXT (not UUID) — supports PRS TEXT PKs.
-- ------------------------------------------------------------
CREATE INDEX idx_al_table_name  ON audit_logs (table_name);
CREATE INDEX idx_al_record_id   ON audit_logs (record_id);
CREATE INDEX idx_al_operation   ON audit_logs (operation);
CREATE INDEX idx_al_changed_by  ON audit_logs (changed_by);
-- BRIN instead of B-tree: insert-ordered table, 1000x smaller index
CREATE INDEX idx_al_changed_at_brin ON audit_logs USING BRIN (changed_at);

-- ------------------------------------------------------------
-- activity_logs
-- Same append-only pattern — use BRIN on created_at.
-- ------------------------------------------------------------
CREATE INDEX idx_actlog_actor_id   ON activity_logs (actor_id);
CREATE INDEX idx_actlog_category   ON activity_logs (category);
CREATE INDEX idx_actlog_event_type ON activity_logs (event_type);
CREATE INDEX idx_actlog_clinic_id  ON activity_logs (clinic_id);
CREATE INDEX idx_actlog_region_id  ON activity_logs (region_id);
CREATE INDEX idx_actlog_entity_id  ON activity_logs (entity_id);
CREATE INDEX idx_actlog_request_id ON activity_logs (request_id);
CREATE INDEX idx_actlog_created_at_brin ON activity_logs USING BRIN (created_at);
-- GIN for metadata search
CREATE INDEX idx_actlog_metadata_gin ON activity_logs USING GIN (metadata);

-- ------------------------------------------------------------
-- staff_requests — GIN on candidate_credentials JSONB
-- Enables fast filtering on specific credential fields
-- ------------------------------------------------------------
CREATE INDEX idx_staff_req_cred_gin ON staff_requests USING GIN (candidate_credentials);

-- ------------------------------------------------------------
-- treatment_plans — GIN on protocol_details JSONB
-- Enables fast search across protocol parameters
-- ------------------------------------------------------------
CREATE INDEX idx_tp_protocol_gin ON treatment_plans USING GIN (protocol_details);

-- ------------------------------------------------------------
-- patient_clinic_transfers — prevent duplicate in-flight transfers
-- A patient can only have one pending/consented transfer for the same
-- source→dest pair at a time. 'completed' and 'declined' rows excluded.
-- ------------------------------------------------------------
CREATE UNIQUE INDEX idx_pct_no_dup_pending
    ON patient_clinic_transfers (patient_id, from_clinic_id, to_clinic_id)
    WHERE status IN ('pending', 'consented');

-- ------------------------------------------------------------
-- clinics — enforce one main branch per region
-- Only one row per region_id where is_main_branch = TRUE
-- ------------------------------------------------------------
CREATE UNIQUE INDEX idx_clinics_one_main_branch
    ON clinics (region_id)
    WHERE is_main_branch = TRUE;

-- ------------------------------------------------------------
-- schema_migrations — idempotent deploy tracking
-- Record which SQL migration files have been applied.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT        PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------
-- doctor_weekly_schedules
-- ------------------------------------------------------------
CREATE INDEX idx_dws_doctor_clinic ON doctor_weekly_schedules (doctor_id, clinic_id);
CREATE INDEX idx_dws_clinic_dow    ON doctor_weekly_schedules (clinic_id, day_of_week);
CREATE INDEX idx_dws_is_active     ON doctor_weekly_schedules (is_active);

-- ------------------------------------------------------------
-- doctor_schedule_overrides
-- ------------------------------------------------------------
CREATE INDEX idx_dso_doctor_date ON doctor_schedule_overrides (doctor_id, override_date);
CREATE INDEX idx_dso_clinic      ON doctor_schedule_overrides (clinic_id);
CREATE INDEX idx_dso_date        ON doctor_schedule_overrides (override_date);

-- ------------------------------------------------------------
-- appointment_requests
-- ------------------------------------------------------------
CREATE INDEX idx_areq_patient_status  ON appointment_requests (patient_id, status);
CREATE INDEX idx_areq_clinic_status   ON appointment_requests (clinic_id, status);
CREATE INDEX idx_areq_doctor_status   ON appointment_requests (doctor_id, status);
CREATE INDEX idx_areq_pref_date1      ON appointment_requests (preferred_date_1);
CREATE INDEX idx_areq_urgency         ON appointment_requests (urgency)
    WHERE urgency IN ('urgent', 'emergency');

-- ------------------------------------------------------------
-- appointments
-- Hot query paths: schedule view, patient history, conflict check
-- ------------------------------------------------------------
CREATE INDEX idx_appt_doctor_date_status ON appointments (doctor_id, appointment_date, status);
CREATE INDEX idx_appt_patient_date       ON appointments (patient_id, appointment_date DESC);
CREATE INDEX idx_appt_clinic_date_status ON appointments (clinic_id, appointment_date, status);
CREATE INDEX idx_appt_session_id         ON appointments (session_id);
CREATE INDEX idx_appt_cycle_id           ON appointments (cycle_id);
CREATE INDEX idx_appt_request_id         ON appointments (appointment_request_id);
CREATE INDEX idx_appt_status             ON appointments (status);

-- ------------------------------------------------------------
-- appointment_audit_logs
-- Append-only: BRIN on changed_at (insert-ordered, 1000x smaller)
-- ------------------------------------------------------------
CREATE INDEX idx_apal_appointment_id ON appointment_audit_logs (appointment_id);
CREATE INDEX idx_apal_changed_at_brin ON appointment_audit_logs USING BRIN (changed_at);
