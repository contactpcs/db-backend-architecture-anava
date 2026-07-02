CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE SEQUENCE IF NOT EXISTS mrn_seq START 10001;

DO $$ BEGIN
    CREATE TYPE assessment_taken_by AS ENUM ('patient', 'doctor_on_behalf');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE profiles (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    cognito_sub          TEXT        UNIQUE NOT NULL,
    email                TEXT        UNIQUE NOT NULL CHECK (email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    first_name           TEXT        NOT NULL,
    last_name            TEXT        NOT NULL,
    phone                TEXT,
    role                 TEXT        NOT NULL CHECK (role IN (
                             'super_admin', 'regional_admin', 'clinic_admin',
                             'doctor', 'clinical_assistant', 'receptionist', 'patient'
                         )),
    gender               TEXT        CHECK (gender IN ('male', 'female', 'other')),
    dob                  DATE,
    address              TEXT,
    city                 TEXT,
    state                TEXT,
    country              TEXT,
    profile_photo_s3_key TEXT,
    pincode              TEXT,
    language_pref        TEXT        NOT NULL DEFAULT 'en',
    is_active            BOOLEAN     NOT NULL DEFAULT TRUE,
    deleted_by           UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    deleted_at           TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE prs_diseases (
    disease_id   TEXT        PRIMARY KEY,
    disease_code TEXT        NOT NULL UNIQUE,
    disease_name TEXT        NOT NULL,
    version      TEXT        NOT NULL DEFAULT 'v1.0',
    status       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE regions (
    region_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    region_name       TEXT        NOT NULL,
    country           TEXT        NOT NULL,
    state             TEXT        NOT NULL,
    regional_admin_id UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    is_active         BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (country, state)
);

CREATE TABLE clinics (
    clinic_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_code     TEXT        UNIQUE NOT NULL,
    clinic_name     TEXT        NOT NULL,
    clinic_type     TEXT        NOT NULL CHECK (clinic_type IN ('anava_owned', 'partner', 'mobile')),
    owner_name      TEXT        NOT NULL DEFAULT 'Anava',
    status          TEXT        NOT NULL DEFAULT 'setup'
                        CHECK (status IN ('setup', 'active', 'pending_closure', 'closed')),
    region_id       UUID        NOT NULL REFERENCES regions(region_id) ON DELETE RESTRICT,
    clinic_admin_id UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    is_main_branch  BOOLEAN     NOT NULL DEFAULT FALSE,
    timezone        TEXT        NOT NULL DEFAULT 'Asia/Kolkata',
    address         TEXT,
    city            TEXT,
    state           TEXT,
    country         TEXT        NOT NULL DEFAULT 'India',
    phone           TEXT,
    email           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE admins (
    admin_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id            UUID        UNIQUE NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    admin_type            TEXT        NOT NULL CHECK (admin_type IN (
                              'super_admin', 'regional_admin', 'clinic_admin'
                          )),
    region_id             UUID        REFERENCES regions(region_id) ON DELETE RESTRICT,
    clinic_id             UUID        REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    force_password_change BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_admins_scope CHECK (
        (admin_type = 'super_admin'    AND region_id IS NULL  AND clinic_id IS NULL)
        OR (admin_type = 'regional_admin' AND region_id IS NOT NULL AND clinic_id IS NULL)
        OR (admin_type = 'clinic_admin'   AND clinic_id IS NOT NULL)
    )
);

CREATE TABLE clinic_staff_assignments (
    assignment_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id     UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    profile_id    UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    staff_role    TEXT        NOT NULL CHECK (staff_role IN (
                      'clinic_admin', 'doctor', 'clinical_assistant', 'receptionist'
                  )),
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    joined_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    removed_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (clinic_id, profile_id)
);

CREATE TABLE doctors (
    doctor_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id           UUID        UNIQUE NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    specialization       TEXT,
    license_number       TEXT,
    hospital_affiliation TEXT,
    max_patient_count    INTEGER     NOT NULL DEFAULT 30 CHECK (max_patient_count >= 1),
    availability_status  TEXT        NOT NULL DEFAULT 'available'
                             CHECK (availability_status IN (
                                 'available', 'at_capacity', 'on_leave', 'inactive'
                             )),
    deleted_by           UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    deleted_at           TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE clinical_assistants (
    ca_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id    UUID        UNIQUE NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id     UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    qualification TEXT,
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    deleted_by    UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    deleted_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE ca_doctor_assignments (
    cda_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ca_id       UUID        NOT NULL REFERENCES clinical_assistants(ca_id) ON DELETE RESTRICT,
    doctor_id   UUID        NOT NULL REFERENCES doctors(profile_id) ON DELETE RESTRICT,
    clinic_id   UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    is_primary  BOOLEAN     NOT NULL DEFAULT FALSE,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    removed_at  TIMESTAMPTZ,
    UNIQUE (ca_id, doctor_id)
);

CREATE TABLE receptionists (
    receptionist_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id      UUID        UNIQUE NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id       UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    deleted_by      UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    deleted_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE patients (
    patient_id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id                UUID        UNIQUE NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    mrn                       TEXT        NOT NULL UNIQUE,
    registration_status       TEXT        NOT NULL DEFAULT 'demographics_complete'
                                              CHECK (registration_status IN (
                                                  'demographics_complete', 'disease_selected',
                                                  'consent_signed', 'anamnesis_complete',
                                                  'general_prs_complete', 'registration_complete'
                                              )),
    primary_clinic_id         UUID        REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    primary_doctor_id         UUID        REFERENCES doctors(profile_id) ON DELETE RESTRICT,
    blood_group               TEXT        CHECK (blood_group IN (
                                              'A+','A-','B+','B-','AB+','AB-','O+','O-','unknown'
                                          )),
    allergies                 TEXT,
    occupation                TEXT,
    marital_status            TEXT        CHECK (marital_status IN (
                                              'single','married','divorced','widowed','other'
                                          )),
    insurance_provider        TEXT,
    insurance_policy          TEXT,
    referred_by               TEXT,
    emergency_contact_name    TEXT,
    emergency_contact_phone   TEXT,
    registration_completed_at TIMESTAMPTZ,
    deleted_by                UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    deleted_at                TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE patient_disease_selection (
    pds_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id      UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    disease_id      TEXT        REFERENCES prs_diseases(disease_id) ON DELETE RESTRICT,
    disease_unknown BOOLEAN     NOT NULL DEFAULT FALSE,
    is_primary      BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_pds_disease_xor CHECK (
        (disease_id IS NOT NULL AND disease_unknown = FALSE)
        OR (disease_id IS NULL AND disease_unknown = TRUE)
    )
);

CREATE TABLE clinic_requests (
    request_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    request_type TEXT        NOT NULL CHECK (request_type IN (
                     'create_clinic', 'close_clinic', 'change_admin', 'change_main_branch'
                 )),
    clinic_type  TEXT        CHECK (clinic_type IN ('anava_owned', 'partner', 'mobile')),
    clinic_id    UUID        REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    region_id    UUID        NOT NULL REFERENCES regions(region_id) ON DELETE RESTRICT,
    submitted_by UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    status       TEXT        NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'approved', 'rejected', 'withdrawn')),
    payload      JSONB       NOT NULL DEFAULT '{}',
    reviewed_by  UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    review_notes TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE staff_requests (
    request_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id             UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    regional_admin_id     UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    request_type          TEXT        NOT NULL CHECK (request_type IN (
                              'open_position', 'candidate_referral', 'staff_removal'
                          )),
    position_role         TEXT        NOT NULL CHECK (position_role IN (
                              'doctor', 'clinical_assistant', 'receptionist', 'clinic_admin'
                          )),
    candidate_name        TEXT,
    candidate_email       TEXT,
    candidate_phone       TEXT,
    candidate_credentials JSONB       NOT NULL DEFAULT '{}',
    target_staff_id       UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    status                TEXT        NOT NULL DEFAULT 'pending'
                              CHECK (status IN (
                                  'pending', 'under_review', 'approved', 'rejected', 'withdrawn'
                              )),
    submitted_by          UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    reviewed_by           UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    review_notes          TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE doctor_patient_assignments (
    assignment_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    doctor_id     UUID        NOT NULL REFERENCES doctors(profile_id) ON DELETE RESTRICT,
    patient_id    UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id     UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    status        TEXT        NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'transferred', 'completed')),
    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at      TIMESTAMPTZ
);

CREATE TABLE treatment_cycles (
    cycle_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id     UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    doctor_id      UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    ca_id          UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id      UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    cycle_type     TEXT        NOT NULL CHECK (cycle_type IN ('initial', 'followup')),
    cycle_number   INTEGER     NOT NULL DEFAULT 1 CHECK (cycle_number >= 1),
    scheduled_date DATE,
    status         TEXT        NOT NULL DEFAULT 'in_progress'
                       CHECK (status IN ('in_progress', 'completed', 'cancelled')),
    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE assessment_protocol_requests (
    request_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id            UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinical_assistant_id UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    doctor_id             UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id             UUID        REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    cycle_id              UUID        REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    protocol_details      JSONB       NOT NULL DEFAULT '{}',
    status                TEXT        NOT NULL DEFAULT 'pending'
                              CHECK (status IN (
                                  'pending', 'approved', 'modification_requested', 'rejected'
                              )),
    doctor_notes          TEXT,
    submitted_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at           TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE sessions (
    session_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id              UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    doctor_id               UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    session_date            TIMESTAMPTZ NOT NULL,
    session_type            TEXT        NOT NULL DEFAULT 'in_person'
                                            CHECK (session_type IN (
                                                'in_person', 'teleconsult', 'follow_up'
                                            )),
    notes                   TEXT,
    status                  TEXT        NOT NULL DEFAULT 'scheduled'
                                            CHECK (status IN (
                                                'scheduled', 'in_progress',
                                                'completed', 'cancelled', 'missed'
                                            )),
    cycle_id                UUID        REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    clinic_id               UUID        REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    ca_id                   UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    session_phase           TEXT        CHECK (session_phase IN (
                                            'clinical_assistant', 'doctor_consultation',
                                            'additional_tests', 'doctor_additional_review',
                                            'treatment', 'home_treatment_visit'
                                        )),
    session_number_in_cycle INTEGER,
    outcome                 TEXT        CHECK (outcome IN (
                                            'session1_complete', 'treatment_plan_given',
                                            'additional_tests_requested', 'session3_complete',
                                            'home_treatment_visit_complete'
                                        )),
    started_at              TIMESTAMPTZ,
    completed_at            TIMESTAMPTZ,
    payment_status          TEXT        CHECK (payment_status IN (
                                            'not_required', 'pending', 'paid', 'waived'
                                        )),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE treatment_plans (
    plan_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id          UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    doctor_id           UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    cycle_id            UUID        NOT NULL REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    device_type         TEXT        NOT NULL,
    protocol_details    JSONB       NOT NULL DEFAULT '{}',
    sessions_prescribed INTEGER     NOT NULL CHECK (sessions_prescribed >= 1),
    standard_sessions   INTEGER     NOT NULL DEFAULT 5 CHECK (standard_sessions >= 1),
    extended_sessions   INTEGER     GENERATED ALWAYS AS (
                            GREATEST(sessions_prescribed - standard_sessions, 0)
                        ) STORED,
    status              TEXT        NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'completed', 'superseded')),
    parent_plan_id      UUID        REFERENCES treatment_plans(plan_id) ON DELETE RESTRICT,
    demo_phase_status   TEXT        NOT NULL DEFAULT 'pending'
                            CHECK (demo_phase_status IN ('pending', 'in_progress', 'completed')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE doctor_session_notes (
    note_id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id             UUID        NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    cycle_id               UUID        NOT NULL REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    patient_id             UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    doctor_id              UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    session_number         INTEGER     NOT NULL CHECK (session_number >= 1),
    session_phase          TEXT        NOT NULL CHECK (session_phase IN (
                               'doctor_consultation', 'doctor_additional_review'
                           )),
    chief_complaint        TEXT,
    clinical_observations  TEXT,
    assessment             TEXT,
    treatment_plan_notes   TEXT,
    follow_up_instructions TEXT,
    referrals              TEXT,
    note_content           TEXT,
    is_confidential        BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id, doctor_id, session_phase)
);

CREATE TABLE treatment_sessions (
    ts_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id          UUID        NOT NULL REFERENCES treatment_plans(plan_id) ON DELETE RESTRICT,
    session_id       UUID        NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    patient_id       UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    ca_id            UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    session_number   INTEGER     NOT NULL CHECK (session_number >= 1),
    billing_type     TEXT        NOT NULL CHECK (billing_type IN ('standard', 'extended')),
    status           TEXT        NOT NULL DEFAULT 'scheduled'
                         CHECK (status IN ('scheduled', 'in_progress', 'completed', 'missed')),
    payment_status   TEXT        NOT NULL DEFAULT 'pending'
                         CHECK (payment_status IN ('not_required', 'pending', 'paid', 'waived')),
    session_notes    TEXT,
    patient_feedback TEXT,
    started_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE doctor_weekly_schedules (
    schedule_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    doctor_id             UUID        NOT NULL REFERENCES doctors(profile_id) ON DELETE RESTRICT,
    clinic_id             UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    day_of_week           SMALLINT    NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    start_time            TIME        NOT NULL,
    end_time              TIME        NOT NULL,
    slot_duration_minutes INTEGER     NOT NULL DEFAULT 30 CHECK (slot_duration_minutes >= 5),
    break_start           TIME,
    break_end             TIME,
    max_appointments      INTEGER     CHECK (max_appointments >= 1),
    is_active             BOOLEAN     NOT NULL DEFAULT TRUE,
    effective_from        DATE,
    effective_until       DATE,
    created_by            UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_dws_times CHECK (start_time < end_time),
    CONSTRAINT chk_dws_break CHECK (
        (break_start IS NULL AND break_end IS NULL)
        OR (break_start IS NOT NULL AND break_end IS NOT NULL AND break_start < break_end)
    ),
    UNIQUE (doctor_id, clinic_id, day_of_week)
);

CREATE TABLE doctor_schedule_overrides (
    override_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    doctor_id             UUID        NOT NULL REFERENCES doctors(profile_id) ON DELETE RESTRICT,
    clinic_id             UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    override_date         DATE        NOT NULL,
    is_available          BOOLEAN     NOT NULL DEFAULT FALSE,
    start_time            TIME,
    end_time              TIME,
    slot_duration_minutes INTEGER     CHECK (slot_duration_minutes >= 5),
    reason                TEXT,
    created_by            UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_dso_times CHECK (
        is_available = FALSE
        OR (start_time IS NOT NULL AND end_time IS NOT NULL AND start_time < end_time)
    ),
    UNIQUE (doctor_id, override_date)
);

CREATE TABLE appointment_requests (
    request_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id               UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    patient_id              UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    doctor_id               UUID        REFERENCES doctors(profile_id) ON DELETE RESTRICT,
    cycle_id                UUID        REFERENCES treatment_cycles(cycle_id) ON DELETE SET NULL,
    request_type            TEXT        NOT NULL DEFAULT 'new'
                                CHECK (request_type IN ('new', 'reschedule', 'followup_cycle')),
    parent_appointment_id   UUID,
    preferred_date_1        DATE        NOT NULL,
    preferred_date_2        DATE,
    preferred_date_3        DATE,
    preferred_time_window   TEXT        NOT NULL DEFAULT 'any'
                                CHECK (preferred_time_window IN (
                                    'morning', 'afternoon', 'evening', 'any'
                                )),
    patient_complaint       TEXT,
    reason                  TEXT,
    urgency                 TEXT        NOT NULL DEFAULT 'normal'
                                CHECK (urgency IN ('normal', 'urgent', 'emergency')),
    status                  TEXT        NOT NULL DEFAULT 'pending'
                                CHECK (status IN (
                                    'pending', 'approved', 'rejected',
                                    'cancelled_by_patient', 'expired'
                                )),
    approved_appointment_id UUID,
    submitted_by            UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    reviewed_by             UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    review_notes            TEXT,
    expires_at              TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE appointments (
    appointment_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id              UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    patient_id             UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    doctor_id              UUID        NOT NULL REFERENCES doctors(profile_id) ON DELETE RESTRICT,
    ca_id                  UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    session_id             UUID        REFERENCES sessions(session_id) ON DELETE SET NULL,
    cycle_id               UUID        REFERENCES treatment_cycles(cycle_id) ON DELETE SET NULL,
    appointment_request_id UUID        REFERENCES appointment_requests(request_id) ON DELETE SET NULL,
    appointment_date       DATE        NOT NULL,
    start_time             TIME        NOT NULL,
    end_time               TIME        NOT NULL,
    slot_duration_minutes  INTEGER     NOT NULL DEFAULT 30 CHECK (slot_duration_minutes >= 5),
    appointment_type       TEXT        NOT NULL DEFAULT 'initial_assessment'
                               CHECK (appointment_type IN (
                                   'initial_assessment', 'doctor_consultation',
                                   'ca_session', 'treatment_session',
                                   'follow_up', 'demo_visit', 'teleconsult'
                               )),
    session_phase          TEXT        CHECK (session_phase IN (
                               'clinical_assistant', 'doctor_consultation',
                               'additional_tests', 'doctor_additional_review',
                               'treatment', 'home_treatment_visit'
                           )),
    status                 TEXT        NOT NULL DEFAULT 'scheduled'
                               CHECK (status IN (
                                   'scheduled', 'confirmed', 'checked_in',
                                   'in_progress', 'completed', 'cancelled',
                                   'no_show', 'rescheduled'
                               )),
    reason                 TEXT,
    patient_complaint      TEXT,
    notes                  TEXT,
    cancellation_reason    TEXT,
    booked_by              UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    booked_by_role         TEXT        NOT NULL,
    cancelled_by           UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    rescheduled_from       UUID        REFERENCES appointments(appointment_id) ON DELETE SET NULL,
    rescheduled_to         UUID        REFERENCES appointments(appointment_id) ON DELETE SET NULL,
    checked_in_at          TIMESTAMPTZ,
    started_at             TIMESTAMPTZ,
    completed_at           TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_appt_times CHECK (start_time < end_time)
);

ALTER TABLE appointment_requests
    ADD CONSTRAINT fk_areq_parent_appointment
    FOREIGN KEY (parent_appointment_id)
    REFERENCES appointments(appointment_id)
    ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE appointment_requests
    ADD CONSTRAINT fk_areq_approved_appointment
    FOREIGN KEY (approved_appointment_id)
    REFERENCES appointments(appointment_id)
    ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE appointment_audit_logs (
    audit_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    appointment_id    UUID        NOT NULL REFERENCES appointments(appointment_id) ON DELETE CASCADE,
    changed_by        UUID        REFERENCES profiles(id) ON DELETE SET NULL,
    changed_by_role   TEXT,
    previous_status   TEXT,
    new_status        TEXT        NOT NULL,
    previous_date     DATE,
    new_date          DATE,
    previous_time     TIME,
    new_time          TIME,
    change_reason     TEXT,
    changed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE prs_scales (
    scale_id          TEXT        PRIMARY KEY,
    scale_code        TEXT        NOT NULL UNIQUE,
    scale_name        TEXT        NOT NULL,
    is_common_scale   BOOLEAN     NOT NULL DEFAULT FALSE,
    num_diseases_used INTEGER     NOT NULL DEFAULT 1,
    applicable_for    TEXT        NOT NULL DEFAULT 'main_clinical'
                          CHECK (applicable_for IN (
                              'general_registration', 'main_clinical', 'followup', 'all'
                          )),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE prs_disease_scale_map (
    ds_map_id     TEXT        PRIMARY KEY,
    disease_id    TEXT        NOT NULL REFERENCES prs_diseases(disease_id) ON DELETE CASCADE,
    scale_id      TEXT        NOT NULL REFERENCES prs_scales(scale_id) ON DELETE CASCADE,
    display_order INTEGER     NOT NULL DEFAULT 0,
    is_required   BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (disease_id, scale_id)
);

CREATE TABLE prs_questions (
    question_id     TEXT        PRIMARY KEY,
    question_code   TEXT        NOT NULL UNIQUE,
    disease_id      TEXT        REFERENCES prs_diseases(disease_id) ON DELETE SET NULL,
    scale_id        TEXT        REFERENCES prs_scales(scale_id) ON DELETE SET NULL,
    ds_map_id       TEXT        REFERENCES prs_disease_scale_map(ds_map_id) ON DELETE SET NULL,
    question_text   TEXT        NOT NULL,
    answer_type     TEXT        NOT NULL CHECK (answer_type IN (
                        'likert', 'radio', 'slider', 'checkbox', 'text', 'number', 'table'
                    )),
    min_value       NUMERIC,
    max_value       NUMERIC,
    is_required     BOOLEAN     NOT NULL DEFAULT TRUE,
    skip_logic      TEXT,
    display_order   INTEGER     NOT NULL DEFAULT 0,
    is_common_scale BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE prs_options (
    option_id     TEXT        PRIMARY KEY,
    question_id   TEXT        NOT NULL REFERENCES prs_questions(question_id) ON DELETE CASCADE,
    option_label  TEXT        NOT NULL,
    option_value  TEXT        NOT NULL,
    points        NUMERIC     NOT NULL DEFAULT 0,
    display_order INTEGER     NOT NULL DEFAULT 0,
    status        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (question_id, option_value)
);

CREATE TABLE prs_scale_question_map (
    sq_map_id     TEXT        PRIMARY KEY,
    scale_id      TEXT        NOT NULL REFERENCES prs_scales(scale_id) ON DELETE CASCADE,
    question_id   TEXT        NOT NULL REFERENCES prs_questions(question_id) ON DELETE CASCADE,
    display_order INTEGER     NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (scale_id, question_id)
);

CREATE TABLE prs_disease_question_map (
    dq_map_id     TEXT        PRIMARY KEY,
    disease_id    TEXT        NOT NULL REFERENCES prs_diseases(disease_id) ON DELETE CASCADE,
    question_id   TEXT        NOT NULL REFERENCES prs_questions(question_id) ON DELETE CASCADE,
    display_order INTEGER     NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (disease_id, question_id)
);

CREATE TABLE prs_assessment_instances (
    instance_id      TEXT        PRIMARY KEY,
    disease_id       TEXT        NOT NULL REFERENCES prs_diseases(disease_id) ON DELETE CASCADE,
    patient_id       UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    session_id       UUID        REFERENCES sessions(session_id) ON DELETE SET NULL,
    cycle_id         UUID        REFERENCES treatment_cycles(cycle_id) ON DELETE SET NULL,
    initiated_by     assessment_taken_by NOT NULL DEFAULT 'patient',
    administered_by  UUID        REFERENCES profiles(id) ON DELETE SET NULL,
    assessment_stage TEXT        NOT NULL DEFAULT 'general_registration'
                         CHECK (assessment_stage IN (
                             'general_registration', 'main_clinical', 'followup'
                         )),
    status           TEXT        NOT NULL DEFAULT 'in_progress'
                         CHECK (status IN ('in_progress', 'completed', 'abandoned')),
    started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ,
    final_result     TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE prs_responses (
    response_id    TEXT        PRIMARY KEY,
    instance_id    TEXT        NOT NULL REFERENCES prs_assessment_instances(instance_id) ON DELETE CASCADE,
    question_id    TEXT        NOT NULL REFERENCES prs_questions(question_id) ON DELETE CASCADE,
    given_response TEXT,
    response_value NUMERIC,
    time_stamp     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (instance_id, question_id)
);

CREATE TABLE prs_scale_results (
    scale_result_id  TEXT        PRIMARY KEY,
    instance_id      TEXT        NOT NULL REFERENCES prs_assessment_instances(instance_id) ON DELETE CASCADE,
    scale_id         TEXT        NOT NULL REFERENCES prs_scales(scale_id) ON DELETE CASCADE,
    calculated_value NUMERIC,
    max_possible     NUMERIC,
    percentage       NUMERIC     GENERATED ALWAYS AS (
                         CASE WHEN max_possible > 0
                              THEN ROUND((calculated_value / max_possible) * 100, 2)
                              ELSE NULL END
                     ) STORED,
    severity_level   TEXT,
    severity_label   TEXT,
    subscale_scores  JSONB       NOT NULL DEFAULT '{}',
    risk_flags       JSONB       NOT NULL DEFAULT '[]',
    raw_score_data   JSONB       NOT NULL DEFAULT '{}',
    time_stamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (instance_id, scale_id)
);

CREATE TABLE prs_final_results (
    final_result_id        TEXT        PRIMARY KEY,
    instance_id            TEXT        NOT NULL UNIQUE
                               REFERENCES prs_assessment_instances(instance_id) ON DELETE CASCADE,
    calculated_value       NUMERIC,
    max_possible           NUMERIC,
    percentage             NUMERIC     GENERATED ALWAYS AS (
                               CASE WHEN max_possible > 0
                                    THEN ROUND((calculated_value / max_possible) * 100, 2)
                                    ELSE NULL END
                           ) STORED,
    scales_completed       INTEGER     NOT NULL DEFAULT 0,
    scales_total           INTEGER     NOT NULL DEFAULT 0,
    overall_severity       TEXT,
    overall_severity_label TEXT,
    scale_summaries        JSONB       NOT NULL DEFAULT '[]',
    all_risk_flags         JSONB       NOT NULL DEFAULT '[]',
    composite_summary      TEXT,
    time_stamp             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE prs_assessment_instances
    ADD CONSTRAINT fk_instance_final_result
    FOREIGN KEY (final_result)
    REFERENCES prs_final_results(final_result_id)
    ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE patient_scale_assignments (
    psa_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id        UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    scale_id          TEXT        NOT NULL REFERENCES prs_scales(scale_id) ON DELETE RESTRICT,
    assessment_stage  TEXT        NOT NULL CHECK (assessment_stage IN (
                          'general_registration', 'main_clinical', 'followup'
                      )),
    assigned_by       UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    assignment_reason TEXT        CHECK (assignment_reason IN (
                          'auto_disease_match', 'ca_selected', 'doctor_override'
                      )),
    is_active         BOOLEAN     NOT NULL DEFAULT TRUE,
    deactivated_at    TIMESTAMPTZ,
    deactivated_by    UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE anamnesis_assessments (
    anamnesis_id TEXT        PRIMARY KEY,
    patient_id   UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    submitted_by UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    taken_by     assessment_taken_by NOT NULL DEFAULT 'patient',
    cycle_id     UUID        REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    version      INTEGER     NOT NULL DEFAULT 1 CHECK (version >= 1),
    status       TEXT        NOT NULL DEFAULT 'in_progress'
                     CHECK (status IN ('in_progress', 'completed')),
    completed_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (patient_id, version)
);

CREATE TABLE anamnesis_questions (
    question_id            TEXT        PRIMARY KEY,
    section_number         INTEGER     NOT NULL,
    section_title          TEXT        NOT NULL,
    question_code          TEXT        NOT NULL UNIQUE,
    question_text          TEXT        NOT NULL,
    answer_type            TEXT        NOT NULL CHECK (answer_type IN (
                               'text', 'textarea', 'radio',
                               'select', 'checkbox', 'conditional_text'
                           )),
    is_required            BOOLEAN     NOT NULL DEFAULT TRUE,
    display_order          INTEGER     NOT NULL DEFAULT 0,
    depends_on_question_id TEXT        REFERENCES anamnesis_questions(question_id),
    depends_on_value       TEXT,
    helper_text            TEXT,
    status                 BOOLEAN     NOT NULL DEFAULT TRUE
);

CREATE TABLE anamnesis_options (
    option_id     TEXT        PRIMARY KEY,
    question_id   TEXT        NOT NULL REFERENCES anamnesis_questions(question_id) ON DELETE CASCADE,
    option_label  TEXT        NOT NULL,
    option_value  TEXT        NOT NULL,
    display_order INTEGER     NOT NULL DEFAULT 0,
    UNIQUE (question_id, option_value)
);

CREATE TABLE anamnesis_responses (
    response_id     TEXT        PRIMARY KEY,
    anamnesis_id    TEXT        NOT NULL REFERENCES anamnesis_assessments(anamnesis_id) ON DELETE CASCADE,
    question_id     TEXT        NOT NULL REFERENCES anamnesis_questions(question_id),
    response_value  TEXT,
    response_values TEXT[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE patient_eeg_files (
    eeg_id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id                UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id                 UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    cycle_id                  UUID        REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    session_id                UUID        REFERENCES sessions(session_id) ON DELETE RESTRICT,
    performed_by              UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    reviewed_by               UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    eeg_type                  TEXT        NOT NULL DEFAULT 'resting_state'
                                              CHECK (eeg_type IN (
                                                  'resting_state', 'sleep_study', 'ambulatory',
                                                  'evoked_potential', 'other'
                                              )),
    duration_minutes          INTEGER,
    raw_data_s3_key           TEXT        UNIQUE,
    raw_file_name             TEXT,
    raw_file_size             BIGINT,
    raw_checksum              TEXT,
    raw_checksum_algorithm    TEXT        NOT NULL DEFAULT 'sha256',
    report_s3_key             TEXT        UNIQUE,
    report_file_name          TEXT,
    report_file_size          BIGINT,
    report_checksum           TEXT,
    report_checksum_algorithm TEXT        NOT NULL DEFAULT 'sha256',
    superseded_by             UUID        REFERENCES patient_eeg_files(eeg_id) ON DELETE RESTRICT,
    recording_notes           TEXT,
    clinical_findings         TEXT,
    is_abnormal               BOOLEAN,
    status                    TEXT        NOT NULL DEFAULT 'raw_uploaded'
                                              CHECK (status IN (
                                                  'raw_uploaded', 'report_pending',
                                                  'report_ready', 'reviewed'
                                              )),
    performed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at               TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE patient_medical_history_files (
    mhf_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id         UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id          UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    cycle_id           UUID        REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    uploaded_by        UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    document_type      TEXT        NOT NULL CHECK (document_type IN (
                                       'past_prescription', 'lab_report', 'imaging_report',
                                       'hospital_discharge', 'referral_letter',
                                       'vaccination_record', 'insurance_document',
                                       'previous_assessment', 'doctor_notes', 'other'
                                   )),
    s3_key             TEXT        NOT NULL UNIQUE,
    file_name          TEXT        NOT NULL,
    file_size          BIGINT,
    mime_type          TEXT,
    checksum           TEXT,
    checksum_algorithm TEXT        NOT NULL DEFAULT 'sha256',
    description        TEXT,
    document_date      DATE,
    source_provider    TEXT,
    is_deleted         BOOLEAN     NOT NULL DEFAULT FALSE,
    deleted_by         UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    deleted_at         TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE consent_templates (
    template_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    consent_type   TEXT        NOT NULL CHECK (consent_type IN (
                       'patient_onboarding', 'patient_clinic_exit',
                       'patient_clinic_transfer', 'patient_relocation_transfer',
                       'staff_onboarding', 'staff_offboarding',
                       'clinic_join_anava', 'clinic_leave_anava'
                   )),
    version        INTEGER     NOT NULL DEFAULT 1 CHECK (version >= 1),
    title          TEXT        NOT NULL,
    content        TEXT        NOT NULL,
    content_hash   TEXT        GENERATED ALWAYS AS (
                       encode(sha256(content::bytea), 'hex')
                   ) STORED,
    effective_date DATE,
    expiry_date    DATE,
    is_active      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (consent_type, version)
);

CREATE TABLE consent_records (
    consent_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    consent_type            TEXT        NOT NULL CHECK (consent_type IN (
                                'patient_onboarding', 'patient_clinic_exit',
                                'patient_clinic_transfer', 'patient_relocation_transfer',
                                'staff_onboarding', 'staff_offboarding',
                                'clinic_join_anava', 'clinic_leave_anava'
                            )),
    template_id             UUID        NOT NULL REFERENCES consent_templates(template_id) ON DELETE RESTRICT,
    patient_id              UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    staff_id                UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id               UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    status                  TEXT        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'signed', 'revoked')),
    signed_at               TIMESTAMPTZ,
    signed_by               UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    witness_id              UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    ip_address              INET,
    signature_data          TEXT,
    pdf_s3_key              TEXT,
    content_hash_at_signing TEXT,
    revoked_at              TIMESTAMPTZ,
    revoked_by              UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_consent_signer CHECK (
        patient_id IS NOT NULL OR staff_id IS NOT NULL
    )
);

CREATE TABLE patient_clinic_transfers (
    pct_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id      UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    from_clinic_id  UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    to_clinic_id    UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    from_doctor_id  UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    to_doctor_id    UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    transfer_reason TEXT        NOT NULL DEFAULT 'clinic_closure'
                        CHECK (transfer_reason IN (
                            'clinic_closure', 'patient_relocation',
                            'patient_request', 'doctor_transfer'
                        )),
    active_cycle_id UUID        REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    status          TEXT        NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'consented', 'completed', 'declined')),
    consent_id      UUID        REFERENCES consent_records(consent_id) ON DELETE RESTRICT,
    initiated_by    UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE products (
    product_id  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL,
    description TEXT,
    category    TEXT        NOT NULL CHECK (category IN ('device', 'accessory')),
    price       NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
    sku         TEXT        UNIQUE,
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE store_orders (
    order_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id        UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id         UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    initiated_by      UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    approved_by       UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    order_type        TEXT        NOT NULL CHECK (order_type IN ('device', 'accessory')),
    status            TEXT        NOT NULL DEFAULT 'pending_doctor_approval'
                          CHECK (status IN (
                              'pending_doctor_approval', 'doctor_approved',
                              'pending_dispatch', 'dispatched_to_clinic',
                              'received_at_clinic', 'collected_by_patient', 'cancelled'
                          )),
    total_amount      NUMERIC(10, 2) CHECK (total_amount >= 0),
    treatment_plan_id UUID        REFERENCES treatment_plans(plan_id) ON DELETE RESTRICT,
    cancelled_by      UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    cancelled_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE order_items (
    item_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id   UUID        NOT NULL REFERENCES store_orders(order_id) ON DELETE RESTRICT,
    product_id UUID        NOT NULL REFERENCES products(product_id) ON DELETE RESTRICT,
    quantity   INTEGER     NOT NULL DEFAULT 1 CHECK (quantity >= 1),
    unit_price NUMERIC(10, 2) NOT NULL CHECK (unit_price >= 0)
);

CREATE TABLE inventory (
    inventory_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id   UUID        NOT NULL REFERENCES products(product_id) ON DELETE RESTRICT,
    clinic_id    UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    quantity     INTEGER     NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (product_id, clinic_id)
);

CREATE TABLE stock_transfers (
    st_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id     UUID        NOT NULL REFERENCES products(product_id) ON DELETE RESTRICT,
    from_type      TEXT        NOT NULL CHECK (from_type IN ('super_admin', 'main_branch')),
    from_clinic_id UUID        REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    to_clinic_id   UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    quantity       INTEGER     NOT NULL CHECK (quantity >= 1),
    order_id       UUID        REFERENCES store_orders(order_id) ON DELETE RESTRICT,
    status         TEXT        NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'dispatched', 'received')),
    initiated_by   UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    received_by    UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dispatched_at  TIMESTAMPTZ,
    received_at    TIMESTAMPTZ,
    CONSTRAINT chk_stock_transfer_from CHECK (
        (from_type = 'super_admin' AND from_clinic_id IS NULL)
        OR (from_type = 'main_branch' AND from_clinic_id IS NOT NULL)
    )
);

CREATE TABLE device_assignments (
    da_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id      UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id       UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    plan_id         UUID        NOT NULL REFERENCES treatment_plans(plan_id) ON DELETE RESTRICT,
    assigned_by     UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    device_type     TEXT        NOT NULL,
    purchase_status TEXT        NOT NULL DEFAULT 'purchase_prompted'
                        CHECK (purchase_status IN (
                            'purchase_prompted', 'pending_payment',
                            'purchased', 'collected', 'returned'
                        )),
    order_id        UUID        REFERENCES store_orders(order_id) ON DELETE RESTRICT,
    prompted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    purchased_at    TIMESTAMPTZ,
    collected_at    TIMESTAMPTZ,
    returned_at     TIMESTAMPTZ,
    returned_by     UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    return_reason   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE payments (
    payment_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID        REFERENCES sessions(session_id) ON DELETE RESTRICT,
    order_id            UUID        REFERENCES store_orders(order_id) ON DELETE RESTRICT,
    idempotency_key     TEXT        NOT NULL UNIQUE,
    razorpay_order_id   TEXT        UNIQUE,
    razorpay_payment_id TEXT        UNIQUE,
    amount              NUMERIC(10, 2) NOT NULL CHECK (amount >= 0),
    currency            TEXT        NOT NULL DEFAULT 'INR',
    payment_method      TEXT        CHECK (payment_method IN (
                                        'cash', 'card', 'upi', 'bank_transfer', 'waived'
                                    )),
    status              TEXT        NOT NULL DEFAULT 'pending'
                                        CHECK (status IN (
                                            'pending', 'paid', 'failed', 'refunded', 'waived'
                                        )),
    gateway_response    JSONB       NOT NULL DEFAULT '{}',
    waived_by           UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    waived_reason       TEXT,
    paid_at             TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_payment_target CHECK (
        (session_id IS NOT NULL AND order_id IS NULL)
        OR (session_id IS NULL AND order_id IS NOT NULL)
    )
);

CREATE TABLE audit_logs (
    log_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    table_name TEXT        NOT NULL,
    operation  TEXT        NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
    record_id  TEXT,
    old_data   JSONB,
    new_data   JSONB,
    changed_by UUID,
    clinic_id  UUID,
    ip_address INET,
    request_id TEXT,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE activity_logs (
    log_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id    UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    actor_role  TEXT        NOT NULL,
    request_id  TEXT,
    category    TEXT        NOT NULL CHECK (category IN (
                                'auth', 'admin', 'patient', 'clinical',
                                'appointment', 'assessment', 'consent',
                                'data_access', 'staff', 'store', 'system'
                            )),
    event_type  TEXT        NOT NULL,
    entity_type TEXT,
    entity_id   UUID,
    clinic_id   UUID        REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    region_id   UUID        REFERENCES regions(region_id) ON DELETE RESTRICT,
    metadata    JSONB       NOT NULL DEFAULT '{}',
    ip_address  INET,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE notifications (
    notification_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    recipient_id      UUID        NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    sender_id         UUID        REFERENCES profiles(id) ON DELETE SET NULL,
    clinic_id         UUID        REFERENCES clinics(clinic_id) ON DELETE SET NULL,
    type              TEXT        NOT NULL DEFAULT 'system'
                                      CHECK (type IN (
                                          'appointment', 'clinical', 'store',
                                          'admin', 'consent', 'system'
                                      )),
    delivery_channel  TEXT        NOT NULL DEFAULT 'in_app'
                                      CHECK (delivery_channel IN (
                                          'in_app', 'email', 'sms', 'push'
                                      )),
    title             TEXT        NOT NULL,
    body              TEXT,
    entity_type       TEXT,
    entity_id         UUID,
    metadata          JSONB       NOT NULL DEFAULT '{}',
    is_read           BOOLEAN     NOT NULL DEFAULT FALSE,
    read_at           TIMESTAMPTZ,
    delivered_at      TIMESTAMPTZ,
    delivery_attempts INTEGER     NOT NULL DEFAULT 0,
    expires_at        TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
