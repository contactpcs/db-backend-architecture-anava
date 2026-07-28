-- ============================================================
-- Anava Clinic — DB Schema
-- File 14: Triggers
-- 1. fn_set_updated_at() — auto-update updated_at column
-- 2. fn_generate_mrn()   — MRN auto-generation BEFORE INSERT
-- 3. fn_audit_trigger()  — write to audit_logs on changes
-- 4. recalculate_final_result — kept in 07_prs_tables.sql
-- ============================================================

-- ============================================================
-- PART 1: updated_at auto-stamp
-- ============================================================

CREATE OR REPLACE FUNCTION fn_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_updated_at_profiles
    BEFORE UPDATE ON profiles
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_regions
    BEFORE UPDATE ON regions
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_clinics
    BEFORE UPDATE ON clinics
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_patients
    BEFORE UPDATE ON patients
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_patient_disease_selection
    BEFORE UPDATE ON patient_disease_selection
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_clinic_requests
    BEFORE UPDATE ON clinic_requests
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_staff_requests
    BEFORE UPDATE ON staff_requests
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_treatment_cycles
    BEFORE UPDATE ON treatment_cycles
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_assessment_protocol_requests
    BEFORE UPDATE ON assessment_protocol_requests
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_sessions
    BEFORE UPDATE ON sessions
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_treatment_plans
    BEFORE UPDATE ON treatment_plans
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_patient_clinic_transfers
    BEFORE UPDATE ON patient_clinic_transfers
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_products
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_store_orders
    BEFORE UPDATE ON store_orders
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_inventory
    BEFORE UPDATE ON inventory
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_prs_diseases
    BEFORE UPDATE ON prs_diseases
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_prs_scales
    BEFORE UPDATE ON prs_scales
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_anamnesis_assessments
    BEFORE UPDATE ON anamnesis_assessments
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_anamnesis_responses
    BEFORE UPDATE ON anamnesis_responses
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_patient_eeg_files
    BEFORE UPDATE ON patient_eeg_files
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_patient_medical_history_files
    BEFORE UPDATE ON patient_medical_history_files
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_doctor_session_notes
    BEFORE UPDATE ON doctor_session_notes
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_payments
    BEFORE UPDATE ON payments
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();


-- ============================================================
-- PART 2: MRN auto-generation trigger
--
-- Format: 'ANV-XXXXXXXX' (8 digits, supports up to 99,999,999 patients).
-- mrn_seq starts at 10001 → first MRN: 'ANV-00010001'.
-- Trigger fires BEFORE INSERT; NOT NULL constraint on mrn is
-- satisfied by this trigger setting the value.
-- ============================================================

CREATE OR REPLACE FUNCTION fn_generate_mrn()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.mrn IS NULL THEN
        NEW.mrn := 'ANV-' || LPAD(nextval('mrn_seq')::TEXT, 8, '0');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_generate_mrn
    BEFORE INSERT ON patients
    FOR EACH ROW EXECUTE FUNCTION fn_generate_mrn();


-- ============================================================
-- PART 3: audit_logs trigger
--
-- Fires AFTER INSERT/UPDATE/DELETE on key tables.
-- Reads app.current_user_id from session context (set by FastAPI
-- middleware at start of each request via SET LOCAL).
-- TG_ARGV[0] = name of the PK column for this table.
--
-- CRITICAL: record_id is TEXT (not UUID) to support both UUID PKs
-- (most tables) and TEXT PKs (prs_assessment_instances instance_id,
-- anamnesis_assessments anamnesis_id). The previous UUID cast caused
-- ERROR: invalid input syntax for type uuid on TEXT PKs.
-- ============================================================

CREATE OR REPLACE FUNCTION fn_audit_trigger()
RETURNS TRIGGER AS $$
DECLARE
    v_pk_col    TEXT := TG_ARGV[0];
    v_record_id TEXT;
    v_old_data  JSONB;
    v_new_data  JSONB;
    v_user_id   UUID;
BEGIN
    -- Read actor from session context (set by FastAPI middleware)
    BEGIN
        v_user_id := NULLIF(current_setting('app.current_user_id', TRUE), '')::UUID;
    EXCEPTION WHEN others THEN
        v_user_id := NULL;
    END;

    IF TG_OP = 'DELETE' THEN
        v_old_data  := to_jsonb(OLD);
        v_new_data  := NULL;
        v_record_id := v_old_data ->> v_pk_col;   -- TEXT, no ::UUID cast
    ELSIF TG_OP = 'INSERT' THEN
        v_old_data  := NULL;
        v_new_data  := to_jsonb(NEW);
        v_record_id := v_new_data ->> v_pk_col;   -- TEXT, no ::UUID cast
    ELSE  -- UPDATE
        v_old_data  := to_jsonb(OLD);
        v_new_data  := to_jsonb(NEW);
        v_record_id := v_new_data ->> v_pk_col;   -- TEXT, no ::UUID cast
    END IF;

    INSERT INTO audit_logs (table_name, operation, record_id, old_data, new_data, changed_by)
    VALUES (TG_TABLE_NAME, TG_OP, v_record_id, v_old_data, v_new_data, v_user_id);

    RETURN NULL;  -- AFTER trigger; return value ignored
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- -------------------------------------------------------
-- Audit triggers — key tables only
-- (audit_logs and activity_logs excluded — append-only)
-- -------------------------------------------------------

CREATE TRIGGER trg_audit_profiles
    AFTER INSERT OR UPDATE OR DELETE ON profiles
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('id');

CREATE TRIGGER trg_audit_regions
    AFTER INSERT OR UPDATE OR DELETE ON regions
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('region_id');

