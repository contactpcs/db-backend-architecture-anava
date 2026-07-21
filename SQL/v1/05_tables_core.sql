-- Generated from live production schema introspection (2026-07-20). Do not hand-edit column/RLS/trigger/function bodies — regenerate from source instead.

CREATE TABLE core."admins" (
    "admin_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "profile_id" UUID NOT NULL,
    "admin_type" TEXT NOT NULL,
    "region_id" UUID,
    "clinic_id" UUID,
    "force_password_change" BOOLEAN NOT NULL DEFAULT false,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."anamnesis_assessments" (
    "anamnesis_id" TEXT NOT NULL,
    "patient_id" UUID NOT NULL,
    "submitted_by" UUID,
    "taken_by" core.assessment_taken_by NOT NULL DEFAULT 'patient'::core.assessment_taken_by,
    "cycle_id" UUID,
    "version" INTEGER NOT NULL DEFAULT 1,
    "status" TEXT NOT NULL DEFAULT 'in_progress'::text,
    "completed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."anamnesis_responses" (
    "response_id" TEXT NOT NULL,
    "anamnesis_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "response_value" TEXT,
    "response_values" text[],
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."appointment_audit_logs" (
    "audit_id" UUID NOT NULL DEFAULT gen_random_uuid(),
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
    "changed_at" TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE ("changed_at");
-- Partitioned by monthly range on changed_at. Initial partitions below;
-- ongoing partition creation ahead of the current date is an operational job (Layer 7),
-- not a one-time setup step — see ops/PARTITION_MAINTENANCE.md.
CREATE TABLE core."appointment_audit_logs_y2025m01" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
CREATE TABLE core."appointment_audit_logs_y2025m02" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
CREATE TABLE core."appointment_audit_logs_y2025m03" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
CREATE TABLE core."appointment_audit_logs_y2025m04" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
CREATE TABLE core."appointment_audit_logs_y2025m05" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
CREATE TABLE core."appointment_audit_logs_y2025m06" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE core."appointment_audit_logs_y2025m07" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE core."appointment_audit_logs_y2025m08" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE core."appointment_audit_logs_y2025m09" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE core."appointment_audit_logs_y2025m10" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE core."appointment_audit_logs_y2025m11" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE core."appointment_audit_logs_y2025m12" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
CREATE TABLE core."appointment_audit_logs_y2026m01" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE core."appointment_audit_logs_y2026m02" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE core."appointment_audit_logs_y2026m03" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE core."appointment_audit_logs_y2026m04" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE core."appointment_audit_logs_y2026m05" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE core."appointment_audit_logs_y2026m06" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE core."appointment_audit_logs_y2026m07" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE core."appointment_audit_logs_y2026m08" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE core."appointment_audit_logs_y2026m09" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE core."appointment_audit_logs_y2026m10" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE core."appointment_audit_logs_y2026m11" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE core."appointment_audit_logs_y2026m12" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE core."appointment_audit_logs_y2027m01" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');
CREATE TABLE core."appointment_audit_logs_y2027m02" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');
CREATE TABLE core."appointment_audit_logs_y2027m03" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');
CREATE TABLE core."appointment_audit_logs_y2027m04" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-04-01') TO ('2027-05-01');
CREATE TABLE core."appointment_audit_logs_y2027m05" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-05-01') TO ('2027-06-01');
CREATE TABLE core."appointment_audit_logs_y2027m06" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-06-01') TO ('2027-07-01');
CREATE TABLE core."appointment_audit_logs_y2027m07" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-07-01') TO ('2027-08-01');
CREATE TABLE core."appointment_audit_logs_y2027m08" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-08-01') TO ('2027-09-01');
CREATE TABLE core."appointment_audit_logs_y2027m09" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-09-01') TO ('2027-10-01');
CREATE TABLE core."appointment_audit_logs_y2027m10" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-10-01') TO ('2027-11-01');
CREATE TABLE core."appointment_audit_logs_y2027m11" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-11-01') TO ('2027-12-01');
CREATE TABLE core."appointment_audit_logs_y2027m12" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-12-01') TO ('2028-01-01');
CREATE TABLE core."appointment_audit_logs_default" PARTITION OF core."appointment_audit_logs" DEFAULT;


CREATE TABLE core."appointment_requests" (
    "request_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID,
    "cycle_id" UUID,
    "request_type" TEXT NOT NULL DEFAULT 'new'::text,
    "parent_appointment_id" UUID,
    "preferred_date_1" DATE NOT NULL,
    "preferred_date_2" DATE,
    "preferred_date_3" DATE,
    "preferred_time_window" TEXT NOT NULL DEFAULT 'any'::text,
    "patient_complaint" TEXT,
    "reason" TEXT,
    "urgency" TEXT NOT NULL DEFAULT 'normal'::text,
    "status" TEXT NOT NULL DEFAULT 'pending'::text,
    "approved_appointment_id" UUID,
    "submitted_by" UUID NOT NULL,
    "reviewed_by" UUID,
    "review_notes" TEXT,
    "expires_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."appointments" (
    "appointment_id" UUID NOT NULL DEFAULT gen_random_uuid(),
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
    "slot_duration_minutes" INTEGER NOT NULL DEFAULT 30,
    "appointment_type" TEXT NOT NULL DEFAULT 'initial_assessment'::text,
    "session_phase" TEXT,
    "status" TEXT NOT NULL DEFAULT 'scheduled'::text,
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
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."assessment_protocol_requests" (
    "request_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "clinical_assistant_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "clinic_id" UUID,
    "cycle_id" UUID,
    "protocol_details" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "status" TEXT NOT NULL DEFAULT 'pending'::text,
    "doctor_notes" TEXT,
    "submitted_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "reviewed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."ca_doctor_assignments" (
    "cda_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "ca_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "is_primary" BOOLEAN NOT NULL DEFAULT false,
    "assigned_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "removed_at" TIMESTAMPTZ
);


CREATE TABLE core."clinic_requests" (
    "request_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "request_type" TEXT NOT NULL,
    "clinic_type" TEXT,
    "clinic_id" UUID,
    "region_id" UUID NOT NULL,
    "submitted_by" UUID NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'pending'::text,
    "payload" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "reviewed_by" UUID,
    "review_notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."clinic_staff_assignments" (
    "assignment_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_id" UUID NOT NULL,
    "profile_id" UUID NOT NULL,
    "staff_role" TEXT NOT NULL,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "joined_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "removed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."clinical_assistants" (
    "ca_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "profile_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "qualification" TEXT,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."clinics" (
    "clinic_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_code" TEXT NOT NULL,
    "clinic_name" TEXT NOT NULL,
    "clinic_type" TEXT NOT NULL,
    "owner_name" TEXT NOT NULL DEFAULT 'Anava'::text,
    "status" TEXT NOT NULL DEFAULT 'setup'::text,
    "region_id" UUID NOT NULL,
    "clinic_admin_id" UUID,
    "is_main_branch" BOOLEAN NOT NULL DEFAULT false,
    "timezone" TEXT NOT NULL DEFAULT 'Asia/Kolkata'::text,
    "address" TEXT,
    "city" TEXT,
    "state" TEXT,
    "country" TEXT NOT NULL DEFAULT 'India'::text,
    "phone" TEXT,
    "email" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."device_assignments" (
    "da_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "plan_id" UUID NOT NULL,
    "assigned_by" UUID NOT NULL,
    "device_type" TEXT NOT NULL,
    "purchase_status" TEXT NOT NULL DEFAULT 'purchase_prompted'::text,
    "order_id" UUID,
    "prompted_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "purchased_at" TIMESTAMPTZ,
    "collected_at" TIMESTAMPTZ,
    "returned_at" TIMESTAMPTZ,
    "returned_by" UUID,
    "return_reason" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."doctor_patient_assignments" (
    "assignment_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "doctor_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'active'::text,
    "assigned_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "ended_at" TIMESTAMPTZ
);


CREATE TABLE core."doctor_schedule_overrides" (
    "override_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "doctor_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "override_date" DATE NOT NULL,
    "is_available" BOOLEAN NOT NULL DEFAULT false,
    "start_time" TIME,
    "end_time" TIME,
    "slot_duration_minutes" INTEGER,
    "reason" TEXT,
    "created_by" UUID NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."doctor_session_notes" (
    "note_id" UUID NOT NULL DEFAULT gen_random_uuid(),
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
    "is_confidential" BOOLEAN NOT NULL DEFAULT false,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."doctor_weekly_schedules" (
    "schedule_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "doctor_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "day_of_week" SMALLINT NOT NULL,
    "start_time" TIME NOT NULL,
    "end_time" TIME NOT NULL,
    "slot_duration_minutes" INTEGER NOT NULL DEFAULT 30,
    "break_start" TIME,
    "break_end" TIME,
    "max_appointments" INTEGER,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "effective_from" DATE,
    "effective_until" DATE,
    "created_by" UUID,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."doctors" (
    "doctor_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "profile_id" UUID NOT NULL,
    "specialization" TEXT,
    "license_number" TEXT,
    "hospital_affiliation" TEXT,
    "max_patient_count" INTEGER NOT NULL DEFAULT 30,
    "availability_status" TEXT NOT NULL DEFAULT 'available'::text,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "clinic_id" UUID
);


CREATE TABLE core."inventory" (
    "inventory_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "product_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "quantity" INTEGER NOT NULL DEFAULT 0,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."notifications" (
    "notification_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "recipient_id" UUID NOT NULL,
    "sender_id" UUID,
    "clinic_id" UUID,
    "type" TEXT NOT NULL DEFAULT 'system'::text,
    "delivery_channel" TEXT NOT NULL DEFAULT 'in_app'::text,
    "title" TEXT NOT NULL,
    "body" TEXT,
    "entity_type" TEXT,
    "entity_id" UUID,
    "metadata" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "is_read" BOOLEAN NOT NULL DEFAULT false,
    "read_at" TIMESTAMPTZ,
    "delivered_at" TIMESTAMPTZ,
    "delivery_attempts" INTEGER NOT NULL DEFAULT 0,
    "expires_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE ("created_at");
-- Partitioned by monthly range on created_at. Initial partitions below;
-- ongoing partition creation ahead of the current date is an operational job (Layer 7),
-- not a one-time setup step — see ops/PARTITION_MAINTENANCE.md.
CREATE TABLE core."notifications_y2025m01" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
CREATE TABLE core."notifications_y2025m02" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
CREATE TABLE core."notifications_y2025m03" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
CREATE TABLE core."notifications_y2025m04" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
CREATE TABLE core."notifications_y2025m05" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
CREATE TABLE core."notifications_y2025m06" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE core."notifications_y2025m07" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE core."notifications_y2025m08" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE core."notifications_y2025m09" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE core."notifications_y2025m10" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE core."notifications_y2025m11" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE core."notifications_y2025m12" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
CREATE TABLE core."notifications_y2026m01" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE core."notifications_y2026m02" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE core."notifications_y2026m03" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE core."notifications_y2026m04" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE core."notifications_y2026m05" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE core."notifications_y2026m06" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE core."notifications_y2026m07" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE core."notifications_y2026m08" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE core."notifications_y2026m09" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE core."notifications_y2026m10" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE core."notifications_y2026m11" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE core."notifications_y2026m12" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE core."notifications_y2027m01" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');
CREATE TABLE core."notifications_y2027m02" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');
CREATE TABLE core."notifications_y2027m03" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');
CREATE TABLE core."notifications_y2027m04" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-04-01') TO ('2027-05-01');
CREATE TABLE core."notifications_y2027m05" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-05-01') TO ('2027-06-01');
CREATE TABLE core."notifications_y2027m06" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-06-01') TO ('2027-07-01');
CREATE TABLE core."notifications_y2027m07" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-07-01') TO ('2027-08-01');
CREATE TABLE core."notifications_y2027m08" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-08-01') TO ('2027-09-01');
CREATE TABLE core."notifications_y2027m09" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-09-01') TO ('2027-10-01');
CREATE TABLE core."notifications_y2027m10" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-10-01') TO ('2027-11-01');
CREATE TABLE core."notifications_y2027m11" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-11-01') TO ('2027-12-01');
CREATE TABLE core."notifications_y2027m12" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-12-01') TO ('2028-01-01');
CREATE TABLE core."notifications_default" PARTITION OF core."notifications" DEFAULT;


CREATE TABLE core."order_items" (
    "item_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "order_id" UUID NOT NULL,
    "product_id" UUID NOT NULL,
    "quantity" INTEGER NOT NULL DEFAULT 1,
    "unit_price" NUMERIC(10,2) NOT NULL
);


CREATE TABLE core."patient_clinic_transfers" (
    "pct_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "from_clinic_id" UUID NOT NULL,
    "to_clinic_id" UUID NOT NULL,
    "from_doctor_id" UUID,
    "to_doctor_id" UUID,
    "transfer_reason" TEXT NOT NULL DEFAULT 'clinic_closure'::text,
    "active_cycle_id" UUID,
    "status" TEXT NOT NULL DEFAULT 'pending'::text,
    "consent_id" UUID,
    "initiated_by" UUID NOT NULL,
    "notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."patient_disease_selection" (
    "pds_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "disease_id" TEXT,
    "disease_unknown" BOOLEAN NOT NULL DEFAULT false,
    "is_primary" BOOLEAN NOT NULL DEFAULT false,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."patient_eeg_files" (
    "eeg_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "cycle_id" UUID,
    "session_id" UUID,
    "performed_by" UUID NOT NULL,
    "reviewed_by" UUID,
    "eeg_type" TEXT NOT NULL DEFAULT 'resting_state'::text,
    "duration_minutes" INTEGER,
    "raw_data_s3_key" TEXT,
    "raw_file_name" TEXT,
    "raw_file_size" BIGINT,
    "raw_checksum" TEXT,
    "raw_checksum_algorithm" TEXT NOT NULL DEFAULT 'sha256'::text,
    "report_s3_key" TEXT,
    "report_file_name" TEXT,
    "report_file_size" BIGINT,
    "report_checksum" TEXT,
    "report_checksum_algorithm" TEXT NOT NULL DEFAULT 'sha256'::text,
    "superseded_by" UUID,
    "recording_notes" TEXT,
    "clinical_findings" TEXT,
    "is_abnormal" BOOLEAN,
    "status" TEXT NOT NULL DEFAULT 'raw_uploaded'::text,
    "performed_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "reviewed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."patient_medical_history_files" (
    "mhf_id" UUID NOT NULL DEFAULT gen_random_uuid(),
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
    "checksum_algorithm" TEXT NOT NULL DEFAULT 'sha256'::text,
    "description" TEXT,
    "document_date" DATE,
    "source_provider" TEXT,
    "is_deleted" BOOLEAN NOT NULL DEFAULT false,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."patient_scale_assignments" (
    "psa_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "scale_id" TEXT NOT NULL,
    "assessment_stage" TEXT NOT NULL,
    "assigned_by" UUID NOT NULL,
    "assignment_reason" TEXT,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "deactivated_at" TIMESTAMPTZ,
    "deactivated_by" UUID,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "disease_id" TEXT
);


CREATE TABLE core."patients" (
    "patient_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "profile_id" UUID NOT NULL,
    "mrn" TEXT NOT NULL,
    "registration_status" TEXT NOT NULL DEFAULT 'demographics_complete'::text,
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
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "self_registered" BOOLEAN NOT NULL DEFAULT false,
    "approval_status" TEXT NOT NULL DEFAULT 'not_required'::text,
    "approved_by" UUID,
    "approved_at" TIMESTAMPTZ,
    "rejection_reason" TEXT
);


CREATE TABLE core."payments" (
    "payment_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "session_id" UUID,
    "order_id" UUID,
    "idempotency_key" TEXT NOT NULL,
    "razorpay_order_id" TEXT,
    "razorpay_payment_id" TEXT,
    "amount" NUMERIC(10,2) NOT NULL,
    "currency" TEXT NOT NULL DEFAULT 'INR'::text,
    "payment_method" TEXT,
    "status" TEXT NOT NULL DEFAULT 'pending'::text,
    "gateway_response" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "waived_by" UUID,
    "waived_reason" TEXT,
    "paid_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."profiles" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
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
    "language_pref" TEXT NOT NULL DEFAULT 'en'::text,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "consent_signed" BOOLEAN NOT NULL DEFAULT true,
    "email_verified" BOOLEAN NOT NULL DEFAULT false,
    "phone_verified" BOOLEAN NOT NULL DEFAULT false
);


CREATE TABLE core."prs_assessment_instances" (
    "instance_id" TEXT NOT NULL,
    "disease_id" TEXT NOT NULL,
    "patient_id" UUID NOT NULL,
    "session_id" UUID,
    "cycle_id" UUID,
    "initiated_by" core.assessment_taken_by NOT NULL DEFAULT 'patient'::core.assessment_taken_by,
    "administered_by" UUID,
    "assessment_stage" TEXT NOT NULL DEFAULT 'general_registration'::text,
    "status" TEXT NOT NULL DEFAULT 'in_progress'::text,
    "started_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "completed_at" TIMESTAMPTZ,
    "final_result" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "language_code" VARCHAR(10) NOT NULL DEFAULT 'en'::character varying
);


CREATE TABLE core."prs_final_results" (
    "final_result_id" TEXT NOT NULL,
    "instance_id" TEXT NOT NULL,
    "calculated_value" NUMERIC,
    "max_possible" NUMERIC,
    "percentage" NUMERIC,
    "scales_completed" INTEGER NOT NULL DEFAULT 0,
    "scales_total" INTEGER NOT NULL DEFAULT 0,
    "overall_severity" TEXT,
    "overall_severity_label" TEXT,
    "scale_summaries" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "all_risk_flags" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "composite_summary" TEXT,
    "time_stamp" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."prs_responses" (
    "response_id" TEXT NOT NULL,
    "instance_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "given_response" TEXT,
    "response_value" NUMERIC,
    "time_stamp" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "language_code" VARCHAR(10) NOT NULL DEFAULT 'en'::character varying
);


CREATE TABLE core."prs_scale_results" (
    "scale_result_id" TEXT NOT NULL,
    "instance_id" TEXT NOT NULL,
    "scale_id" TEXT NOT NULL,
    "calculated_value" NUMERIC,
    "max_possible" NUMERIC,
    "percentage" NUMERIC,
    "severity_level" TEXT,
    "severity_label" TEXT,
    "subscale_scores" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "risk_flags" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "raw_score_data" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "time_stamp" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."receptionists" (
    "receptionist_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "profile_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."regions" (
    "region_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "region_name" TEXT NOT NULL,
    "country" TEXT NOT NULL,
    "state" TEXT NOT NULL,
    "regional_admin_id" UUID,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."sessions" (
    "session_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID,
    "session_date" TIMESTAMPTZ NOT NULL,
    "session_type" TEXT NOT NULL DEFAULT 'in_person'::text,
    "notes" TEXT,
    "status" TEXT NOT NULL DEFAULT 'scheduled'::text,
    "cycle_id" UUID,
    "clinic_id" UUID,
    "ca_id" UUID,
    "session_phase" TEXT,
    "session_number_in_cycle" INTEGER,
    "outcome" TEXT,
    "started_at" TIMESTAMPTZ,
    "completed_at" TIMESTAMPTZ,
    "payment_status" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."staff_requests" (
    "request_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_id" UUID NOT NULL,
    "regional_admin_id" UUID,
    "request_type" TEXT NOT NULL,
    "position_role" TEXT NOT NULL,
    "candidate_name" TEXT,
    "candidate_email" TEXT,
    "candidate_phone" TEXT,
    "candidate_credentials" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "target_staff_id" UUID,
    "status" TEXT NOT NULL DEFAULT 'pending'::text,
    "submitted_by" UUID NOT NULL,
    "reviewed_by" UUID,
    "review_notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "fulfilled_profile_id" UUID,
    "fulfilled_at" TIMESTAMPTZ
);


CREATE TABLE core."stock_transfers" (
    "st_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "product_id" UUID NOT NULL,
    "from_type" TEXT NOT NULL,
    "from_clinic_id" UUID,
    "to_clinic_id" UUID NOT NULL,
    "quantity" INTEGER NOT NULL,
    "order_id" UUID,
    "status" TEXT NOT NULL DEFAULT 'pending'::text,
    "initiated_by" UUID NOT NULL,
    "received_by" UUID,
    "notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "dispatched_at" TIMESTAMPTZ,
    "received_at" TIMESTAMPTZ
);


CREATE TABLE core."store_orders" (
    "order_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "initiated_by" UUID NOT NULL,
    "approved_by" UUID,
    "order_type" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'pending_doctor_approval'::text,
    "total_amount" NUMERIC(10,2),
    "treatment_plan_id" UUID,
    "cancelled_by" UUID,
    "cancelled_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."treatment_cycles" (
    "cycle_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "ca_id" UUID,
    "clinic_id" UUID NOT NULL,
    "cycle_type" TEXT NOT NULL,
    "cycle_number" INTEGER NOT NULL DEFAULT 1,
    "scheduled_date" DATE,
    "status" TEXT NOT NULL DEFAULT 'in_progress'::text,
    "notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."treatment_plans" (
    "plan_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "cycle_id" UUID NOT NULL,
    "device_type" TEXT NOT NULL,
    "protocol_details" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "sessions_prescribed" INTEGER NOT NULL,
    "standard_sessions" INTEGER NOT NULL DEFAULT 5,
    "extended_sessions" INTEGER,
    "status" TEXT NOT NULL DEFAULT 'active'::text,
    "parent_plan_id" UUID,
    "demo_phase_status" TEXT NOT NULL DEFAULT 'pending'::text,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."treatment_sessions" (
    "ts_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "plan_id" UUID NOT NULL,
    "session_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "ca_id" UUID NOT NULL,
    "session_number" INTEGER NOT NULL,
    "billing_type" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'scheduled'::text,
    "payment_status" TEXT NOT NULL DEFAULT 'pending'::text,
    "session_notes" TEXT,
    "patient_feedback" TEXT,
    "started_at" TIMESTAMPTZ,
    "completed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE ("created_at");
-- Partitioned by yearly range on created_at. Initial partitions below;
-- ongoing partition creation ahead of the current date is an operational job (Layer 7),
-- not a one-time setup step — see ops/PARTITION_MAINTENANCE.md.
CREATE TABLE core."treatment_sessions_y2024" PARTITION OF core."treatment_sessions"
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE core."treatment_sessions_y2025" PARTITION OF core."treatment_sessions"
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE core."treatment_sessions_y2026" PARTITION OF core."treatment_sessions"
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE core."treatment_sessions_y2027" PARTITION OF core."treatment_sessions"
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
CREATE TABLE core."treatment_sessions_y2028" PARTITION OF core."treatment_sessions"
    FOR VALUES FROM ('2028-01-01') TO ('2029-01-01');
CREATE TABLE core."treatment_sessions_default" PARTITION OF core."treatment_sessions" DEFAULT;

