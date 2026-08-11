-- ===========================================================================
-- erd_drawsql.sql — drawSQL.app import file
--
-- Generated FROM erd.sql. Same 89 tables, 89 primary keys and 259 foreign keys,
-- reduced to the subset drawSQL's importer can parse.
--
-- REMOVED (drawSQL cannot parse these; nothing is lost — they remain in erd.sql):
--   1,164 partition children and every PARTITION BY clause
--   13 functions and the DO $$ ... $$ block
--   84 triggers
--   245 RLS policies and all ENABLE/FORCE ROW LEVEL SECURITY
--   grants, roles, extensions, sequences, the view, comments
--   CHECK constraints and DEFAULT expressions
--
-- CHANGED:
--   the 6 enum types are inlined as VARCHAR (drawSQL does not resolve CREATE TYPE)
--   TEXT[] is shown as TEXT (no array support in the importer)
--   the 16 ALTER TABLE ... ADD COLUMN patches are folded into their parent table,
--     so patients/profiles/doctors/consent_records show their full column list
--   schema qualifiers are stripped — table names are unique across all six
--     schemas, so nothing collides and FK targets resolve
--
-- DIAGRAMMING ONLY — do not run this against a database. erd.sql is the schema.
-- ===========================================================================

CREATE TABLE "admins" (
    "admin_id" UUID NOT NULL,
    "profile_id" UUID NOT NULL,
    "admin_type" TEXT NOT NULL,
    "region_id" UUID,
    "clinic_id" UUID,
    "force_password_change" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "anamnesis_assessments" (
    "anamnesis_id" TEXT NOT NULL,
    "patient_id" UUID NOT NULL,
    "submitted_by" UUID,
    "taken_by" VARCHAR(20) NOT NULL,
    "cycle_id" UUID,
    "version" INTEGER NOT NULL,
    "status" TEXT NOT NULL,
    "completed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "anamnesis_responses" (
    "response_id" TEXT NOT NULL,
    "anamnesis_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "response_value" TEXT,
    "response_values" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "appointment_audit_logs" (
    "audit_id" UUID NOT NULL,
    "appointment_id" UUID NOT NULL,
    "changed_by" UUID,
    "changed_by_role" TEXT,
    "previous_status" TEXT,
    "new_status" TEXT NOT NULL,
    "previous_date" DATE,
    "new_date" DATE,
    "previous_time" TIME,
    "new_time" TIME,
    "change_reason" TEXT,
    "changed_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "appointment_requests" (
    "request_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID,
    "cycle_id" UUID,
    "request_type" TEXT NOT NULL,
    "parent_appointment_id" UUID,
    "preferred_date_1" DATE NOT NULL,
    "preferred_date_2" DATE,
    "preferred_date_3" DATE,
    "preferred_time_window" TEXT NOT NULL,
    "patient_complaint" TEXT,
    "reason" TEXT,
    "urgency" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "approved_appointment_id" UUID,
    "submitted_by" UUID NOT NULL,
    "reviewed_by" UUID,
    "review_notes" TEXT,
    "expires_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "appointments" (
    "appointment_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "ca_id" UUID,
    "session_id" UUID,
    "cycle_id" UUID,
    "appointment_request_id" UUID,
    "appointment_date" DATE NOT NULL,
    "start_time" TIME NOT NULL,
    "end_time" TIME NOT NULL,
    "slot_duration_minutes" INTEGER NOT NULL,
    "appointment_type" TEXT NOT NULL,
    "session_phase" TEXT,
    "status" TEXT NOT NULL,
    "reason" TEXT,
    "patient_complaint" TEXT,
    "notes" TEXT,
    "cancellation_reason" TEXT,
    "booked_by" UUID NOT NULL,
    "booked_by_role" TEXT NOT NULL,
    "cancelled_by" UUID,
    "rescheduled_from" UUID,
    "rescheduled_to" UUID,
    "checked_in_at" TIMESTAMPTZ,
    "started_at" TIMESTAMPTZ,
    "completed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "assessment_protocol_requests" (
    "request_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "clinical_assistant_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "clinic_id" UUID,
    "cycle_id" UUID,
    "protocol_details" JSONB NOT NULL,
    "status" TEXT NOT NULL,
    "doctor_notes" TEXT,
    "submitted_at" TIMESTAMPTZ NOT NULL,
    "reviewed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "ca_doctor_assignments" (
    "cda_id" UUID NOT NULL,
    "ca_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "is_primary" BOOLEAN NOT NULL,
    "assigned_at" TIMESTAMPTZ NOT NULL,
    "removed_at" TIMESTAMPTZ
);

CREATE TABLE "clinic_requests" (
    "request_id" UUID NOT NULL,
    "request_type" TEXT NOT NULL,
    "clinic_type" TEXT,
    "clinic_id" UUID,
    "region_id" UUID NOT NULL,
    "submitted_by" UUID NOT NULL,
    "status" TEXT NOT NULL,
    "payload" JSONB NOT NULL,
    "reviewed_by" UUID,
    "review_notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "clinic_staff_assignments" (
    "assignment_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "profile_id" UUID NOT NULL,
    "staff_role" TEXT NOT NULL,
    "is_active" BOOLEAN NOT NULL,
    "joined_at" TIMESTAMPTZ NOT NULL,
    "removed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "clinical_assistants" (
    "ca_id" UUID NOT NULL,
    "profile_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "qualification" TEXT,
    "is_active" BOOLEAN NOT NULL,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "clinics" (
    "clinic_id" UUID NOT NULL,
    "clinic_code" TEXT NOT NULL,
    "clinic_name" TEXT NOT NULL,
    "clinic_type" TEXT NOT NULL,
    "owner_name" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "region_id" UUID NOT NULL,
    "clinic_admin_id" UUID,
    "is_main_branch" BOOLEAN NOT NULL,
    "timezone" TEXT NOT NULL,
    "address" TEXT,
    "city" TEXT,
    "state" TEXT,
    "country" TEXT NOT NULL,
    "phone" TEXT,
    "email" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "device_assignments" (
    "da_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "plan_id" UUID NOT NULL,
    "assigned_by" UUID NOT NULL,
    "device_type" TEXT NOT NULL,
    "purchase_status" TEXT NOT NULL,
    "order_id" UUID,
    "prompted_at" TIMESTAMPTZ NOT NULL,
    "purchased_at" TIMESTAMPTZ,
    "collected_at" TIMESTAMPTZ,
    "returned_at" TIMESTAMPTZ,
    "returned_by" UUID,
    "return_reason" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "doctor_patient_assignments" (
    "assignment_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "status" TEXT NOT NULL,
    "assigned_at" TIMESTAMPTZ NOT NULL,
    "ended_at" TIMESTAMPTZ
);

CREATE TABLE "doctor_schedule_overrides" (
    "override_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "override_date" DATE NOT NULL,
    "is_available" BOOLEAN NOT NULL,
    "start_time" TIME,
    "end_time" TIME,
    "slot_duration_minutes" INTEGER,
    "reason" TEXT,
    "created_by" UUID NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "doctor_session_notes" (
    "note_id" UUID NOT NULL,
    "session_id" UUID NOT NULL,
    "cycle_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "session_number" INTEGER NOT NULL,
    "session_phase" TEXT NOT NULL,
    "chief_complaint" TEXT,
    "clinical_observations" TEXT,
    "assessment" TEXT,
    "treatment_plan_notes" TEXT,
    "follow_up_instructions" TEXT,
    "referrals" TEXT,
    "note_content" TEXT,
    "is_confidential" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "doctor_weekly_schedules" (
    "schedule_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "day_of_week" SMALLINT NOT NULL,
    "start_time" TIME NOT NULL,
    "end_time" TIME NOT NULL,
    "slot_duration_minutes" INTEGER NOT NULL,
    "break_start" TIME,
    "break_end" TIME,
    "max_appointments" INTEGER,
    "is_active" BOOLEAN NOT NULL,
    "effective_from" DATE,
    "effective_until" DATE,
    "created_by" UUID,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "doctors" (
    "doctor_id" UUID NOT NULL,
    "profile_id" UUID NOT NULL,
    "specialization" TEXT,
    "license_number" TEXT,
    "hospital_affiliation" TEXT,
    "max_patient_count" INTEGER NOT NULL,
    "availability_status" TEXT NOT NULL,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL,
    "clinic_id" UUID,
    "legal_hold" BOOLEAN NOT NULL
);

CREATE TABLE "inventory" (
    "inventory_id" UUID NOT NULL,
    "product_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "quantity" INTEGER NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "notifications" (
    "notification_id" UUID NOT NULL,
    "recipient_id" UUID NOT NULL,
    "sender_id" UUID,
    "clinic_id" UUID,
    "type" TEXT NOT NULL,
    "delivery_channel" TEXT NOT NULL,
    "title" TEXT NOT NULL,
    "body" TEXT,
    "entity_type" TEXT,
    "entity_id" UUID,
    "metadata" JSONB NOT NULL,
    "is_read" BOOLEAN NOT NULL,
    "read_at" TIMESTAMPTZ,
    "delivered_at" TIMESTAMPTZ,
    "delivery_attempts" INTEGER NOT NULL,
    "expires_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "order_items" (
    "item_id" UUID NOT NULL,
    "order_id" UUID NOT NULL,
    "product_id" UUID NOT NULL,
    "quantity" INTEGER NOT NULL,
    "unit_price" NUMERIC(10,2) NOT NULL
);

CREATE TABLE "patient_clinic_transfers" (
    "pct_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "from_clinic_id" UUID NOT NULL,
    "to_clinic_id" UUID NOT NULL,
    "from_doctor_id" UUID,
    "to_doctor_id" UUID,
    "transfer_reason" TEXT NOT NULL,
    "active_cycle_id" UUID,
    "status" TEXT NOT NULL,
    "consent_id" UUID,
    "initiated_by" UUID NOT NULL,
    "notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "patient_disease_selection" (
    "pds_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "disease_id" TEXT,
    "disease_unknown" BOOLEAN NOT NULL,
    "is_primary" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "patient_eeg_files" (
    "eeg_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "cycle_id" UUID,
    "session_id" UUID,
    "performed_by" UUID NOT NULL,
    "reviewed_by" UUID,
    "eeg_type" TEXT NOT NULL,
    "duration_minutes" INTEGER,
    "raw_data_s3_key" TEXT,
    "raw_file_name" TEXT,
    "raw_file_size" BIGINT,
    "raw_checksum" TEXT,
    "raw_checksum_algorithm" TEXT NOT NULL,
    "report_s3_key" TEXT,
    "report_file_name" TEXT,
    "report_file_size" BIGINT,
    "report_checksum" TEXT,
    "report_checksum_algorithm" TEXT NOT NULL,
    "superseded_by" UUID,
    "recording_notes" TEXT,
    "clinical_findings" TEXT,
    "is_abnormal" BOOLEAN,
    "status" TEXT NOT NULL,
    "performed_at" TIMESTAMPTZ NOT NULL,
    "reviewed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "patient_medical_history_files" (
    "mhf_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "cycle_id" UUID,
    "uploaded_by" UUID NOT NULL,
    "document_type" TEXT NOT NULL,
    "s3_key" TEXT NOT NULL,
    "file_name" TEXT NOT NULL,
    "file_size" BIGINT,
    "mime_type" TEXT,
    "checksum" TEXT,
    "checksum_algorithm" TEXT NOT NULL,
    "description" TEXT,
    "document_date" DATE,
    "source_provider" TEXT,
    "is_deleted" BOOLEAN NOT NULL,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "patient_scale_assignments" (
    "psa_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "scale_id" TEXT NOT NULL,
    "assessment_stage" TEXT NOT NULL,
    "assigned_by" UUID NOT NULL,
    "assignment_reason" TEXT,
    "is_active" BOOLEAN NOT NULL,
    "deactivated_at" TIMESTAMPTZ,
    "deactivated_by" UUID,
    "created_at" TIMESTAMPTZ NOT NULL,
    "disease_id" TEXT
);

CREATE TABLE "patients" (
    "patient_id" UUID NOT NULL,
    "profile_id" UUID NOT NULL,
    "mrn" TEXT NOT NULL,
    "registration_status" TEXT NOT NULL,
    "primary_clinic_id" UUID,
    "primary_doctor_id" UUID,
    "blood_group" TEXT,
    "allergies" TEXT,
    "occupation" TEXT,
    "marital_status" TEXT,
    "insurance_provider" TEXT,
    "insurance_policy" TEXT,
    "referred_by" TEXT,
    "emergency_contact_name" TEXT,
    "emergency_contact_phone" TEXT,
    "registration_completed_at" TIMESTAMPTZ,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "self_registered" BOOLEAN NOT NULL,
    "approval_status" TEXT NOT NULL,
    "approved_by" UUID,
    "approved_at" TIMESTAMPTZ,
    "rejection_reason" TEXT,
    "retention_basis_cleared_at" TIMESTAMPTZ,
    "legal_hold" BOOLEAN NOT NULL,
    "closure_type" TEXT,
    "closure_reason" TEXT,
    "closed_at" TIMESTAMPTZ,
    "rejoin_deadline" TIMESTAMPTZ,
    "portal_access_mode" TEXT NOT NULL,
    "last_clinical_contact_at" TIMESTAMPTZ,
    "registered_by" UUID,
    "guardian_name" TEXT,
    "guardian_relationship" TEXT,
    "guardian_contact" TEXT
);

CREATE TABLE "payments" (
    "payment_id" UUID NOT NULL,
    "session_id" UUID,
    "order_id" UUID,
    "idempotency_key" TEXT NOT NULL,
    "razorpay_order_id" TEXT,
    "razorpay_payment_id" TEXT,
    "amount" NUMERIC(10,2) NOT NULL,
    "currency" TEXT NOT NULL,
    "payment_method" TEXT,
    "status" TEXT NOT NULL,
    "gateway_response" JSONB NOT NULL,
    "waived_by" UUID,
    "waived_reason" TEXT,
    "paid_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "profiles" (
    "id" UUID NOT NULL,
    "cognito_sub" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "first_name" TEXT NOT NULL,
    "last_name" TEXT NOT NULL,
    "phone" TEXT,
    "role" TEXT NOT NULL,
    "gender" TEXT,
    "dob" DATE,
    "address" TEXT,
    "city" TEXT,
    "state" TEXT,
    "country" TEXT,
    "profile_photo_s3_key" TEXT,
    "pincode" TEXT,
    "language_pref" TEXT NOT NULL,
    "is_active" BOOLEAN NOT NULL,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "consent_signed" BOOLEAN NOT NULL,
    "email_verified" BOOLEAN NOT NULL,
    "phone_verified" BOOLEAN NOT NULL,
    "is_anonymized" BOOLEAN NOT NULL,
    "anonymized_at" TIMESTAMPTZ
);

CREATE TABLE "prs_assessment_instances" (
    "instance_id" TEXT NOT NULL,
    "disease_id" TEXT NOT NULL,
    "patient_id" UUID NOT NULL,
    "session_id" UUID,
    "cycle_id" UUID,
    "initiated_by" VARCHAR(20) NOT NULL,
    "administered_by" UUID,
    "assessment_stage" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "started_at" TIMESTAMPTZ NOT NULL,
    "completed_at" TIMESTAMPTZ,
    "final_result" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL,
    "language_code" VARCHAR(10) NOT NULL
);

CREATE TABLE "prs_final_results" (
    "final_result_id" TEXT NOT NULL,
    "instance_id" TEXT NOT NULL,
    "calculated_value" NUMERIC,
    "max_possible" NUMERIC,
    "percentage" NUMERIC,
    "scales_completed" INTEGER NOT NULL,
    "scales_total" INTEGER NOT NULL,
    "overall_severity" TEXT,
    "overall_severity_label" TEXT,
    "scale_summaries" JSONB NOT NULL,
    "all_risk_flags" JSONB NOT NULL,
    "composite_summary" TEXT,
    "time_stamp" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "prs_responses" (
    "response_id" TEXT NOT NULL,
    "instance_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "given_response" TEXT,
    "response_value" NUMERIC,
    "time_stamp" TIMESTAMPTZ NOT NULL,
    "language_code" VARCHAR(10) NOT NULL
);

CREATE TABLE "prs_scale_results" (
    "scale_result_id" TEXT NOT NULL,
    "instance_id" TEXT NOT NULL,
    "scale_id" TEXT NOT NULL,
    "calculated_value" NUMERIC,
    "max_possible" NUMERIC,
    "percentage" NUMERIC,
    "severity_level" TEXT,
    "severity_label" TEXT,
    "subscale_scores" JSONB NOT NULL,
    "risk_flags" JSONB NOT NULL,
    "raw_score_data" JSONB NOT NULL,
    "time_stamp" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "receptionists" (
    "receptionist_id" UUID NOT NULL,
    "profile_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "is_active" BOOLEAN NOT NULL,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "regions" (
    "region_id" UUID NOT NULL,
    "region_name" TEXT NOT NULL,
    "country" TEXT NOT NULL,
    "state" TEXT NOT NULL,
    "regional_admin_id" UUID,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "sessions" (
    "session_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID,
    "session_date" TIMESTAMPTZ NOT NULL,
    "session_type" TEXT NOT NULL,
    "notes" TEXT,
    "status" TEXT NOT NULL,
    "cycle_id" UUID,
    "clinic_id" UUID,
    "ca_id" UUID,
    "session_phase" TEXT,
    "session_number_in_cycle" INTEGER,
    "outcome" TEXT,
    "started_at" TIMESTAMPTZ,
    "completed_at" TIMESTAMPTZ,
    "payment_status" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "staff_requests" (
    "request_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "regional_admin_id" UUID,
    "request_type" TEXT NOT NULL,
    "position_role" TEXT NOT NULL,
    "candidate_name" TEXT,
    "candidate_email" TEXT,
    "candidate_phone" TEXT,
    "candidate_credentials" JSONB NOT NULL,
    "target_staff_id" UUID,
    "status" TEXT NOT NULL,
    "submitted_by" UUID NOT NULL,
    "reviewed_by" UUID,
    "review_notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "fulfilled_profile_id" UUID,
    "fulfilled_at" TIMESTAMPTZ
);

CREATE TABLE "stock_transfers" (
    "st_id" UUID NOT NULL,
    "product_id" UUID NOT NULL,
    "from_type" TEXT NOT NULL,
    "from_clinic_id" UUID,
    "to_clinic_id" UUID NOT NULL,
    "quantity" INTEGER NOT NULL,
    "order_id" UUID,
    "status" TEXT NOT NULL,
    "initiated_by" UUID NOT NULL,
    "received_by" UUID,
    "notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL,
    "dispatched_at" TIMESTAMPTZ,
    "received_at" TIMESTAMPTZ
);

CREATE TABLE "store_orders" (
    "order_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "initiated_by" UUID NOT NULL,
    "approved_by" UUID,
    "order_type" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "total_amount" NUMERIC(10,2),
    "treatment_plan_id" UUID,
    "cancelled_by" UUID,
    "cancelled_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "treatment_cycles" (
    "cycle_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "ca_id" UUID,
    "clinic_id" UUID NOT NULL,
    "cycle_type" TEXT NOT NULL,
    "cycle_number" INTEGER NOT NULL,
    "scheduled_date" DATE,
    "status" TEXT NOT NULL,
    "notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "treatment_plans" (
    "plan_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "cycle_id" UUID NOT NULL,
    "device_type" TEXT NOT NULL,
    "protocol_details" JSONB NOT NULL,
    "sessions_prescribed" INTEGER NOT NULL,
    "standard_sessions" INTEGER NOT NULL,
    "extended_sessions" INTEGER,
    "status" TEXT NOT NULL,
    "parent_plan_id" UUID,
    "demo_phase_status" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "treatment_sessions" (
    "ts_id" UUID NOT NULL,
    "plan_id" UUID NOT NULL,
    "session_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "ca_id" UUID NOT NULL,
    "session_number" INTEGER NOT NULL,
    "billing_type" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "payment_status" TEXT NOT NULL,
    "session_notes" TEXT,
    "patient_feedback" TEXT,
    "started_at" TIMESTAMPTZ,
    "completed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "anamnesis_options" (
    "option_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "option_label" TEXT NOT NULL,
    "option_value" TEXT NOT NULL,
    "display_order" INTEGER NOT NULL
);

CREATE TABLE "anamnesis_questions" (
    "question_id" TEXT NOT NULL,
    "section_number" INTEGER NOT NULL,
    "section_title" TEXT NOT NULL,
    "question_code" TEXT NOT NULL,
    "question_text" TEXT NOT NULL,
    "answer_type" TEXT NOT NULL,
    "is_required" BOOLEAN NOT NULL,
    "display_order" INTEGER NOT NULL,
    "depends_on_question_id" TEXT,
    "depends_on_value" TEXT,
    "helper_text" TEXT,
    "status" BOOLEAN NOT NULL
);

CREATE TABLE "consent_templates" (
    "template_id" UUID NOT NULL,
    "consent_type" TEXT NOT NULL,
    "version" INTEGER NOT NULL,
    "title" TEXT NOT NULL,
    "content" TEXT NOT NULL,
    "content_hash" TEXT,
    "effective_date" DATE,
    "expiry_date" DATE,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "role" TEXT
);

CREATE TABLE "products" (
    "product_id" UUID NOT NULL,
    "name" TEXT NOT NULL,
    "description" TEXT,
    "category" TEXT NOT NULL,
    "price" NUMERIC(10,2) NOT NULL,
    "sku" TEXT,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "prs_disease_question_map" (
    "dq_map_id" TEXT NOT NULL,
    "disease_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "display_order" INTEGER NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "prs_disease_scale_map" (
    "ds_map_id" TEXT NOT NULL,
    "disease_id" TEXT NOT NULL,
    "scale_id" TEXT NOT NULL,
    "display_order" INTEGER NOT NULL,
    "is_required" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "prs_diseases" (
    "disease_id" TEXT NOT NULL,
    "disease_code" TEXT NOT NULL,
    "disease_name" TEXT NOT NULL,
    "version" TEXT NOT NULL,
    "status" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "prs_option_translations" (
    "option_id" TEXT NOT NULL,
    "language_code" VARCHAR(10) NOT NULL,
    "option_label" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "prs_options" (
    "option_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "option_label" TEXT NOT NULL,
    "option_value" TEXT NOT NULL,
    "points" NUMERIC NOT NULL,
    "display_order" INTEGER NOT NULL,
    "status" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "prs_question_translations" (
    "question_id" TEXT NOT NULL,
    "language_code" VARCHAR(10) NOT NULL,
    "question_text" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "prs_questions" (
    "question_id" TEXT NOT NULL,
    "question_code" TEXT NOT NULL,
    "disease_id" TEXT,
    "scale_id" TEXT,
    "ds_map_id" TEXT,
    "question_text" TEXT NOT NULL,
    "answer_type" TEXT NOT NULL,
    "min_value" NUMERIC,
    "max_value" NUMERIC,
    "is_required" BOOLEAN NOT NULL,
    "skip_logic" TEXT,
    "display_order" INTEGER NOT NULL,
    "is_common_scale" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "prs_scale_question_map" (
    "sq_map_id" TEXT NOT NULL,
    "scale_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "display_order" INTEGER NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "prs_scales" (
    "scale_id" TEXT NOT NULL,
    "scale_code" TEXT NOT NULL,
    "scale_name" TEXT NOT NULL,
    "is_common_scale" BOOLEAN NOT NULL,
    "num_diseases_used" INTEGER NOT NULL,
    "applicable_for" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "activity_logs" (
    "log_id" UUID NOT NULL,
    "actor_id" UUID NOT NULL,
    "actor_role" TEXT NOT NULL,
    "request_id" TEXT,
    "category" TEXT NOT NULL,
    "event_type" TEXT NOT NULL,
    "entity_type" TEXT,
    "entity_id" UUID,
    "clinic_id" UUID,
    "region_id" UUID,
    "metadata" JSONB NOT NULL,
    "ip_address" INET,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "audit_logs" (
    "log_id" UUID NOT NULL,
    "table_name" TEXT NOT NULL,
    "operation" TEXT NOT NULL,
    "record_id" TEXT,
    "old_data" JSONB,
    "new_data" JSONB,
    "changed_by" UUID,
    "clinic_id" UUID,
    "ip_address" INET,
    "request_id" TEXT,
    "changed_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "consent_records" (
    "consent_id" UUID NOT NULL,
    "consent_type" TEXT NOT NULL,
    "template_id" UUID NOT NULL,
    "patient_id" UUID,
    "staff_id" UUID,
    "clinic_id" UUID,
    "status" TEXT NOT NULL,
    "signed_at" TIMESTAMPTZ,
    "signed_by" UUID,
    "witness_id" UUID,
    "ip_address" INET,
    "signature_data" TEXT,
    "pdf_s3_key" TEXT,
    "content_hash_at_signing" TEXT,
    "revoked_at" TIMESTAMPTZ,
    "revoked_by" UUID,
    "created_at" TIMESTAMPTZ NOT NULL,
    "region_id" UUID,
    "guardian_id" UUID
);

CREATE TABLE "alembic_version" (
    "version_num" VARCHAR(32) NOT NULL
);

CREATE TABLE "outbox_events" (
    "outbox_id" UUID NOT NULL,
    "aggregate_type" TEXT NOT NULL,
    "aggregate_id" TEXT NOT NULL,
    "event_type" TEXT NOT NULL,
    "payload" JSONB NOT NULL,
    "published_at" TIMESTAMPTZ,
    "publish_attempts" INTEGER NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "schema_migrations" (
    "version" TEXT NOT NULL,
    "applied_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "neuromod_devices" (
    "device_id" UUID NOT NULL,
    "device_code" TEXT NOT NULL,
    "device_name" TEXT NOT NULL,
    "modality" VARCHAR(20) NOT NULL,
    "phase" SMALLINT NOT NULL,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "neuromod_conditions" (
    "condition_id" UUID NOT NULL,
    "condition_name" TEXT NOT NULL,
    "display_order" INTEGER NOT NULL,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "neuromod_diagnoses" (
    "diagnosis_id" UUID NOT NULL,
    "condition_id" UUID NOT NULL,
    "icd10_code" TEXT NOT NULL,
    "icd10_description" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "neuromod_scales" (
    "scale_id" UUID NOT NULL,
    "scale_code" TEXT NOT NULL,
    "scale_name" TEXT NOT NULL,
    "prs_scale_id" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "neuromod_condition_scales" (
    "cs_map_id" UUID NOT NULL,
    "condition_id" UUID NOT NULL,
    "scale_id" UUID NOT NULL,
    "display_order" INTEGER NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "tdcs_placements" (
    "tdcs_placement_id" UUID NOT NULL,
    "condition_id" UUID NOT NULL,
    "device_id" UUID NOT NULL,
    "montage_label" TEXT NOT NULL,
    "anode_site" TEXT,
    "cathode_site" TEXT,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "hd_tdcs_placements" (
    "hd_tdcs_placement_id" UUID NOT NULL,
    "condition_id" UUID NOT NULL,
    "device_id" UUID NOT NULL,
    "montage_label" TEXT NOT NULL,
    "anode_site" TEXT,
    "return_sites" TEXT NOT NULL,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "tavns_placements" (
    "tavns_placement_id" UUID NOT NULL,
    "condition_id" UUID NOT NULL,
    "device_id" UUID NOT NULL,
    "montage_label" TEXT NOT NULL,
    "ear_side" VARCHAR(10),
    "auricular_site" TEXT,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "tps_placements" (
    "tps_placement_id" UUID NOT NULL,
    "condition_id" UUID NOT NULL,
    "device_id" UUID NOT NULL,
    "montage_label" TEXT NOT NULL,
    "target_region" TEXT,
    "hemisphere" VARCHAR(10),
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "rtms_placements" (
    "rtms_placement_id" UUID NOT NULL,
    "condition_id" UUID NOT NULL,
    "device_id" UUID NOT NULL,
    "montage_label" TEXT NOT NULL,
    "coil_target" TEXT,
    "coil_type" TEXT,
    "hemisphere" VARCHAR(10),
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "other_placements" (
    "other_placement_id" UUID NOT NULL,
    "condition_id" UUID NOT NULL,
    "device_id" UUID NOT NULL,
    "montage_label" TEXT NOT NULL,
    "placement_details" JSONB NOT NULL,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "tdcs_dosing" (
    "tdcs_dosing_id" UUID NOT NULL,
    "condition_id" UUID NOT NULL,
    "device_id" UUID NOT NULL,
    "tdcs_placement_id" UUID NOT NULL,
    "evidence_level" VARCHAR(1) NOT NULL,
    "current_ma_min" NUMERIC(3,1),
    "current_ma_max" NUMERIC(3,1),
    "session_duration_min" INTEGER,
    "sessions_per_day" INTEGER,
    "num_sessions_text" TEXT,
    "notes" TEXT,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "hd_tdcs_dosing" (
    "hd_tdcs_dosing_id" UUID NOT NULL,
    "condition_id" UUID NOT NULL,
    "device_id" UUID NOT NULL,
    "hd_tdcs_placement_id" UUID NOT NULL,
    "evidence_level" VARCHAR(1) NOT NULL,
    "total_current_ma" NUMERIC(3,1),
    "per_return_current_ma" NUMERIC(3,1),
    "session_duration_min" INTEGER,
    "sessions_per_day" INTEGER,
    "num_sessions_text" TEXT,
    "notes" TEXT,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "tavns_dosing" (
    "tavns_dosing_id" UUID NOT NULL,
    "condition_id" UUID NOT NULL,
    "device_id" UUID NOT NULL,
    "tavns_placement_id" UUID NOT NULL,
    "evidence_level" VARCHAR(1) NOT NULL,
    "intensity_ma" NUMERIC(4,2),
    "pulse_width_us" INTEGER,
    "frequency_hz" NUMERIC(6,2),
    "duty_cycle_on_sec" INTEGER,
    "duty_cycle_off_sec" INTEGER,
    "session_duration_min" INTEGER,
    "num_sessions_text" TEXT,
    "notes" TEXT,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "tps_dosing" (
    "tps_dosing_id" UUID NOT NULL,
    "condition_id" UUID NOT NULL,
    "device_id" UUID NOT NULL,
    "tps_placement_id" UUID NOT NULL,
    "evidence_level" VARCHAR(1) NOT NULL,
    "energy_mj" NUMERIC(6,3),
    "pulses_per_session" INTEGER,
    "pulse_rate_hz" NUMERIC(6,2),
    "num_sessions_text" TEXT,
    "notes" TEXT,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "rtms_dosing" (
    "rtms_dosing_id" UUID NOT NULL,
    "condition_id" UUID NOT NULL,
    "device_id" UUID NOT NULL,
    "rtms_placement_id" UUID NOT NULL,
    "evidence_level" VARCHAR(1) NOT NULL,
    "frequency_hz" NUMERIC(6,2),
    "pct_motor_threshold" NUMERIC(5,2),
    "train_count" INTEGER,
    "pulses_per_train" INTEGER,
    "pulses_per_session" INTEGER,
    "inter_train_interval_sec" NUMERIC(6,2),
    "num_sessions_text" TEXT,
    "notes" TEXT,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "other_dosing" (
    "other_dosing_id" UUID NOT NULL,
    "condition_id" UUID NOT NULL,
    "device_id" UUID NOT NULL,
    "other_placement_id" UUID NOT NULL,
    "evidence_level" VARCHAR(1) NOT NULL,
    "dose_details" JSONB NOT NULL,
    "num_sessions_text" TEXT,
    "notes" TEXT,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "treatment_protocols" (
    "protocol_id" UUID NOT NULL,
    "plan_id" UUID NOT NULL,
    "device_id" UUID NOT NULL,
    "set_by" UUID NOT NULL,
    "tdcs_placement_id" UUID,
    "hd_tdcs_placement_id" UUID,
    "tavns_placement_id" UUID,
    "tps_placement_id" UUID,
    "rtms_placement_id" UUID,
    "other_placement_id" UUID,
    "tdcs_dosing_id" UUID,
    "hd_tdcs_dosing_id" UUID,
    "tavns_dosing_id" UUID,
    "tps_dosing_id" UUID,
    "rtms_dosing_id" UUID,
    "other_dosing_id" UUID,
    "session_count" INTEGER NOT NULL,
    "follow_up_every_n" INTEGER,
    "status" VARCHAR(20) NOT NULL,
    "device_settings" JSONB NOT NULL,
    "notes" TEXT,
    "activated_at" TIMESTAMPTZ,
    "completed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "protocol_sessions" (
    "protocol_session_id" UUID NOT NULL,
    "protocol_id" UUID NOT NULL,
    "appointment_id" UUID NOT NULL,
    "session_number" INTEGER NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "protocol_followups" (
    "protocol_followup_id" UUID NOT NULL,
    "protocol_id" UUID NOT NULL,
    "appointment_id" UUID NOT NULL,
    "after_session_number" INTEGER NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "device_session_prs_responses" (
    "ds_prs_id" UUID NOT NULL,
    "protocol_session_id" UUID NOT NULL,
    "instance_id" TEXT NOT NULL,
    "patient_id" UUID NOT NULL,
    "recorded_at" TIMESTAMPTZ NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "followup_prs_responses" (
    "fu_prs_id" UUID NOT NULL,
    "protocol_followup_id" UUID NOT NULL,
    "instance_id" TEXT NOT NULL,
    "patient_id" UUID NOT NULL,
    "recorded_at" TIMESTAMPTZ NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "erasure_requests" (
    "request_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "requested_by" UUID NOT NULL,
    "requester_verification_method" TEXT,
    "status" TEXT NOT NULL,
    "received_at" TIMESTAMPTZ NOT NULL,
    "response_due_at" TIMESTAMPTZ NOT NULL,
    "responded_at" TIMESTAMPTZ,
    "response_summary" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "erasure_request_items" (
    "item_id" UUID NOT NULL,
    "request_id" UUID NOT NULL,
    "data_category" TEXT NOT NULL,
    "bucket" TEXT NOT NULL,
    "legal_basis" TEXT,
    "retention_expires_at" TIMESTAMPTZ,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "data_portability_requests" (
    "request_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "requested_by" UUID NOT NULL,
    "format" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "delivery_method" TEXT,
    "delivered_at" TIMESTAMPTZ,
    "download_expires_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "staff_termination_authorizations" (
    "termination_id" UUID NOT NULL,
    "staff_profile_id" UUID NOT NULL,
    "termination_type" TEXT NOT NULL,
    "reason" TEXT,
    "primary_authorizer_id" UUID NOT NULL,
    "secondary_authorizer_id" UUID,
    "authorized_at" TIMESTAMPTZ NOT NULL,
    "effective_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "compliance_incidents" (
    "incident_id" UUID NOT NULL,
    "detected_at" TIMESTAMPTZ NOT NULL,
    "detected_by" UUID,
    "category" TEXT NOT NULL,
    "affected_data_categories" JSONB NOT NULL,
    "affected_patient_count" INTEGER,
    "severity" TEXT,
    "containment_actions" TEXT,
    "board_notified_at" TIMESTAMPTZ,
    "patients_notified_at" TIMESTAMPTZ,
    "eu_authority_notified_at" TIMESTAMPTZ,
    "remediation_summary" TEXT,
    "post_incident_review_at" TIMESTAMPTZ,
    "status" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

CREATE TABLE "manual_snapshots" (
    "snapshot_id" UUID NOT NULL,
    "purpose" TEXT NOT NULL,
    "created_by" UUID,
    "created_at" TIMESTAMPTZ NOT NULL,
    "intended_deletion_at" TIMESTAMPTZ NOT NULL,
    "deleted_at" TIMESTAMPTZ
);

-- ---- Primary keys (89) ----
ALTER TABLE "activity_logs" ADD CONSTRAINT "activity_logs_pkey" PRIMARY KEY ("log_id", "created_at");
ALTER TABLE "admins" ADD CONSTRAINT "admins_pkey" PRIMARY KEY ("admin_id");
ALTER TABLE "alembic_version" ADD CONSTRAINT "alembic_version_pkey" PRIMARY KEY ("version_num");
ALTER TABLE "anamnesis_assessments" ADD CONSTRAINT "anamnesis_assessments_pkey" PRIMARY KEY ("anamnesis_id");
ALTER TABLE "anamnesis_options" ADD CONSTRAINT "anamnesis_options_pkey" PRIMARY KEY ("option_id");
ALTER TABLE "anamnesis_questions" ADD CONSTRAINT "anamnesis_questions_pkey" PRIMARY KEY ("question_id");
ALTER TABLE "anamnesis_responses" ADD CONSTRAINT "anamnesis_responses_pkey" PRIMARY KEY ("response_id");
ALTER TABLE "appointment_audit_logs" ADD CONSTRAINT "appointment_audit_logs_pkey" PRIMARY KEY ("audit_id", "changed_at");
ALTER TABLE "appointment_requests" ADD CONSTRAINT "appointment_requests_pkey" PRIMARY KEY ("request_id");
ALTER TABLE "appointments" ADD CONSTRAINT "appointments_pkey" PRIMARY KEY ("appointment_id");
ALTER TABLE "assessment_protocol_requests" ADD CONSTRAINT "assessment_protocol_requests_pkey" PRIMARY KEY ("request_id");
ALTER TABLE "audit_logs" ADD CONSTRAINT "audit_logs_pkey" PRIMARY KEY ("log_id", "changed_at");
ALTER TABLE "ca_doctor_assignments" ADD CONSTRAINT "ca_doctor_assignments_pkey" PRIMARY KEY ("cda_id");
ALTER TABLE "clinic_requests" ADD CONSTRAINT "clinic_requests_pkey" PRIMARY KEY ("request_id");
ALTER TABLE "clinic_staff_assignments" ADD CONSTRAINT "clinic_staff_assignments_pkey" PRIMARY KEY ("assignment_id");
ALTER TABLE "clinical_assistants" ADD CONSTRAINT "clinical_assistants_pkey" PRIMARY KEY ("ca_id");
ALTER TABLE "clinics" ADD CONSTRAINT "clinics_pkey" PRIMARY KEY ("clinic_id");
ALTER TABLE "consent_records" ADD CONSTRAINT "consent_records_pkey" PRIMARY KEY ("consent_id");
ALTER TABLE "consent_templates" ADD CONSTRAINT "consent_templates_pkey" PRIMARY KEY ("template_id");
ALTER TABLE "device_assignments" ADD CONSTRAINT "device_assignments_pkey" PRIMARY KEY ("da_id");
ALTER TABLE "doctor_patient_assignments" ADD CONSTRAINT "doctor_patient_assignments_pkey" PRIMARY KEY ("assignment_id");
ALTER TABLE "doctor_schedule_overrides" ADD CONSTRAINT "doctor_schedule_overrides_pkey" PRIMARY KEY ("override_id");
ALTER TABLE "doctor_session_notes" ADD CONSTRAINT "doctor_session_notes_pkey" PRIMARY KEY ("note_id");
ALTER TABLE "doctor_weekly_schedules" ADD CONSTRAINT "doctor_weekly_schedules_pkey" PRIMARY KEY ("schedule_id");
ALTER TABLE "doctors" ADD CONSTRAINT "doctors_pkey" PRIMARY KEY ("doctor_id");
ALTER TABLE "inventory" ADD CONSTRAINT "inventory_pkey" PRIMARY KEY ("inventory_id");
ALTER TABLE "notifications" ADD CONSTRAINT "notifications_pkey" PRIMARY KEY ("notification_id", "created_at");
ALTER TABLE "order_items" ADD CONSTRAINT "order_items_pkey" PRIMARY KEY ("item_id");
ALTER TABLE "outbox_events" ADD CONSTRAINT "outbox_events_pkey" PRIMARY KEY ("outbox_id");
ALTER TABLE "patient_clinic_transfers" ADD CONSTRAINT "patient_clinic_transfers_pkey" PRIMARY KEY ("pct_id");
ALTER TABLE "patient_disease_selection" ADD CONSTRAINT "patient_disease_selection_pkey" PRIMARY KEY ("pds_id");
ALTER TABLE "patient_eeg_files" ADD CONSTRAINT "patient_eeg_files_pkey" PRIMARY KEY ("eeg_id");
ALTER TABLE "patient_medical_history_files" ADD CONSTRAINT "patient_medical_history_files_pkey" PRIMARY KEY ("mhf_id");
ALTER TABLE "patient_scale_assignments" ADD CONSTRAINT "patient_scale_assignments_pkey" PRIMARY KEY ("psa_id");
ALTER TABLE "patients" ADD CONSTRAINT "patients_pkey" PRIMARY KEY ("patient_id");
ALTER TABLE "payments" ADD CONSTRAINT "payments_pkey" PRIMARY KEY ("payment_id");
ALTER TABLE "products" ADD CONSTRAINT "products_pkey" PRIMARY KEY ("product_id");
ALTER TABLE "profiles" ADD CONSTRAINT "profiles_pkey" PRIMARY KEY ("id");
ALTER TABLE "prs_assessment_instances" ADD CONSTRAINT "prs_assessment_instances_pkey" PRIMARY KEY ("instance_id");
ALTER TABLE "prs_disease_question_map" ADD CONSTRAINT "prs_disease_question_map_pkey" PRIMARY KEY ("dq_map_id");
ALTER TABLE "prs_disease_scale_map" ADD CONSTRAINT "prs_disease_scale_map_pkey" PRIMARY KEY ("ds_map_id");
ALTER TABLE "prs_diseases" ADD CONSTRAINT "prs_diseases_pkey" PRIMARY KEY ("disease_id");
ALTER TABLE "prs_final_results" ADD CONSTRAINT "prs_final_results_pkey" PRIMARY KEY ("final_result_id");
ALTER TABLE "prs_option_translations" ADD CONSTRAINT "prs_option_translations_pkey" PRIMARY KEY ("option_id", "language_code");
ALTER TABLE "prs_options" ADD CONSTRAINT "prs_options_pkey" PRIMARY KEY ("option_id");
ALTER TABLE "prs_question_translations" ADD CONSTRAINT "prs_question_translations_pkey" PRIMARY KEY ("question_id", "language_code");
ALTER TABLE "prs_questions" ADD CONSTRAINT "prs_questions_pkey" PRIMARY KEY ("question_id");
ALTER TABLE "prs_responses" ADD CONSTRAINT "prs_responses_pkey" PRIMARY KEY ("response_id");
ALTER TABLE "prs_scale_question_map" ADD CONSTRAINT "prs_scale_question_map_pkey" PRIMARY KEY ("sq_map_id");
ALTER TABLE "prs_scale_results" ADD CONSTRAINT "prs_scale_results_pkey" PRIMARY KEY ("scale_result_id");
ALTER TABLE "prs_scales" ADD CONSTRAINT "prs_scales_pkey" PRIMARY KEY ("scale_id");
ALTER TABLE "receptionists" ADD CONSTRAINT "receptionists_pkey" PRIMARY KEY ("receptionist_id");
ALTER TABLE "regions" ADD CONSTRAINT "regions_pkey" PRIMARY KEY ("region_id");
ALTER TABLE "schema_migrations" ADD CONSTRAINT "schema_migrations_pkey" PRIMARY KEY ("version");
ALTER TABLE "sessions" ADD CONSTRAINT "sessions_pkey" PRIMARY KEY ("session_id");
ALTER TABLE "staff_requests" ADD CONSTRAINT "staff_requests_pkey" PRIMARY KEY ("request_id");
ALTER TABLE "stock_transfers" ADD CONSTRAINT "stock_transfers_pkey" PRIMARY KEY ("st_id");
ALTER TABLE "store_orders" ADD CONSTRAINT "store_orders_pkey" PRIMARY KEY ("order_id");
ALTER TABLE "treatment_cycles" ADD CONSTRAINT "treatment_cycles_pkey" PRIMARY KEY ("cycle_id");
ALTER TABLE "treatment_plans" ADD CONSTRAINT "treatment_plans_pkey" PRIMARY KEY ("plan_id");
ALTER TABLE "treatment_sessions" ADD CONSTRAINT "treatment_sessions_pkey" PRIMARY KEY ("ts_id", "created_at");
ALTER TABLE "neuromod_devices" ADD CONSTRAINT "neuromod_devices_pkey" PRIMARY KEY ("device_id");
ALTER TABLE "neuromod_conditions" ADD CONSTRAINT "neuromod_conditions_pkey" PRIMARY KEY ("condition_id");
ALTER TABLE "neuromod_diagnoses" ADD CONSTRAINT "neuromod_diagnoses_pkey" PRIMARY KEY ("diagnosis_id");
ALTER TABLE "neuromod_scales" ADD CONSTRAINT "neuromod_scales_pkey" PRIMARY KEY ("scale_id");
ALTER TABLE "neuromod_condition_scales" ADD CONSTRAINT "neuromod_condition_scales_pkey" PRIMARY KEY ("cs_map_id");
ALTER TABLE "tdcs_placements" ADD CONSTRAINT "tdcs_placements_pkey" PRIMARY KEY ("tdcs_placement_id");
ALTER TABLE "hd_tdcs_placements" ADD CONSTRAINT "hd_tdcs_placements_pkey" PRIMARY KEY ("hd_tdcs_placement_id");
ALTER TABLE "tavns_placements" ADD CONSTRAINT "tavns_placements_pkey" PRIMARY KEY ("tavns_placement_id");
ALTER TABLE "tps_placements" ADD CONSTRAINT "tps_placements_pkey" PRIMARY KEY ("tps_placement_id");
ALTER TABLE "rtms_placements" ADD CONSTRAINT "rtms_placements_pkey" PRIMARY KEY ("rtms_placement_id");
ALTER TABLE "other_placements" ADD CONSTRAINT "other_placements_pkey" PRIMARY KEY ("other_placement_id");
ALTER TABLE "tdcs_dosing" ADD CONSTRAINT "tdcs_dosing_pkey" PRIMARY KEY ("tdcs_dosing_id");
ALTER TABLE "hd_tdcs_dosing" ADD CONSTRAINT "hd_tdcs_dosing_pkey" PRIMARY KEY ("hd_tdcs_dosing_id");
ALTER TABLE "tavns_dosing" ADD CONSTRAINT "tavns_dosing_pkey" PRIMARY KEY ("tavns_dosing_id");
ALTER TABLE "tps_dosing" ADD CONSTRAINT "tps_dosing_pkey" PRIMARY KEY ("tps_dosing_id");
ALTER TABLE "rtms_dosing" ADD CONSTRAINT "rtms_dosing_pkey" PRIMARY KEY ("rtms_dosing_id");
ALTER TABLE "other_dosing" ADD CONSTRAINT "other_dosing_pkey" PRIMARY KEY ("other_dosing_id");
ALTER TABLE "treatment_protocols" ADD CONSTRAINT "treatment_protocols_pkey" PRIMARY KEY ("protocol_id");
ALTER TABLE "protocol_sessions" ADD CONSTRAINT "protocol_sessions_pkey" PRIMARY KEY ("protocol_session_id");
ALTER TABLE "protocol_followups" ADD CONSTRAINT "protocol_followups_pkey" PRIMARY KEY ("protocol_followup_id");
ALTER TABLE "device_session_prs_responses" ADD CONSTRAINT "device_session_prs_responses_pkey" PRIMARY KEY ("ds_prs_id");
ALTER TABLE "followup_prs_responses" ADD CONSTRAINT "followup_prs_responses_pkey" PRIMARY KEY ("fu_prs_id");
ALTER TABLE "erasure_requests" ADD CONSTRAINT "erasure_requests_pkey" PRIMARY KEY ("request_id");
ALTER TABLE "erasure_request_items" ADD CONSTRAINT "erasure_request_items_pkey" PRIMARY KEY ("item_id");
ALTER TABLE "data_portability_requests" ADD CONSTRAINT "data_portability_requests_pkey" PRIMARY KEY ("request_id");
ALTER TABLE "staff_termination_authorizations" ADD CONSTRAINT "staff_termination_authorizations_pkey" PRIMARY KEY ("termination_id");
ALTER TABLE "compliance_incidents" ADD CONSTRAINT "compliance_incidents_pkey" PRIMARY KEY ("incident_id");
ALTER TABLE "manual_snapshots" ADD CONSTRAINT "manual_snapshots_pkey" PRIMARY KEY ("snapshot_id");

-- ---- Foreign keys (259; all ON DELETE RESTRICT in erd.sql) ----
ALTER TABLE "erasure_requests" ADD CONSTRAINT "fk_erasure_requests_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "erasure_requests" ADD CONSTRAINT "fk_erasure_requests_requested_by" FOREIGN KEY ("requested_by") REFERENCES "profiles" ("id");
ALTER TABLE "erasure_request_items" ADD CONSTRAINT "fk_erasure_request_items_request_id" FOREIGN KEY ("request_id") REFERENCES "erasure_requests" ("request_id");
ALTER TABLE "data_portability_requests" ADD CONSTRAINT "fk_data_portability_requests_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "data_portability_requests" ADD CONSTRAINT "fk_data_portability_requests_requested_by" FOREIGN KEY ("requested_by") REFERENCES "profiles" ("id");
ALTER TABLE "staff_termination_authorizations" ADD CONSTRAINT "fk_staff_term_staff_profile_id" FOREIGN KEY ("staff_profile_id") REFERENCES "profiles" ("id");
ALTER TABLE "staff_termination_authorizations" ADD CONSTRAINT "fk_staff_term_primary_authorizer" FOREIGN KEY ("primary_authorizer_id") REFERENCES "profiles" ("id");
ALTER TABLE "staff_termination_authorizations" ADD CONSTRAINT "fk_staff_term_secondary_authorizer" FOREIGN KEY ("secondary_authorizer_id") REFERENCES "profiles" ("id");
ALTER TABLE "compliance_incidents" ADD CONSTRAINT "fk_compliance_incidents_detected_by" FOREIGN KEY ("detected_by") REFERENCES "profiles" ("id");
ALTER TABLE "manual_snapshots" ADD CONSTRAINT "fk_manual_snapshots_created_by" FOREIGN KEY ("created_by") REFERENCES "profiles" ("id");
ALTER TABLE "consent_records" ADD CONSTRAINT "fk_consent_records_guardian_id" FOREIGN KEY ("guardian_id") REFERENCES "profiles" ("id");
ALTER TABLE "activity_logs" ADD CONSTRAINT "fk_activity_logs_actor_id" FOREIGN KEY ("actor_id") REFERENCES "profiles" ("id");
ALTER TABLE "activity_logs" ADD CONSTRAINT "fk_activity_logs_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "activity_logs" ADD CONSTRAINT "fk_activity_logs_region_id" FOREIGN KEY ("region_id") REFERENCES "regions" ("region_id");
ALTER TABLE "admins" ADD CONSTRAINT "fk_admins_profile_id" FOREIGN KEY ("profile_id") REFERENCES "profiles" ("id");
ALTER TABLE "admins" ADD CONSTRAINT "fk_admins_region_id" FOREIGN KEY ("region_id") REFERENCES "regions" ("region_id");
ALTER TABLE "admins" ADD CONSTRAINT "fk_admins_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "anamnesis_assessments" ADD CONSTRAINT "fk_anamnesis_assessments_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "anamnesis_assessments" ADD CONSTRAINT "fk_anamnesis_assessments_submitted_by" FOREIGN KEY ("submitted_by") REFERENCES "profiles" ("id");
ALTER TABLE "anamnesis_assessments" ADD CONSTRAINT "fk_anamnesis_assessments_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES "treatment_cycles" ("cycle_id");
ALTER TABLE "anamnesis_options" ADD CONSTRAINT "fk_anamnesis_options_question_id" FOREIGN KEY ("question_id") REFERENCES "anamnesis_questions" ("question_id");
ALTER TABLE "anamnesis_questions" ADD CONSTRAINT "fk_anamnesis_questions_depends_on_question_id" FOREIGN KEY ("depends_on_question_id") REFERENCES "anamnesis_questions" ("question_id");
ALTER TABLE "anamnesis_responses" ADD CONSTRAINT "fk_anamnesis_responses_anamnesis_id" FOREIGN KEY ("anamnesis_id") REFERENCES "anamnesis_assessments" ("anamnesis_id");
ALTER TABLE "anamnesis_responses" ADD CONSTRAINT "fk_anamnesis_responses_question_id" FOREIGN KEY ("question_id") REFERENCES "anamnesis_questions" ("question_id");
ALTER TABLE "appointment_audit_logs" ADD CONSTRAINT "fk_appointment_audit_logs_appointment_id" FOREIGN KEY ("appointment_id") REFERENCES "appointments" ("appointment_id");
ALTER TABLE "appointment_audit_logs" ADD CONSTRAINT "fk_appointment_audit_logs_changed_by" FOREIGN KEY ("changed_by") REFERENCES "profiles" ("id");
ALTER TABLE "appointment_requests" ADD CONSTRAINT "fk_appointment_requests_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "appointment_requests" ADD CONSTRAINT "fk_appointment_requests_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "appointment_requests" ADD CONSTRAINT "fk_appointment_requests_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES "profiles" ("id");
ALTER TABLE "appointment_requests" ADD CONSTRAINT "fk_appointment_requests_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES "treatment_cycles" ("cycle_id");
ALTER TABLE "appointment_requests" ADD CONSTRAINT "fk_appointment_requests_parent_appointment_id" FOREIGN KEY ("parent_appointment_id") REFERENCES "appointments" ("appointment_id");
ALTER TABLE "appointment_requests" ADD CONSTRAINT "fk_appointment_requests_approved_appointment_id" FOREIGN KEY ("approved_appointment_id") REFERENCES "appointments" ("appointment_id");
ALTER TABLE "appointment_requests" ADD CONSTRAINT "fk_appointment_requests_submitted_by" FOREIGN KEY ("submitted_by") REFERENCES "profiles" ("id");
ALTER TABLE "appointment_requests" ADD CONSTRAINT "fk_appointment_requests_reviewed_by" FOREIGN KEY ("reviewed_by") REFERENCES "profiles" ("id");
ALTER TABLE "appointments" ADD CONSTRAINT "fk_appointments_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "appointments" ADD CONSTRAINT "fk_appointments_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "appointments" ADD CONSTRAINT "fk_appointments_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES "profiles" ("id");
ALTER TABLE "appointments" ADD CONSTRAINT "fk_appointments_ca_id" FOREIGN KEY ("ca_id") REFERENCES "profiles" ("id");
ALTER TABLE "appointments" ADD CONSTRAINT "fk_appointments_session_id" FOREIGN KEY ("session_id") REFERENCES "sessions" ("session_id");
ALTER TABLE "appointments" ADD CONSTRAINT "fk_appointments_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES "treatment_cycles" ("cycle_id");
ALTER TABLE "appointments" ADD CONSTRAINT "fk_appointments_appointment_request_id" FOREIGN KEY ("appointment_request_id") REFERENCES "appointment_requests" ("request_id");
ALTER TABLE "appointments" ADD CONSTRAINT "fk_appointments_booked_by" FOREIGN KEY ("booked_by") REFERENCES "profiles" ("id");
ALTER TABLE "appointments" ADD CONSTRAINT "fk_appointments_cancelled_by" FOREIGN KEY ("cancelled_by") REFERENCES "profiles" ("id");
ALTER TABLE "appointments" ADD CONSTRAINT "fk_appointments_rescheduled_from" FOREIGN KEY ("rescheduled_from") REFERENCES "appointments" ("appointment_id");
ALTER TABLE "appointments" ADD CONSTRAINT "fk_appointments_rescheduled_to" FOREIGN KEY ("rescheduled_to") REFERENCES "appointments" ("appointment_id");
ALTER TABLE "assessment_protocol_requests" ADD CONSTRAINT "fk_assessment_protocol_requests_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "assessment_protocol_requests" ADD CONSTRAINT "fk_assessment_protocol_requests_clinical_assistant_id" FOREIGN KEY ("clinical_assistant_id") REFERENCES "profiles" ("id");
ALTER TABLE "assessment_protocol_requests" ADD CONSTRAINT "fk_assessment_protocol_requests_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES "profiles" ("id");
ALTER TABLE "assessment_protocol_requests" ADD CONSTRAINT "fk_assessment_protocol_requests_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "assessment_protocol_requests" ADD CONSTRAINT "fk_assessment_protocol_requests_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES "treatment_cycles" ("cycle_id");
ALTER TABLE "audit_logs" ADD CONSTRAINT "fk_audit_logs_changed_by" FOREIGN KEY ("changed_by") REFERENCES "profiles" ("id");
ALTER TABLE "audit_logs" ADD CONSTRAINT "fk_audit_logs_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "ca_doctor_assignments" ADD CONSTRAINT "fk_ca_doctor_assignments_ca_id" FOREIGN KEY ("ca_id") REFERENCES "clinical_assistants" ("ca_id");
ALTER TABLE "ca_doctor_assignments" ADD CONSTRAINT "fk_ca_doctor_assignments_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES "doctors" ("doctor_id");
ALTER TABLE "ca_doctor_assignments" ADD CONSTRAINT "fk_ca_doctor_assignments_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "clinic_requests" ADD CONSTRAINT "fk_clinic_requests_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "clinic_requests" ADD CONSTRAINT "fk_clinic_requests_region_id" FOREIGN KEY ("region_id") REFERENCES "regions" ("region_id");
ALTER TABLE "clinic_requests" ADD CONSTRAINT "fk_clinic_requests_submitted_by" FOREIGN KEY ("submitted_by") REFERENCES "profiles" ("id");
ALTER TABLE "clinic_requests" ADD CONSTRAINT "fk_clinic_requests_reviewed_by" FOREIGN KEY ("reviewed_by") REFERENCES "profiles" ("id");
ALTER TABLE "clinic_staff_assignments" ADD CONSTRAINT "fk_clinic_staff_assignments_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "clinic_staff_assignments" ADD CONSTRAINT "fk_clinic_staff_assignments_profile_id" FOREIGN KEY ("profile_id") REFERENCES "profiles" ("id");
ALTER TABLE "clinical_assistants" ADD CONSTRAINT "fk_clinical_assistants_profile_id" FOREIGN KEY ("profile_id") REFERENCES "profiles" ("id");
ALTER TABLE "clinical_assistants" ADD CONSTRAINT "fk_clinical_assistants_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "clinical_assistants" ADD CONSTRAINT "fk_clinical_assistants_deleted_by" FOREIGN KEY ("deleted_by") REFERENCES "profiles" ("id");
ALTER TABLE "clinics" ADD CONSTRAINT "fk_clinics_region_id" FOREIGN KEY ("region_id") REFERENCES "regions" ("region_id");
ALTER TABLE "clinics" ADD CONSTRAINT "fk_clinics_clinic_admin_id" FOREIGN KEY ("clinic_admin_id") REFERENCES "profiles" ("id");
ALTER TABLE "consent_records" ADD CONSTRAINT "fk_consent_records_template_id" FOREIGN KEY ("template_id") REFERENCES "consent_templates" ("template_id");
ALTER TABLE "consent_records" ADD CONSTRAINT "fk_consent_records_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "consent_records" ADD CONSTRAINT "fk_consent_records_staff_id" FOREIGN KEY ("staff_id") REFERENCES "profiles" ("id");
ALTER TABLE "consent_records" ADD CONSTRAINT "fk_consent_records_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "consent_records" ADD CONSTRAINT "fk_consent_records_signed_by" FOREIGN KEY ("signed_by") REFERENCES "profiles" ("id");
ALTER TABLE "consent_records" ADD CONSTRAINT "fk_consent_records_witness_id" FOREIGN KEY ("witness_id") REFERENCES "profiles" ("id");
ALTER TABLE "consent_records" ADD CONSTRAINT "fk_consent_records_revoked_by" FOREIGN KEY ("revoked_by") REFERENCES "profiles" ("id");
ALTER TABLE "consent_records" ADD CONSTRAINT "fk_consent_records_region_id" FOREIGN KEY ("region_id") REFERENCES "regions" ("region_id");
ALTER TABLE "device_assignments" ADD CONSTRAINT "fk_device_assignments_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "device_assignments" ADD CONSTRAINT "fk_device_assignments_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "device_assignments" ADD CONSTRAINT "fk_device_assignments_plan_id" FOREIGN KEY ("plan_id") REFERENCES "treatment_plans" ("plan_id");
ALTER TABLE "device_assignments" ADD CONSTRAINT "fk_device_assignments_assigned_by" FOREIGN KEY ("assigned_by") REFERENCES "profiles" ("id");
ALTER TABLE "device_assignments" ADD CONSTRAINT "fk_device_assignments_order_id" FOREIGN KEY ("order_id") REFERENCES "store_orders" ("order_id");
ALTER TABLE "device_assignments" ADD CONSTRAINT "fk_device_assignments_returned_by" FOREIGN KEY ("returned_by") REFERENCES "profiles" ("id");
ALTER TABLE "doctor_patient_assignments" ADD CONSTRAINT "fk_doctor_patient_assignments_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES "profiles" ("id");
ALTER TABLE "doctor_patient_assignments" ADD CONSTRAINT "fk_doctor_patient_assignments_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "doctor_patient_assignments" ADD CONSTRAINT "fk_doctor_patient_assignments_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "doctor_schedule_overrides" ADD CONSTRAINT "fk_doctor_schedule_overrides_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES "profiles" ("id");
ALTER TABLE "doctor_schedule_overrides" ADD CONSTRAINT "fk_doctor_schedule_overrides_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "doctor_schedule_overrides" ADD CONSTRAINT "fk_doctor_schedule_overrides_created_by" FOREIGN KEY ("created_by") REFERENCES "profiles" ("id");
ALTER TABLE "doctor_session_notes" ADD CONSTRAINT "fk_doctor_session_notes_session_id" FOREIGN KEY ("session_id") REFERENCES "sessions" ("session_id");
ALTER TABLE "doctor_session_notes" ADD CONSTRAINT "fk_doctor_session_notes_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES "treatment_cycles" ("cycle_id");
ALTER TABLE "doctor_session_notes" ADD CONSTRAINT "fk_doctor_session_notes_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "doctor_session_notes" ADD CONSTRAINT "fk_doctor_session_notes_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES "profiles" ("id");
ALTER TABLE "doctor_weekly_schedules" ADD CONSTRAINT "fk_doctor_weekly_schedules_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES "profiles" ("id");
ALTER TABLE "doctor_weekly_schedules" ADD CONSTRAINT "fk_doctor_weekly_schedules_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "doctor_weekly_schedules" ADD CONSTRAINT "fk_doctor_weekly_schedules_created_by" FOREIGN KEY ("created_by") REFERENCES "profiles" ("id");
ALTER TABLE "doctors" ADD CONSTRAINT "fk_doctors_profile_id" FOREIGN KEY ("profile_id") REFERENCES "profiles" ("id");
ALTER TABLE "doctors" ADD CONSTRAINT "fk_doctors_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "doctors" ADD CONSTRAINT "fk_doctors_deleted_by" FOREIGN KEY ("deleted_by") REFERENCES "profiles" ("id");
ALTER TABLE "inventory" ADD CONSTRAINT "fk_inventory_product_id" FOREIGN KEY ("product_id") REFERENCES "products" ("product_id");
ALTER TABLE "inventory" ADD CONSTRAINT "fk_inventory_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "notifications" ADD CONSTRAINT "fk_notifications_recipient_id" FOREIGN KEY ("recipient_id") REFERENCES "profiles" ("id");
ALTER TABLE "notifications" ADD CONSTRAINT "fk_notifications_sender_id" FOREIGN KEY ("sender_id") REFERENCES "profiles" ("id");
ALTER TABLE "notifications" ADD CONSTRAINT "fk_notifications_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "order_items" ADD CONSTRAINT "fk_order_items_order_id" FOREIGN KEY ("order_id") REFERENCES "store_orders" ("order_id");
ALTER TABLE "order_items" ADD CONSTRAINT "fk_order_items_product_id" FOREIGN KEY ("product_id") REFERENCES "products" ("product_id");
ALTER TABLE "patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_from_clinic_id" FOREIGN KEY ("from_clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_to_clinic_id" FOREIGN KEY ("to_clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_from_doctor_id" FOREIGN KEY ("from_doctor_id") REFERENCES "profiles" ("id");
ALTER TABLE "patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_to_doctor_id" FOREIGN KEY ("to_doctor_id") REFERENCES "profiles" ("id");
ALTER TABLE "patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_active_cycle_id" FOREIGN KEY ("active_cycle_id") REFERENCES "treatment_cycles" ("cycle_id");
ALTER TABLE "patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_consent_id" FOREIGN KEY ("consent_id") REFERENCES "consent_records" ("consent_id");
ALTER TABLE "patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_initiated_by" FOREIGN KEY ("initiated_by") REFERENCES "profiles" ("id");
ALTER TABLE "patient_disease_selection" ADD CONSTRAINT "fk_patient_disease_selection_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "patient_disease_selection" ADD CONSTRAINT "fk_patient_disease_selection_disease_id" FOREIGN KEY ("disease_id") REFERENCES "prs_diseases" ("disease_id");
ALTER TABLE "patient_eeg_files" ADD CONSTRAINT "fk_patient_eeg_files_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "patient_eeg_files" ADD CONSTRAINT "fk_patient_eeg_files_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "patient_eeg_files" ADD CONSTRAINT "fk_patient_eeg_files_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES "treatment_cycles" ("cycle_id");
ALTER TABLE "patient_eeg_files" ADD CONSTRAINT "fk_patient_eeg_files_session_id" FOREIGN KEY ("session_id") REFERENCES "sessions" ("session_id");
ALTER TABLE "patient_eeg_files" ADD CONSTRAINT "fk_patient_eeg_files_performed_by" FOREIGN KEY ("performed_by") REFERENCES "profiles" ("id");
ALTER TABLE "patient_eeg_files" ADD CONSTRAINT "fk_patient_eeg_files_reviewed_by" FOREIGN KEY ("reviewed_by") REFERENCES "profiles" ("id");
ALTER TABLE "patient_eeg_files" ADD CONSTRAINT "fk_patient_eeg_files_superseded_by" FOREIGN KEY ("superseded_by") REFERENCES "patient_eeg_files" ("eeg_id");
ALTER TABLE "patient_medical_history_files" ADD CONSTRAINT "fk_patient_medical_history_files_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "patient_medical_history_files" ADD CONSTRAINT "fk_patient_medical_history_files_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "patient_medical_history_files" ADD CONSTRAINT "fk_patient_medical_history_files_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES "treatment_cycles" ("cycle_id");
ALTER TABLE "patient_medical_history_files" ADD CONSTRAINT "fk_patient_medical_history_files_uploaded_by" FOREIGN KEY ("uploaded_by") REFERENCES "profiles" ("id");
ALTER TABLE "patient_medical_history_files" ADD CONSTRAINT "fk_patient_medical_history_files_deleted_by" FOREIGN KEY ("deleted_by") REFERENCES "profiles" ("id");
ALTER TABLE "patient_scale_assignments" ADD CONSTRAINT "fk_patient_scale_assignments_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "patient_scale_assignments" ADD CONSTRAINT "fk_patient_scale_assignments_scale_id" FOREIGN KEY ("scale_id") REFERENCES "prs_scales" ("scale_id");
ALTER TABLE "patient_scale_assignments" ADD CONSTRAINT "fk_patient_scale_assignments_assigned_by" FOREIGN KEY ("assigned_by") REFERENCES "profiles" ("id");
ALTER TABLE "patient_scale_assignments" ADD CONSTRAINT "fk_patient_scale_assignments_deactivated_by" FOREIGN KEY ("deactivated_by") REFERENCES "profiles" ("id");
ALTER TABLE "patient_scale_assignments" ADD CONSTRAINT "fk_patient_scale_assignments_disease_id" FOREIGN KEY ("disease_id") REFERENCES "prs_diseases" ("disease_id");
ALTER TABLE "patients" ADD CONSTRAINT "fk_patients_profile_id" FOREIGN KEY ("profile_id") REFERENCES "profiles" ("id");
ALTER TABLE "patients" ADD CONSTRAINT "fk_patients_primary_clinic_id" FOREIGN KEY ("primary_clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "patients" ADD CONSTRAINT "fk_patients_primary_doctor_id" FOREIGN KEY ("primary_doctor_id") REFERENCES "profiles" ("id");
ALTER TABLE "patients" ADD CONSTRAINT "fk_patients_deleted_by" FOREIGN KEY ("deleted_by") REFERENCES "profiles" ("id");
ALTER TABLE "patients" ADD CONSTRAINT "fk_patients_approved_by" FOREIGN KEY ("approved_by") REFERENCES "profiles" ("id");
ALTER TABLE "payments" ADD CONSTRAINT "fk_payments_session_id" FOREIGN KEY ("session_id") REFERENCES "sessions" ("session_id");
ALTER TABLE "payments" ADD CONSTRAINT "fk_payments_order_id" FOREIGN KEY ("order_id") REFERENCES "store_orders" ("order_id");
ALTER TABLE "payments" ADD CONSTRAINT "fk_payments_waived_by" FOREIGN KEY ("waived_by") REFERENCES "profiles" ("id");
ALTER TABLE "profiles" ADD CONSTRAINT "fk_profiles_deleted_by" FOREIGN KEY ("deleted_by") REFERENCES "profiles" ("id");
ALTER TABLE "prs_assessment_instances" ADD CONSTRAINT "fk_prs_assessment_instances_disease_id" FOREIGN KEY ("disease_id") REFERENCES "prs_diseases" ("disease_id");
ALTER TABLE "prs_assessment_instances" ADD CONSTRAINT "fk_prs_assessment_instances_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "prs_assessment_instances" ADD CONSTRAINT "fk_prs_assessment_instances_session_id" FOREIGN KEY ("session_id") REFERENCES "sessions" ("session_id");
ALTER TABLE "prs_assessment_instances" ADD CONSTRAINT "fk_prs_assessment_instances_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES "treatment_cycles" ("cycle_id");
ALTER TABLE "prs_assessment_instances" ADD CONSTRAINT "fk_prs_assessment_instances_administered_by" FOREIGN KEY ("administered_by") REFERENCES "profiles" ("id");
ALTER TABLE "prs_disease_question_map" ADD CONSTRAINT "fk_prs_disease_question_map_disease_id" FOREIGN KEY ("disease_id") REFERENCES "prs_diseases" ("disease_id");
ALTER TABLE "prs_disease_question_map" ADD CONSTRAINT "fk_prs_disease_question_map_question_id" FOREIGN KEY ("question_id") REFERENCES "prs_questions" ("question_id");
ALTER TABLE "prs_disease_scale_map" ADD CONSTRAINT "fk_prs_disease_scale_map_disease_id" FOREIGN KEY ("disease_id") REFERENCES "prs_diseases" ("disease_id");
ALTER TABLE "prs_disease_scale_map" ADD CONSTRAINT "fk_prs_disease_scale_map_scale_id" FOREIGN KEY ("scale_id") REFERENCES "prs_scales" ("scale_id");
ALTER TABLE "prs_final_results" ADD CONSTRAINT "fk_prs_final_results_instance_id" FOREIGN KEY ("instance_id") REFERENCES "prs_assessment_instances" ("instance_id");
ALTER TABLE "prs_option_translations" ADD CONSTRAINT "fk_prs_option_translations_option_id" FOREIGN KEY ("option_id") REFERENCES "prs_options" ("option_id");
ALTER TABLE "prs_options" ADD CONSTRAINT "fk_prs_options_question_id" FOREIGN KEY ("question_id") REFERENCES "prs_questions" ("question_id");
ALTER TABLE "prs_question_translations" ADD CONSTRAINT "fk_prs_question_translations_question_id" FOREIGN KEY ("question_id") REFERENCES "prs_questions" ("question_id");
ALTER TABLE "prs_questions" ADD CONSTRAINT "fk_prs_questions_disease_id" FOREIGN KEY ("disease_id") REFERENCES "prs_diseases" ("disease_id");
ALTER TABLE "prs_questions" ADD CONSTRAINT "fk_prs_questions_scale_id" FOREIGN KEY ("scale_id") REFERENCES "prs_scales" ("scale_id");
ALTER TABLE "prs_questions" ADD CONSTRAINT "fk_prs_questions_ds_map_id" FOREIGN KEY ("ds_map_id") REFERENCES "prs_disease_scale_map" ("ds_map_id");
ALTER TABLE "prs_responses" ADD CONSTRAINT "fk_prs_responses_instance_id" FOREIGN KEY ("instance_id") REFERENCES "prs_assessment_instances" ("instance_id");
ALTER TABLE "prs_responses" ADD CONSTRAINT "fk_prs_responses_question_id" FOREIGN KEY ("question_id") REFERENCES "prs_questions" ("question_id");
ALTER TABLE "prs_scale_question_map" ADD CONSTRAINT "fk_prs_scale_question_map_scale_id" FOREIGN KEY ("scale_id") REFERENCES "prs_scales" ("scale_id");
ALTER TABLE "prs_scale_question_map" ADD CONSTRAINT "fk_prs_scale_question_map_question_id" FOREIGN KEY ("question_id") REFERENCES "prs_questions" ("question_id");
ALTER TABLE "prs_scale_results" ADD CONSTRAINT "fk_prs_scale_results_instance_id" FOREIGN KEY ("instance_id") REFERENCES "prs_assessment_instances" ("instance_id");
ALTER TABLE "prs_scale_results" ADD CONSTRAINT "fk_prs_scale_results_scale_id" FOREIGN KEY ("scale_id") REFERENCES "prs_scales" ("scale_id");
ALTER TABLE "receptionists" ADD CONSTRAINT "fk_receptionists_profile_id" FOREIGN KEY ("profile_id") REFERENCES "profiles" ("id");
ALTER TABLE "receptionists" ADD CONSTRAINT "fk_receptionists_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "receptionists" ADD CONSTRAINT "fk_receptionists_deleted_by" FOREIGN KEY ("deleted_by") REFERENCES "profiles" ("id");
ALTER TABLE "regions" ADD CONSTRAINT "fk_regions_regional_admin_id" FOREIGN KEY ("regional_admin_id") REFERENCES "profiles" ("id");
ALTER TABLE "sessions" ADD CONSTRAINT "fk_sessions_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "sessions" ADD CONSTRAINT "fk_sessions_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES "profiles" ("id");
ALTER TABLE "sessions" ADD CONSTRAINT "fk_sessions_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES "treatment_cycles" ("cycle_id");
ALTER TABLE "sessions" ADD CONSTRAINT "fk_sessions_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "sessions" ADD CONSTRAINT "fk_sessions_ca_id" FOREIGN KEY ("ca_id") REFERENCES "profiles" ("id");
ALTER TABLE "staff_requests" ADD CONSTRAINT "fk_staff_requests_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "staff_requests" ADD CONSTRAINT "fk_staff_requests_regional_admin_id" FOREIGN KEY ("regional_admin_id") REFERENCES "profiles" ("id");
ALTER TABLE "staff_requests" ADD CONSTRAINT "fk_staff_requests_submitted_by" FOREIGN KEY ("submitted_by") REFERENCES "profiles" ("id");
ALTER TABLE "staff_requests" ADD CONSTRAINT "fk_staff_requests_reviewed_by" FOREIGN KEY ("reviewed_by") REFERENCES "profiles" ("id");
ALTER TABLE "staff_requests" ADD CONSTRAINT "fk_staff_requests_fulfilled_profile_id" FOREIGN KEY ("fulfilled_profile_id") REFERENCES "profiles" ("id");
ALTER TABLE "stock_transfers" ADD CONSTRAINT "fk_stock_transfers_product_id" FOREIGN KEY ("product_id") REFERENCES "products" ("product_id");
ALTER TABLE "stock_transfers" ADD CONSTRAINT "fk_stock_transfers_from_clinic_id" FOREIGN KEY ("from_clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "stock_transfers" ADD CONSTRAINT "fk_stock_transfers_to_clinic_id" FOREIGN KEY ("to_clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "stock_transfers" ADD CONSTRAINT "fk_stock_transfers_order_id" FOREIGN KEY ("order_id") REFERENCES "store_orders" ("order_id");
ALTER TABLE "stock_transfers" ADD CONSTRAINT "fk_stock_transfers_initiated_by" FOREIGN KEY ("initiated_by") REFERENCES "profiles" ("id");
ALTER TABLE "stock_transfers" ADD CONSTRAINT "fk_stock_transfers_received_by" FOREIGN KEY ("received_by") REFERENCES "profiles" ("id");
ALTER TABLE "store_orders" ADD CONSTRAINT "fk_store_orders_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "store_orders" ADD CONSTRAINT "fk_store_orders_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "store_orders" ADD CONSTRAINT "fk_store_orders_initiated_by" FOREIGN KEY ("initiated_by") REFERENCES "profiles" ("id");
ALTER TABLE "store_orders" ADD CONSTRAINT "fk_store_orders_approved_by" FOREIGN KEY ("approved_by") REFERENCES "profiles" ("id");
ALTER TABLE "store_orders" ADD CONSTRAINT "fk_store_orders_treatment_plan_id" FOREIGN KEY ("treatment_plan_id") REFERENCES "treatment_plans" ("plan_id");
ALTER TABLE "store_orders" ADD CONSTRAINT "fk_store_orders_cancelled_by" FOREIGN KEY ("cancelled_by") REFERENCES "profiles" ("id");
ALTER TABLE "treatment_cycles" ADD CONSTRAINT "fk_treatment_cycles_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "treatment_cycles" ADD CONSTRAINT "fk_treatment_cycles_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES "profiles" ("id");
ALTER TABLE "treatment_cycles" ADD CONSTRAINT "fk_treatment_cycles_ca_id" FOREIGN KEY ("ca_id") REFERENCES "profiles" ("id");
ALTER TABLE "treatment_cycles" ADD CONSTRAINT "fk_treatment_cycles_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "clinics" ("clinic_id");
ALTER TABLE "treatment_plans" ADD CONSTRAINT "fk_treatment_plans_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "treatment_plans" ADD CONSTRAINT "fk_treatment_plans_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES "profiles" ("id");
ALTER TABLE "treatment_plans" ADD CONSTRAINT "fk_treatment_plans_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES "treatment_cycles" ("cycle_id");
ALTER TABLE "treatment_plans" ADD CONSTRAINT "fk_treatment_plans_parent_plan_id" FOREIGN KEY ("parent_plan_id") REFERENCES "treatment_plans" ("plan_id");
ALTER TABLE "treatment_sessions" ADD CONSTRAINT "fk_treatment_sessions_plan_id" FOREIGN KEY ("plan_id") REFERENCES "treatment_plans" ("plan_id");
ALTER TABLE "treatment_sessions" ADD CONSTRAINT "fk_treatment_sessions_session_id" FOREIGN KEY ("session_id") REFERENCES "sessions" ("session_id");
ALTER TABLE "treatment_sessions" ADD CONSTRAINT "fk_treatment_sessions_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "treatment_sessions" ADD CONSTRAINT "fk_treatment_sessions_ca_id" FOREIGN KEY ("ca_id") REFERENCES "profiles" ("id");
ALTER TABLE "patients" ADD CONSTRAINT "fk_patients_registered_by" FOREIGN KEY ("registered_by") REFERENCES "profiles" ("id");
ALTER TABLE "neuromod_diagnoses" ADD CONSTRAINT "fk_neuromod_diagnoses_condition_id" FOREIGN KEY ("condition_id") REFERENCES "neuromod_conditions" ("condition_id");
ALTER TABLE "neuromod_scales" ADD CONSTRAINT "fk_neuromod_scales_prs_scale_id" FOREIGN KEY ("prs_scale_id") REFERENCES "prs_scales" ("scale_id");
ALTER TABLE "neuromod_condition_scales" ADD CONSTRAINT "fk_neuromod_condition_scales_condition_id" FOREIGN KEY ("condition_id") REFERENCES "neuromod_conditions" ("condition_id");
ALTER TABLE "neuromod_condition_scales" ADD CONSTRAINT "fk_neuromod_condition_scales_scale_id" FOREIGN KEY ("scale_id") REFERENCES "neuromod_scales" ("scale_id");
ALTER TABLE "tdcs_placements" ADD CONSTRAINT "fk_tdcs_placements_condition_id" FOREIGN KEY ("condition_id") REFERENCES "neuromod_conditions" ("condition_id");
ALTER TABLE "tdcs_placements" ADD CONSTRAINT "fk_tdcs_placements_device_id" FOREIGN KEY ("device_id") REFERENCES "neuromod_devices" ("device_id");
ALTER TABLE "hd_tdcs_placements" ADD CONSTRAINT "fk_hd_tdcs_placements_condition_id" FOREIGN KEY ("condition_id") REFERENCES "neuromod_conditions" ("condition_id");
ALTER TABLE "hd_tdcs_placements" ADD CONSTRAINT "fk_hd_tdcs_placements_device_id" FOREIGN KEY ("device_id") REFERENCES "neuromod_devices" ("device_id");
ALTER TABLE "tavns_placements" ADD CONSTRAINT "fk_tavns_placements_condition_id" FOREIGN KEY ("condition_id") REFERENCES "neuromod_conditions" ("condition_id");
ALTER TABLE "tavns_placements" ADD CONSTRAINT "fk_tavns_placements_device_id" FOREIGN KEY ("device_id") REFERENCES "neuromod_devices" ("device_id");
ALTER TABLE "tps_placements" ADD CONSTRAINT "fk_tps_placements_condition_id" FOREIGN KEY ("condition_id") REFERENCES "neuromod_conditions" ("condition_id");
ALTER TABLE "tps_placements" ADD CONSTRAINT "fk_tps_placements_device_id" FOREIGN KEY ("device_id") REFERENCES "neuromod_devices" ("device_id");
ALTER TABLE "rtms_placements" ADD CONSTRAINT "fk_rtms_placements_condition_id" FOREIGN KEY ("condition_id") REFERENCES "neuromod_conditions" ("condition_id");
ALTER TABLE "rtms_placements" ADD CONSTRAINT "fk_rtms_placements_device_id" FOREIGN KEY ("device_id") REFERENCES "neuromod_devices" ("device_id");
ALTER TABLE "other_placements" ADD CONSTRAINT "fk_other_placements_condition_id" FOREIGN KEY ("condition_id") REFERENCES "neuromod_conditions" ("condition_id");
ALTER TABLE "other_placements" ADD CONSTRAINT "fk_other_placements_device_id" FOREIGN KEY ("device_id") REFERENCES "neuromod_devices" ("device_id");
ALTER TABLE "tdcs_dosing" ADD CONSTRAINT "fk_tdcs_dosing_condition_id" FOREIGN KEY ("condition_id") REFERENCES "neuromod_conditions" ("condition_id");
ALTER TABLE "tdcs_dosing" ADD CONSTRAINT "fk_tdcs_dosing_device_id" FOREIGN KEY ("device_id") REFERENCES "neuromod_devices" ("device_id");
ALTER TABLE "tdcs_dosing" ADD CONSTRAINT "fk_tdcs_dosing_tdcs_placement_id" FOREIGN KEY ("tdcs_placement_id") REFERENCES "tdcs_placements" ("tdcs_placement_id");
ALTER TABLE "hd_tdcs_dosing" ADD CONSTRAINT "fk_hd_tdcs_dosing_condition_id" FOREIGN KEY ("condition_id") REFERENCES "neuromod_conditions" ("condition_id");
ALTER TABLE "hd_tdcs_dosing" ADD CONSTRAINT "fk_hd_tdcs_dosing_device_id" FOREIGN KEY ("device_id") REFERENCES "neuromod_devices" ("device_id");
ALTER TABLE "hd_tdcs_dosing" ADD CONSTRAINT "fk_hd_tdcs_dosing_placement_id" FOREIGN KEY ("hd_tdcs_placement_id") REFERENCES "hd_tdcs_placements" ("hd_tdcs_placement_id");
ALTER TABLE "tavns_dosing" ADD CONSTRAINT "fk_tavns_dosing_condition_id" FOREIGN KEY ("condition_id") REFERENCES "neuromod_conditions" ("condition_id");
ALTER TABLE "tavns_dosing" ADD CONSTRAINT "fk_tavns_dosing_device_id" FOREIGN KEY ("device_id") REFERENCES "neuromod_devices" ("device_id");
ALTER TABLE "tavns_dosing" ADD CONSTRAINT "fk_tavns_dosing_placement_id" FOREIGN KEY ("tavns_placement_id") REFERENCES "tavns_placements" ("tavns_placement_id");
ALTER TABLE "tps_dosing" ADD CONSTRAINT "fk_tps_dosing_condition_id" FOREIGN KEY ("condition_id") REFERENCES "neuromod_conditions" ("condition_id");
ALTER TABLE "tps_dosing" ADD CONSTRAINT "fk_tps_dosing_device_id" FOREIGN KEY ("device_id") REFERENCES "neuromod_devices" ("device_id");
ALTER TABLE "tps_dosing" ADD CONSTRAINT "fk_tps_dosing_placement_id" FOREIGN KEY ("tps_placement_id") REFERENCES "tps_placements" ("tps_placement_id");
ALTER TABLE "rtms_dosing" ADD CONSTRAINT "fk_rtms_dosing_condition_id" FOREIGN KEY ("condition_id") REFERENCES "neuromod_conditions" ("condition_id");
ALTER TABLE "rtms_dosing" ADD CONSTRAINT "fk_rtms_dosing_device_id" FOREIGN KEY ("device_id") REFERENCES "neuromod_devices" ("device_id");
ALTER TABLE "rtms_dosing" ADD CONSTRAINT "fk_rtms_dosing_placement_id" FOREIGN KEY ("rtms_placement_id") REFERENCES "rtms_placements" ("rtms_placement_id");
ALTER TABLE "other_dosing" ADD CONSTRAINT "fk_other_dosing_condition_id" FOREIGN KEY ("condition_id") REFERENCES "neuromod_conditions" ("condition_id");
ALTER TABLE "other_dosing" ADD CONSTRAINT "fk_other_dosing_device_id" FOREIGN KEY ("device_id") REFERENCES "neuromod_devices" ("device_id");
ALTER TABLE "other_dosing" ADD CONSTRAINT "fk_other_dosing_placement_id" FOREIGN KEY ("other_placement_id") REFERENCES "other_placements" ("other_placement_id");
ALTER TABLE "treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_plan_id" FOREIGN KEY ("plan_id") REFERENCES "treatment_plans" ("plan_id");
ALTER TABLE "treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_device_id" FOREIGN KEY ("device_id") REFERENCES "neuromod_devices" ("device_id");
ALTER TABLE "treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_set_by" FOREIGN KEY ("set_by") REFERENCES "profiles" ("id");
ALTER TABLE "treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tdcs_placement_id" FOREIGN KEY ("tdcs_placement_id") REFERENCES "tdcs_placements" ("tdcs_placement_id");
ALTER TABLE "treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_hd_tdcs_placement_id" FOREIGN KEY ("hd_tdcs_placement_id") REFERENCES "hd_tdcs_placements" ("hd_tdcs_placement_id");
ALTER TABLE "treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tavns_placement_id" FOREIGN KEY ("tavns_placement_id") REFERENCES "tavns_placements" ("tavns_placement_id");
ALTER TABLE "treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tps_placement_id" FOREIGN KEY ("tps_placement_id") REFERENCES "tps_placements" ("tps_placement_id");
ALTER TABLE "treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_rtms_placement_id" FOREIGN KEY ("rtms_placement_id") REFERENCES "rtms_placements" ("rtms_placement_id");
ALTER TABLE "treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_other_placement_id" FOREIGN KEY ("other_placement_id") REFERENCES "other_placements" ("other_placement_id");
ALTER TABLE "treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tdcs_dosing_id" FOREIGN KEY ("tdcs_dosing_id") REFERENCES "tdcs_dosing" ("tdcs_dosing_id");
ALTER TABLE "treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_hd_tdcs_dosing_id" FOREIGN KEY ("hd_tdcs_dosing_id") REFERENCES "hd_tdcs_dosing" ("hd_tdcs_dosing_id");
ALTER TABLE "treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tavns_dosing_id" FOREIGN KEY ("tavns_dosing_id") REFERENCES "tavns_dosing" ("tavns_dosing_id");
ALTER TABLE "treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tps_dosing_id" FOREIGN KEY ("tps_dosing_id") REFERENCES "tps_dosing" ("tps_dosing_id");
ALTER TABLE "treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_rtms_dosing_id" FOREIGN KEY ("rtms_dosing_id") REFERENCES "rtms_dosing" ("rtms_dosing_id");
ALTER TABLE "treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_other_dosing_id" FOREIGN KEY ("other_dosing_id") REFERENCES "other_dosing" ("other_dosing_id");
ALTER TABLE "protocol_sessions" ADD CONSTRAINT "fk_protocol_sessions_protocol_id" FOREIGN KEY ("protocol_id") REFERENCES "treatment_protocols" ("protocol_id");
ALTER TABLE "protocol_sessions" ADD CONSTRAINT "fk_protocol_sessions_appointment_id" FOREIGN KEY ("appointment_id") REFERENCES "appointments" ("appointment_id");
ALTER TABLE "protocol_followups" ADD CONSTRAINT "fk_protocol_followups_protocol_id" FOREIGN KEY ("protocol_id") REFERENCES "treatment_protocols" ("protocol_id");
ALTER TABLE "protocol_followups" ADD CONSTRAINT "fk_protocol_followups_appointment_id" FOREIGN KEY ("appointment_id") REFERENCES "appointments" ("appointment_id");
ALTER TABLE "device_session_prs_responses" ADD CONSTRAINT "fk_ds_prs_protocol_session_id" FOREIGN KEY ("protocol_session_id") REFERENCES "protocol_sessions" ("protocol_session_id");
ALTER TABLE "device_session_prs_responses" ADD CONSTRAINT "fk_ds_prs_instance_id" FOREIGN KEY ("instance_id") REFERENCES "prs_assessment_instances" ("instance_id");
ALTER TABLE "device_session_prs_responses" ADD CONSTRAINT "fk_ds_prs_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
ALTER TABLE "followup_prs_responses" ADD CONSTRAINT "fk_fu_prs_protocol_followup_id" FOREIGN KEY ("protocol_followup_id") REFERENCES "protocol_followups" ("protocol_followup_id");
ALTER TABLE "followup_prs_responses" ADD CONSTRAINT "fk_fu_prs_instance_id" FOREIGN KEY ("instance_id") REFERENCES "prs_assessment_instances" ("instance_id");
ALTER TABLE "followup_prs_responses" ADD CONSTRAINT "fk_fu_prs_patient_id" FOREIGN KEY ("patient_id") REFERENCES "profiles" ("id");