CREATE TRIGGER trg_audit_clinics
    AFTER INSERT OR UPDATE OR DELETE ON clinics
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('clinic_id');

CREATE TRIGGER trg_audit_admins
    AFTER INSERT OR UPDATE OR DELETE ON admins
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('admin_id');

CREATE TRIGGER trg_audit_clinic_staff_assignments
    AFTER INSERT OR UPDATE OR DELETE ON clinic_staff_assignments
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('assignment_id');

CREATE TRIGGER trg_audit_doctors
    AFTER INSERT OR UPDATE OR DELETE ON doctors
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('doctor_id');

CREATE TRIGGER trg_audit_patients
    AFTER INSERT OR UPDATE OR DELETE ON patients
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('patient_id');

CREATE TRIGGER trg_audit_patient_disease_selection
    AFTER INSERT OR UPDATE OR DELETE ON patient_disease_selection
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('pds_id');

CREATE TRIGGER trg_audit_clinic_requests
    AFTER INSERT OR UPDATE OR DELETE ON clinic_requests
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('request_id');

CREATE TRIGGER trg_audit_staff_requests
    AFTER INSERT OR UPDATE OR DELETE ON staff_requests
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('request_id');

CREATE TRIGGER trg_audit_doctor_patient_assignments
    AFTER INSERT OR UPDATE OR DELETE ON doctor_patient_assignments
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('assignment_id');

CREATE TRIGGER trg_audit_treatment_cycles
    AFTER INSERT OR UPDATE OR DELETE ON treatment_cycles
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('cycle_id');

CREATE TRIGGER trg_audit_assessment_protocol_requests
    AFTER INSERT OR UPDATE OR DELETE ON assessment_protocol_requests
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('request_id');

CREATE TRIGGER trg_audit_sessions
    AFTER INSERT OR UPDATE OR DELETE ON sessions
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('session_id');

CREATE TRIGGER trg_audit_treatment_plans
    AFTER INSERT OR UPDATE OR DELETE ON treatment_plans
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('plan_id');

CREATE TRIGGER trg_audit_treatment_sessions
    AFTER INSERT OR UPDATE OR DELETE ON treatment_sessions
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('ts_id');

-- TEXT PK tables: instance_id and anamnesis_id are TEXT, not UUID.
-- fn_audit_trigger now uses TEXT for record_id — no cast error.
CREATE TRIGGER trg_audit_prs_assessment_instances
    AFTER INSERT OR UPDATE OR DELETE ON prs_assessment_instances
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('instance_id');

CREATE TRIGGER trg_audit_anamnesis_assessments
    AFTER INSERT OR UPDATE OR DELETE ON anamnesis_assessments
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('anamnesis_id');

CREATE TRIGGER trg_audit_consent_records
    AFTER INSERT OR UPDATE OR DELETE ON consent_records
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('consent_id');

CREATE TRIGGER trg_audit_patient_clinic_transfers
    AFTER INSERT OR UPDATE OR DELETE ON patient_clinic_transfers
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('pct_id');

CREATE TRIGGER trg_audit_store_orders
    AFTER INSERT OR UPDATE OR DELETE ON store_orders
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('order_id');

CREATE TRIGGER trg_audit_stock_transfers
    AFTER INSERT OR UPDATE OR DELETE ON stock_transfers
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('st_id');

CREATE TRIGGER trg_audit_device_assignments
    AFTER INSERT OR UPDATE OR DELETE ON device_assignments
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('da_id');

CREATE TRIGGER trg_audit_payments
    AFTER INSERT OR UPDATE OR DELETE ON payments
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('payment_id');

CREATE TRIGGER trg_audit_consent_templates
    AFTER INSERT OR UPDATE OR DELETE ON consent_templates
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('template_id');

CREATE TRIGGER trg_audit_patient_scale_assignments
    AFTER INSERT OR UPDATE OR DELETE ON patient_scale_assignments
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('psa_id');

CREATE TRIGGER trg_audit_doctor_session_notes
    AFTER INSERT OR UPDATE OR DELETE ON doctor_session_notes
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('note_id');

CREATE TRIGGER trg_audit_patient_eeg_files
    AFTER INSERT OR UPDATE OR DELETE ON patient_eeg_files
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('eeg_id');

CREATE TRIGGER trg_audit_patient_medical_history_files
    AFTER INSERT OR UPDATE OR DELETE ON patient_medical_history_files
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('mhf_id');

CREATE TRIGGER trg_audit_ca_doctor_assignments
    AFTER INSERT OR UPDATE OR DELETE ON ca_doctor_assignments
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('cda_id');


-- ============================================================
-- 06b: Appointment scheduling tables
-- ============================================================

-- updated_at triggers
CREATE TRIGGER trg_updated_at_doctor_weekly_schedules
    BEFORE UPDATE ON doctor_weekly_schedules
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_appointment_requests
    BEFORE UPDATE ON appointment_requests
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_updated_at_appointments
    BEFORE UPDATE ON appointments
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

-- audit triggers
CREATE TRIGGER trg_audit_doctor_weekly_schedules
    AFTER INSERT OR UPDATE OR DELETE ON doctor_weekly_schedules
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('schedule_id');

CREATE TRIGGER trg_audit_doctor_schedule_overrides
    AFTER INSERT OR UPDATE OR DELETE ON doctor_schedule_overrides
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('override_id');

CREATE TRIGGER trg_audit_appointment_requests
    AFTER INSERT OR UPDATE OR DELETE ON appointment_requests
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('request_id');

CREATE TRIGGER trg_audit_appointments
    AFTER INSERT OR UPDATE OR DELETE ON appointments
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger('appointment_id');
