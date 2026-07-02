-- ============================================================
-- Anava Clinic — MasterDB_Anava.sql
-- AUTO-GENERATED
-- ============================================================



-- SOURCE: 01_extensions.sql

-- ============================================================
-- Anava Clinic — DB Schema
-- File 01: Extensions
-- Run this first on a fresh RDS PostgreSQL 14+ instance
-- ============================================================

-- pgcrypto: required for pgp_sym_encrypt (PHI column-level encryption)
-- and for sha256() used in consent_templates.content_hash.
-- gen_random_uuid() is built-in from PG13+; uuid-ossp NOT needed.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- SOURCE: 02_core_tables.sql

-- ============================================================
-- Anava Clinic — DB Schema
-- File 02: Core Tables
-- profiles, prs_diseases (early dep), regions, clinics,
-- admins, clinic_staff_assignments
-- ============================================================

-- ------------------------------------------------------------
-- profiles — universal user table, all roles use this
-- ------------------------------------------------------------
CREATE TABLE profiles (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cognito_sub          TEXT UNIQUE NOT NULL,
    email                TEXT UNIQUE NOT NULL CHECK (email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    first_name           TEXT NOT NULL,
    last_name            TEXT NOT NULL,
    phone                TEXT,
    role                 TEXT NOT NULL CHECK (role IN (
                             'super_admin', 'regional_admin', 'clinic_admin',
                             'doctor', 'clinical_assistant', 'receptionist', 'patient'
                         )),
    gender               TEXT CHECK (gender IN ('male', 'female', 'other')),
    dob                  DATE,
    address              TEXT,
    city                 TEXT,
    state                TEXT,
    country              TEXT,
    profile_photo_s3_key TEXT,
    pincode              TEXT,
    language_pref        TEXT NOT NULL DEFAULT 'en',
    is_active            BOOLEAN NOT NULL DEFAULT TRUE,
    -- Soft-delete: deactivate instead of physically removing any profile
    -- that has ever been attached to clinical records.
    deleted_by           UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    deleted_at           TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------
-- prs_diseases — placed here (before patient_disease_selection
-- and prs_scales which both reference it)
-- TEXT PK matches v6 composite format: e.g. 'CHRONICPAIN/2026'
-- Keeping v6 structure exactly — code depends on these keys
-- ------------------------------------------------------------
CREATE TABLE prs_diseases (
    disease_id   TEXT PRIMARY KEY,
    disease_code TEXT NOT NULL UNIQUE,
    disease_name TEXT NOT NULL,
    version      TEXT NOT NULL DEFAULT 'v1.0',
    status       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------
-- regions — geographic containers (country + state unique pair)
-- ------------------------------------------------------------
CREATE TABLE regions (
    region_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    region_name       TEXT NOT NULL,
    country           TEXT NOT NULL,
    state             TEXT NOT NULL,
    regional_admin_id UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (country, state)
);

-- ------------------------------------------------------------
-- clinics — one per physical location
-- ------------------------------------------------------------
CREATE TABLE clinics (
    clinic_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_code     TEXT UNIQUE NOT NULL,
    clinic_name     TEXT NOT NULL,
    clinic_type     TEXT NOT NULL CHECK (clinic_type IN ('anava_owned', 'partner', 'mobile')),
    owner_name      TEXT NOT NULL DEFAULT 'Anava',
    status          TEXT NOT NULL DEFAULT 'setup'
                        CHECK (status IN ('setup', 'active', 'pending_closure', 'closed')),
    region_id       UUID NOT NULL REFERENCES regions(region_id) ON DELETE RESTRICT,
    clinic_admin_id UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    is_main_branch  BOOLEAN NOT NULL DEFAULT FALSE,
    timezone        TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    address         TEXT,
    city            TEXT,
    state           TEXT,
    country         TEXT NOT NULL DEFAULT 'India',
    phone           TEXT,
    email           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------
-- admins — super_admin / regional_admin / clinic_admin detail
-- ------------------------------------------------------------
CREATE TABLE admins (
    admin_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id            UUID UNIQUE NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    admin_type            TEXT NOT NULL CHECK (admin_type IN (
                              'super_admin', 'regional_admin', 'clinic_admin'
                          )),
    region_id             UUID REFERENCES regions(region_id) ON DELETE RESTRICT,
    clinic_id             UUID REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    force_password_change BOOLEAN NOT NULL DEFAULT FALSE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_admins_scope CHECK (
        (admin_type = 'super_admin'    AND region_id IS NULL AND clinic_id IS NULL)
        OR (admin_type = 'regional_admin' AND region_id IS NOT NULL AND clinic_id IS NULL)
        OR (admin_type = 'clinic_admin'   AND clinic_id IS NOT NULL)
    )
);

-- ------------------------------------------------------------
-- clinic_staff_assignments — staff ↔ clinic membership
-- Soft-delete via removed_at; is_active flag for quick filter
-- ------------------------------------------------------------
CREATE TABLE clinic_staff_assignments (
    assignment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id     UUID NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    profile_id    UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    staff_role    TEXT NOT NULL CHECK (staff_role IN (
                      'clinic_admin', 'doctor', 'clinical_assistant', 'receptionist'
                  )),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    joined_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    removed_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Prevent duplicate active assignments for the same staff member at the same clinic.
    UNIQUE (clinic_id, profile_id)
);

-- SOURCE: 03_staff_role_tables.sql

-- ============================================================
-- Anava Clinic — DB Schema
-- File 03: Staff Role Tables
-- doctors, clinical_assistants, receptionists
-- ============================================================

-- ------------------------------------------------------------
-- doctors — doctor-specific metadata
--
-- active_patient_count REMOVED: application-maintained counters
-- under concurrent load drift silently (read-modify-write race).
-- Use view v_doctor_active_patient_counts for live count queries.
-- Capacity check: query view and compare against max_patient_count.
-- ------------------------------------------------------------
CREATE TABLE doctors (
    doctor_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id           UUID        UNIQUE NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    specialization       TEXT,
    license_number       TEXT,
    hospital_affiliation TEXT,
    max_patient_count    INTEGER     NOT NULL DEFAULT 30 CHECK (max_patient_count >= 1),
    availability_status  TEXT        NOT NULL DEFAULT 'available'
                             CHECK (availability_status IN (
                                 'available',
                                 'at_capacity',
                                 'on_leave',
                                 'inactive'
                             )),
    deleted_by           UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    deleted_at           TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Live active patient count — query this view instead of a cached counter.
-- Counts doctor_patient_assignments where status = 'active'.
-- Created after doctor_patient_assignments (in 06_clinical_tables.sql).
-- View created in 06_clinical_tables.sql to respect dependency order.

-- ------------------------------------------------------------
-- clinical_assistants — CA-specific metadata
-- clinic_id: home clinic (CAs work at one clinic base)
-- supervising_doctor_id removed — CAs serve multiple doctors
-- via ca_doctor_assignments junction table
-- ------------------------------------------------------------
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

-- ------------------------------------------------------------
-- ca_doctor_assignments — CA ↔ Doctor many-to-many
-- A CA can work under multiple doctors in the same clinic.
-- is_primary: TRUE for the CA's main/default supervising doctor.
-- removed_at NULL = assignment still active.
-- ------------------------------------------------------------
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

-- ------------------------------------------------------------
-- receptionists — receptionist-specific metadata
-- clinic_id: clinic where this receptionist is based
-- ------------------------------------------------------------
CREATE TABLE receptionists (
    receptionist_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id      UUID        UNIQUE NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id       UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    deleted_by      UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    deleted_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- SOURCE: 04_patient_tables.sql

-- ============================================================
-- Anava Clinic — DB Schema
-- File 04: Patient Tables
-- patients, patient_disease_selection
-- ============================================================

-- ------------------------------------------------------------
-- patients — patient-specific data + registration state machine
-- registration_status tracks the 6-step Phase 1 flow.
--
-- mrn: NOT NULL enforced; trigger fn_generate_mrn() sets the
-- value BEFORE INSERT so the NOT NULL constraint is always met.
-- Format: 'ANV-XXXXXXXX' (8 digits, supports up to 99,999,999 patients).
-- Sequence starts at 10001 → first MRN: 'ANV-00010001'.
--
-- primary_doctor_id: FK → doctors(profile_id) enforces that only
-- valid doctors can be set as primary doctor. Stores the doctor's
-- profile_id UUID (consistent with all other doctor references).
-- ------------------------------------------------------------
CREATE SEQUENCE IF NOT EXISTS mrn_seq START 10001;

CREATE TABLE patients (
    patient_id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id                UUID        UNIQUE NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    mrn                       TEXT        NOT NULL UNIQUE,  -- set by fn_generate_mrn() BEFORE INSERT
    registration_status       TEXT        NOT NULL DEFAULT 'demographics_complete'
                                              CHECK (registration_status IN (
                                                  'demographics_complete',
                                                  'disease_selected',
                                                  'consent_signed',
                                                  'anamnesis_complete',
                                                  'general_prs_complete',
                                                  'registration_complete'
                                              )),
    primary_clinic_id         UUID        REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    primary_doctor_id         UUID        REFERENCES doctors(profile_id) ON DELETE RESTRICT,
    -- medical data
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
    -- soft delete — deactivate, never physically remove PHI records
    deleted_by                UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    deleted_at                TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------
-- patient_disease_selection — diseases selected at registration
--
-- Supports comorbidities: one row per disease per patient.
-- UNIQUE(patient_id, disease_id) prevents duplicate disease rows.
-- is_primary marks the patient's main presenting condition.
-- disease_unknown: patient doesn't know their diagnosis — in this
-- case disease_id IS NULL and disease_unknown = TRUE (one row max
-- per patient with unknown; enforced at application layer).
-- ------------------------------------------------------------
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

-- Prevent same disease appearing twice for same patient.
-- Partial index on disease_id IS NOT NULL so multiple 'unknown' rows
-- are blocked at the application layer, not the DB constraint.
CREATE UNIQUE INDEX idx_pds_patient_disease_unique
    ON patient_disease_selection (patient_id, disease_id)
    WHERE disease_id IS NOT NULL;

-- SOURCE: 05_request_tables.sql

-- ============================================================
-- Anava Clinic — DB Schema
-- File 05: Request Tables
-- clinic_requests, staff_requests
-- ============================================================

-- ------------------------------------------------------------
-- clinic_requests — all clinic lifecycle requests
-- (create, close, change admin, change main branch)
-- payload JSONB holds request-type-specific fields
-- ------------------------------------------------------------
CREATE TABLE clinic_requests (
    request_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_type TEXT NOT NULL CHECK (request_type IN (
                     'create_clinic', 'close_clinic',
                     'change_admin', 'change_main_branch'
                 )),
    clinic_type  TEXT CHECK (clinic_type IN ('anava_owned', 'partner', 'mobile')),
    clinic_id    UUID REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    region_id    UUID NOT NULL REFERENCES regions(region_id) ON DELETE RESTRICT,
    submitted_by UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    status       TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'approved', 'rejected', 'withdrawn')),
    payload      JSONB NOT NULL DEFAULT '{}',
    reviewed_by  UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    review_notes TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------
-- staff_requests — staff hiring (open position / candidate referral)
-- and staff removal requests
-- ------------------------------------------------------------
CREATE TABLE staff_requests (
    request_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id              UUID NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    regional_admin_id      UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    request_type           TEXT NOT NULL CHECK (request_type IN (
                               'open_position', 'candidate_referral', 'staff_removal'
                           )),
    position_role          TEXT NOT NULL CHECK (position_role IN (
                               'doctor', 'clinical_assistant', 'receptionist', 'clinic_admin'
                           )),
    candidate_name         TEXT,
    candidate_email        TEXT,
    candidate_phone        TEXT,
    candidate_credentials  JSONB NOT NULL DEFAULT '{}',
    target_staff_id        UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    status                 TEXT NOT NULL DEFAULT 'pending'
                               CHECK (status IN (
                                   'pending', 'under_review', 'approved',
                                   'rejected', 'withdrawn'
                               )),
    submitted_by           UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    reviewed_by            UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    review_notes           TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- SOURCE: 06_clinical_tables.sql

-- ============================================================
-- Anava Clinic — DB Schema
-- File 06: Clinical Tables
-- doctor_patient_assignments, treatment_cycles,
-- assessment_protocol_requests, sessions,
-- treatment_plans, treatment_sessions
-- (device_assignments is in 10_store_tables.sql — depends on store_orders)
-- ============================================================

-- ------------------------------------------------------------
-- doctor_patient_assignments — doctor ↔ patient link
-- Load-balanced: doctor with MIN(active_patient_count) assigned
-- ------------------------------------------------------------
CREATE TABLE doctor_patient_assignments (
    assignment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doctor_id     UUID NOT NULL REFERENCES doctors(profile_id) ON DELETE RESTRICT,
    patient_id    UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id     UUID NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    status        TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'transferred', 'completed')),
    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at      TIMESTAMPTZ
);

-- Live active patient count per doctor. Replaces the dropped active_patient_count column.
-- Query: SELECT * FROM v_doctor_active_patient_counts WHERE doctor_id = $1;
-- Then compare to doctors.max_patient_count for capacity check.
CREATE VIEW v_doctor_active_patient_counts AS
SELECT
    doctor_id,
    COUNT(*) AS active_patient_count
FROM doctor_patient_assignments
WHERE status = 'active'
GROUP BY doctor_id;

-- ------------------------------------------------------------
-- treatment_cycles — groups all sessions in one clinical cycle
-- cycle_number: 1 = initial, 2,3... = follow-up blocks
-- ------------------------------------------------------------
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

-- ------------------------------------------------------------
-- assessment_protocol_requests — CA designs protocol, Doctor authorizes
-- cycle_id NULL for initial block (no block created yet at Flow M time)
-- Multiple records per patient possible (if modification requested)
-- ------------------------------------------------------------
CREATE TABLE assessment_protocol_requests (
    request_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id            UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinical_assistant_id UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    doctor_id             UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id             UUID REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    cycle_id              UUID REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    protocol_details      JSONB NOT NULL DEFAULT '{}',
    -- { eeg_type: string, main_prs_scale_ids: UUID[], additional_tests: string[] }
    status                TEXT NOT NULL DEFAULT 'pending'
                              CHECK (status IN (
                                  'pending', 'approved',
                                  'modification_requested', 'rejected'
                              )),
    doctor_notes          TEXT,
    submitted_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at           TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------
-- sessions — extends v6 sessions table with Anava clinical flow
--
-- v6 base columns kept exactly:
--   session_date, session_type ('in_person','teleconsult','follow_up')
--   notes, status ('scheduled','in_progress','completed','cancelled')
--
-- Anava extensions (all nullable — v6 sessions have no block/phase):
--   cycle_id, clinic_id, ca_id, session_phase, session_number_in_cycle
--   outcome, started_at, completed_at, payment_status
--
-- status: v6 values kept + 'missed' added for Anava no-show tracking
-- session_phase NULL = plain v6-style session (no block context)
-- doctor_id NULL = CA-only session (S1, treatment)
-- ca_id NULL = doctor-only session (S2, S4)
-- ------------------------------------------------------------
CREATE TABLE sessions (
    session_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- ── v6 base columns ──────────────────────────────────────
    patient_id              UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    doctor_id               UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    session_date            TIMESTAMPTZ NOT NULL,  -- scheduled appointment datetime; must be explicitly provided
    session_type            TEXT        NOT NULL DEFAULT 'in_person'
                                            CHECK (session_type IN (
                                                'in_person', 'teleconsult', 'follow_up'
                                            )),
    notes                   TEXT,
    status                  TEXT        NOT NULL DEFAULT 'scheduled'
                                            CHECK (status IN (
                                                'scheduled', 'in_progress',
                                                'completed', 'cancelled',
                                                'missed'  -- Anava addition: patient no-show
                                            )),

    -- ── Anava extensions ─────────────────────────────────────
    -- NULL for plain v6-style sessions; set for Anava block-based sessions
    cycle_id                UUID        REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    clinic_id               UUID        REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    ca_id                   UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    session_phase           TEXT        CHECK (session_phase IN (
                                            'clinical_assistant',
                                            'doctor_consultation',
                                            'additional_tests',
                                            'doctor_additional_review',
                                            'treatment',
                                            'home_treatment_visit'
                                        )),
    session_number_in_cycle INTEGER,
    outcome                 TEXT        CHECK (outcome IN (
                                            'session1_complete',
                                            'treatment_plan_given',
                                            'additional_tests_requested',
                                            'session3_complete',
                                            'home_treatment_visit_complete'
                                        )),
    started_at              TIMESTAMPTZ,
    completed_at            TIMESTAMPTZ,
    -- NULL = payment determination pending (e.g. free-phase sessions, or type TBD)
    payment_status          TEXT        CHECK (payment_status IN (
                                            'not_required', 'pending', 'paid', 'waived'
                                        )),

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------
-- treatment_plans — Doctor's treatment prescription
-- parent_plan_id: NULL for first plan; set for follow-up plans
-- extended_sessions GENERATED column: sessions beyond standard 5
-- status 'superseded' when a newer plan replaces this one
-- ------------------------------------------------------------
CREATE TABLE treatment_plans (
    plan_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id         UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    doctor_id          UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    cycle_id           UUID NOT NULL REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    device_type        TEXT NOT NULL,
    protocol_details   JSONB NOT NULL DEFAULT '{}',
    -- { frequency_hz: number, duration_minutes: number, protocol_name: string, notes: string }
    sessions_prescribed  INTEGER NOT NULL CHECK (sessions_prescribed >= 1),
    standard_sessions    INTEGER NOT NULL DEFAULT 5 CHECK (standard_sessions >= 1),
    extended_sessions    INTEGER GENERATED ALWAYS AS (
                             GREATEST(sessions_prescribed - standard_sessions, 0)
                         ) STORED,
    status             TEXT NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active', 'completed', 'superseded')),
    parent_plan_id     UUID REFERENCES treatment_plans(plan_id) ON DELETE RESTRICT,
    demo_phase_status  TEXT NOT NULL DEFAULT 'pending'
                           CHECK (demo_phase_status IN ('pending', 'in_progress', 'completed')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------
-- doctor_session_notes — structured notes per doctor per session
-- One row per doctor per session (UNIQUE session_id, doctor_id).
-- If two doctors review same session, they each get their own row.
-- session_number: denormalized from sessions.session_number_in_cycle
--                 for fast "show me all notes from session 3" queries.
-- session_phase restricted to doctor-facing phases only.
-- ------------------------------------------------------------
CREATE TABLE doctor_session_notes (
    note_id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id             UUID        NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    cycle_id               UUID        NOT NULL REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    patient_id             UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    doctor_id              UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    -- denormalized: sessions.session_number_in_cycle — avoids JOIN on every notes query
    session_number         INTEGER     NOT NULL CHECK (session_number >= 1),
    session_phase          TEXT        NOT NULL CHECK (session_phase IN (
                               'doctor_consultation',       -- S2: initial assessment
                               'doctor_additional_review'   -- S4: post additional tests
                           )),
    -- structured fields for neurological clinical notes
    chief_complaint        TEXT,        -- patient's presenting complaint (in doctor's words)
    clinical_observations  TEXT,        -- exam findings, neurological observations
    assessment             TEXT,        -- clinical impression / diagnosis notes
    treatment_plan_notes   TEXT,        -- reasoning behind prescribed treatment (narrative)
    follow_up_instructions TEXT,        -- instructions given to patient
    referrals              TEXT,        -- any external referrals made
    note_content           TEXT,        -- free-form catch-all (anything not captured above)
    is_confidential        BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- one note entry per doctor per session phase
    -- (doctor may write notes in both doctor_consultation and doctor_additional_review)
    UNIQUE (session_id, doctor_id, session_phase)
);


-- ------------------------------------------------------------
-- treatment_sessions — individual treatment session records
-- billing_type: standard (1-5) | extended (6+, always billed)
-- Extended sessions cannot start until payment_status = paid/waived
-- ------------------------------------------------------------
CREATE TABLE treatment_sessions (
    ts_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id          UUID NOT NULL REFERENCES treatment_plans(plan_id) ON DELETE RESTRICT,
    session_id       UUID NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    patient_id       UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    ca_id            UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    session_number   INTEGER NOT NULL CHECK (session_number >= 1),
    billing_type     TEXT NOT NULL CHECK (billing_type IN ('standard', 'extended')),
    status           TEXT NOT NULL DEFAULT 'scheduled'
                         CHECK (status IN ('scheduled', 'in_progress', 'completed', 'missed')),
    payment_status   TEXT NOT NULL DEFAULT 'pending'
                         CHECK (payment_status IN (
                             'not_required', 'pending', 'paid', 'waived'
                         )),
    session_notes    TEXT,
    patient_feedback TEXT,
    started_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- SOURCE: 06b_appointment_tables.sql

-- ============================================================
-- Anava Clinic — DB Schema
-- File 06b: Appointment Scheduling Tables
-- doctor_weekly_schedules, doctor_schedule_overrides,
-- appointment_requests, appointments, appointment_audit_logs
--
-- Dependency: 06_clinical_tables.sql must run first
-- (appointment_requests.cycle_id → treatment_cycles)
-- (appointments.session_id     → sessions)
-- (appointments.cycle_id       → treatment_cycles)
--
-- Circular dependency resolved via DEFERRABLE FKs:
--   appointment_requests.parent_appointment_id   → appointments
--   appointment_requests.approved_appointment_id → appointments
-- These are added via ALTER TABLE after appointments is created.
-- ============================================================

-- ------------------------------------------------------------
-- doctor_weekly_schedules — recurring weekly availability
-- One row per doctor per clinic per day_of_week.
-- day_of_week: 0=Sunday, 1=Monday ... 6=Saturday  (matches v6)
-- break_start/end: optional mid-session break (lunch/prayer)
-- effective_from/until NULL = always active
-- UNIQUE(doctor_id, clinic_id, day_of_week): one slot rule per day
-- ------------------------------------------------------------
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

-- ------------------------------------------------------------
-- doctor_schedule_overrides — single-date exceptions
-- is_available=FALSE: entire date blocked (leave, holiday, OOO)
-- is_available=TRUE:  special availability with different times
-- start_time/end_time required when is_available=TRUE (chk enforces)
-- UNIQUE(doctor_id, override_date): one override per doctor per date
-- ------------------------------------------------------------
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

-- ------------------------------------------------------------
-- appointment_requests — patient-initiated booking requests
-- request_type:
--   'new'           = first-time visit request
--   'reschedule'    = patient wants to move an existing appointment
--   'followup_cycle'= patient requesting a follow-up treatment cycle
-- parent_appointment_id: FK added after appointments table created
-- approved_appointment_id: FK added after appointments table created
-- ------------------------------------------------------------
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

-- ------------------------------------------------------------
-- appointments — the actual scheduled time slot
-- Scheduling layer: date, time, slot, booking status.
-- Clinical layer (phase outcome) lives in sessions table.
-- session_id NULL until clinical session is created for this slot.
-- cycle_id NULL for pre-block initial appointments.
-- appointment_type maps to Anava clinical flow phases:
--   'initial_assessment'   = S1+S2 first visit
--   'doctor_consultation'  = S2 only
--   'ca_session'           = S1 / S3 CA-only
--   'treatment_session'    = treatment block sessions
--   'follow_up'            = T+X follow-up visit
--   'demo_visit'           = demo phase home visit
--   'teleconsult'          = remote consultation
-- rescheduled_from: original appointment (self-ref)
-- rescheduled_to:   replacement appointment (set on old row)
-- booked_by_role:   denormalized for fast queries without profile join
-- ------------------------------------------------------------
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

-- Deferred circular FKs: appointment_requests ↔ appointments
-- Same pattern as prs_assessment_instances ↔ prs_final_results.
-- DEFERRABLE INITIALLY DEFERRED: FK check runs at transaction commit,
-- not statement time — allows inserting both sides in one transaction.
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

-- ------------------------------------------------------------
-- appointment_audit_logs — immutable appointment status/date log
-- Written by application layer on every appointment mutation.
-- Covers: status transitions, reschedules (date/time change),
--         cancellations, no-shows.
-- Append-only — never updated or deleted.
-- changed_by ON DELETE SET NULL: log survives profile deactivation.
-- ------------------------------------------------------------
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

-- SOURCE: 07_prs_tables.sql

-- ============================================================
-- Anava Clinic — DB Schema
-- File 07: PRS Tables
--
-- BASE RULE: Keep v6 structure exactly — application code and
-- seed scripts (seed_scales.py from PRS_DET.xlsx) depend on it.
--
-- CHANGES FROM v6:
--   prs_scales            + ADD applicable_for (new Anava field)
--   prs_assessment_instances:
--                         + ADD assessment_stage  (new Anava field)
--                         + ADD cycle_id          (new Anava field)
--                         + ADD administered_by   (new Anava field)
--                         ~ visit_id renamed → session_id
--                           FK updated: sessions(session_id) not sessions(id)
--                         ~ patient_id FK → profiles(id) not patients(id)
--                           (Anava universal table is profiles, not patients)
--   patient_scale_assignments — BRAND NEW table (Anava only)
--
-- DROPPED vs v6:
--   assessment_permissions — replaced by assessment_protocol_requests
--                            (in 06_clinical_tables.sql)
--
-- SEED DATA (questions, options):
--   Run backend/scripts/seed_scales.py after schema is applied.
--   Diseases + scales + disease_scale_map: see 16_seed_data.sql.
-- ============================================================


-- ============================================================
-- ENUMS (v6, kept as-is)
-- ============================================================

DO $$ BEGIN
    CREATE TYPE assessment_taken_by AS ENUM ('patient', 'doctor_on_behalf');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- ============================================================
-- prs_diseases (v6, TEXT PK, placed in 02_core_tables.sql)
-- Referenced here for documentation only.
-- ============================================================
-- Already created in 02_core_tables.sql


-- ============================================================
-- prs_scales (v6 + Anava: ADD applicable_for)
-- TEXT PK: e.g. 'EQ-5D-5L/2026'
-- applicable_for: which PRS stage this scale belongs to
-- ============================================================
CREATE TABLE prs_scales (
    scale_id          TEXT PRIMARY KEY,
    scale_code        TEXT NOT NULL UNIQUE,
    scale_name        TEXT NOT NULL,
    is_common_scale   BOOLEAN NOT NULL DEFAULT FALSE,
    num_diseases_used INTEGER NOT NULL DEFAULT 1,
    -- Anava addition: which stage administers this scale
    applicable_for    TEXT NOT NULL DEFAULT 'main_clinical'
                          CHECK (applicable_for IN (
                              'general_registration',
                              'main_clinical',
                              'followup',
                              'all'
                          )),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- prs_disease_scale_map (v6, unchanged)
-- Ordered mapping: which scales belong to each disease
-- TEXT PK: e.g. 'Depression/Anxiety/EQ-5D-5L'
-- ============================================================
CREATE TABLE prs_disease_scale_map (
    ds_map_id     TEXT PRIMARY KEY,
    disease_id    TEXT NOT NULL REFERENCES prs_diseases(disease_id) ON DELETE CASCADE,
    scale_id      TEXT NOT NULL REFERENCES prs_scales(scale_id) ON DELETE CASCADE,
    display_order INTEGER NOT NULL DEFAULT 0,
    is_required   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (disease_id, scale_id)
);


-- ============================================================
-- prs_questions (v6, unchanged)
-- TEXT PK: e.g. 'PDSS/004'
-- answer_type extended with 'table' for complex grid questions
-- ============================================================
CREATE TABLE prs_questions (
    question_id     TEXT PRIMARY KEY,
    question_code   TEXT NOT NULL UNIQUE,
    disease_id      TEXT REFERENCES prs_diseases(disease_id) ON DELETE SET NULL,
    scale_id        TEXT REFERENCES prs_scales(scale_id) ON DELETE SET NULL,
    ds_map_id       TEXT REFERENCES prs_disease_scale_map(ds_map_id) ON DELETE SET NULL,
    question_text   TEXT NOT NULL,
    answer_type     TEXT NOT NULL CHECK (answer_type IN (
                        'likert', 'radio', 'slider', 'checkbox',
                        'text', 'number', 'table'
                    )),
    min_value       NUMERIC,
    max_value       NUMERIC,
    is_required     BOOLEAN NOT NULL DEFAULT TRUE,
    skip_logic      TEXT,
    display_order   INTEGER NOT NULL DEFAULT 0,
    is_common_scale BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- prs_options (v6, unchanged)
-- Separate table for answer choices per question
-- TEXT PK: e.g. 'PDSS/004/03'
-- points: score contribution per answer choice
-- ============================================================
CREATE TABLE prs_options (
    option_id     TEXT PRIMARY KEY,
    question_id   TEXT NOT NULL REFERENCES prs_questions(question_id) ON DELETE CASCADE,
    option_label  TEXT NOT NULL,
    option_value  TEXT NOT NULL,
    points        NUMERIC NOT NULL DEFAULT 0,
    display_order INTEGER NOT NULL DEFAULT 0,
    status        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (question_id, option_value)
);


-- ============================================================
-- prs_scale_question_map (v6, unchanged)
-- Ordered list of questions within each scale
-- TEXT PK: e.g. 'PDSS/2026/PDSS/004'
-- ============================================================
CREATE TABLE prs_scale_question_map (
    sq_map_id     TEXT PRIMARY KEY,
    scale_id      TEXT NOT NULL REFERENCES prs_scales(scale_id) ON DELETE CASCADE,
    question_id   TEXT NOT NULL REFERENCES prs_questions(question_id) ON DELETE CASCADE,
    display_order INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (scale_id, question_id)
);


-- ============================================================
-- prs_disease_question_map (v6, unchanged)
-- Flat denormalised map: every question reachable from a disease
-- Used by scoring engine for disease-level aggregation
-- TEXT PK: e.g. 'CHRONICPAIN/2026/DASS-21/006'
-- ============================================================
CREATE TABLE prs_disease_question_map (
    dq_map_id     TEXT PRIMARY KEY,
    disease_id    TEXT NOT NULL REFERENCES prs_diseases(disease_id) ON DELETE CASCADE,
    question_id   TEXT NOT NULL REFERENCES prs_questions(question_id) ON DELETE CASCADE,
    display_order INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (disease_id, question_id)
);


-- ============================================================
-- prs_assessment_instances (v6 + Anava additions)
-- TEXT PK: e.g. 'PAT001/001'
--
-- Anava changes:
--   patient_id FK → profiles(id)  [was patients(id) in Supabase]
--   visit_id   → renamed session_id, FK → sessions(session_id)
--   + assessment_stage TEXT (general_registration/main_clinical/followup)
--   + cycle_id  UUID → treatment_cycles(cycle_id)  [NULL at registration]
--   + administered_by UUID → profiles(id)  [CA or Receptionist]
-- ============================================================
CREATE TABLE prs_assessment_instances (
    instance_id      TEXT PRIMARY KEY,
    disease_id       TEXT NOT NULL REFERENCES prs_diseases(disease_id) ON DELETE CASCADE,
    patient_id       UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    -- renamed from visit_id; NULL for general_registration (no session yet)
    session_id       UUID REFERENCES sessions(session_id) ON DELETE SET NULL,
    -- Anava: NULL for general_registration (no block yet)
    cycle_id         UUID REFERENCES treatment_cycles(cycle_id) ON DELETE SET NULL,
    initiated_by     assessment_taken_by NOT NULL DEFAULT 'patient',
    -- Anava: CA, Receptionist, or System who administered this instance
    administered_by  UUID REFERENCES profiles(id) ON DELETE SET NULL,
    -- Anava: which clinical stage this assessment belongs to.
    -- Default 'general_registration': safest default; app sets explicitly for other stages.
    assessment_stage TEXT NOT NULL DEFAULT 'general_registration'
                         CHECK (assessment_stage IN (
                             'general_registration',
                             'main_clinical',
                             'followup'
                         )),
    status           TEXT NOT NULL DEFAULT 'in_progress'
                         CHECK (status IN ('in_progress', 'completed', 'abandoned')),
    started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ,
    final_result     TEXT,  -- FK set later (deferred): prs_final_results.final_result_id
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- prs_responses (v6, unchanged)
-- One row per question per assessment instance
-- TEXT PK: e.g. 'PAT001/001/0006'
-- given_response: raw text answer
-- response_value: numeric score extracted from answer
-- UNIQUE(instance_id, question_id): one answer per question
-- ============================================================
CREATE TABLE prs_responses (
    response_id    TEXT PRIMARY KEY,
    instance_id    TEXT NOT NULL REFERENCES prs_assessment_instances(instance_id) ON DELETE CASCADE,
    question_id    TEXT NOT NULL REFERENCES prs_questions(question_id) ON DELETE CASCADE,
    given_response TEXT,
    response_value NUMERIC,
    time_stamp     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (instance_id, question_id)
);


-- ============================================================
-- prs_scale_results (v6, unchanged)
-- Computed score per scale within an assessment instance
-- percentage is GENERATED ALWAYS AS (calculated_value / max_possible * 100)
-- subscale_scores JSONB: breakdown by subscale where applicable
-- risk_flags JSONB: array of clinical risk indicators
-- TEXT PK: e.g. 'PAT001/001/EQ-5D-5L/2026'
-- ============================================================
CREATE TABLE prs_scale_results (
    scale_result_id  TEXT PRIMARY KEY,
    instance_id      TEXT NOT NULL REFERENCES prs_assessment_instances(instance_id) ON DELETE CASCADE,
    scale_id         TEXT NOT NULL REFERENCES prs_scales(scale_id) ON DELETE CASCADE,
    calculated_value NUMERIC,
    max_possible     NUMERIC,
    percentage       NUMERIC GENERATED ALWAYS AS (
                         CASE WHEN max_possible > 0
                              THEN ROUND((calculated_value / max_possible) * 100, 2)
                              ELSE NULL
                         END
                     ) STORED,
    severity_level   TEXT,
    severity_label   TEXT,
    subscale_scores  JSONB NOT NULL DEFAULT '{}',
    risk_flags       JSONB NOT NULL DEFAULT '[]',
    raw_score_data   JSONB NOT NULL DEFAULT '{}',
    time_stamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (instance_id, scale_id)
);


-- ============================================================
-- prs_final_results (v6, unchanged)
-- Aggregated score across ALL scales in one assessment instance
-- Upserted by trigger recalculate_final_result (see below)
-- TEXT PK: e.g. 'PAT001/001/CHRONICPAIN/2026'
-- ============================================================
CREATE TABLE prs_final_results (
    final_result_id        TEXT PRIMARY KEY,
    instance_id            TEXT NOT NULL UNIQUE
                               REFERENCES prs_assessment_instances(instance_id) ON DELETE CASCADE,
    calculated_value       NUMERIC,
    max_possible           NUMERIC,
    percentage             NUMERIC GENERATED ALWAYS AS (
                               CASE WHEN max_possible > 0
                                    THEN ROUND((calculated_value / max_possible) * 100, 2)
                                    ELSE NULL
                               END
                           ) STORED,
    scales_completed       INTEGER NOT NULL DEFAULT 0,
    scales_total           INTEGER NOT NULL DEFAULT 0,
    overall_severity       TEXT,
    overall_severity_label TEXT,
    scale_summaries        JSONB NOT NULL DEFAULT '[]',
    all_risk_flags         JSONB NOT NULL DEFAULT '[]',
    composite_summary      TEXT,
    time_stamp             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Deferred FK: instances ↔ final_results (circular dependency)
-- final_result on instances is set AFTER final_results row exists
ALTER TABLE prs_assessment_instances
    ADD CONSTRAINT fk_instance_final_result
    FOREIGN KEY (final_result)
    REFERENCES prs_final_results(final_result_id)
    ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED;


-- ============================================================
-- patient_scale_assignments (NEW — Anava only)
-- Tracks which specific PRS scales are assigned to each patient
-- Enables consistent longitudinal comparison across follow-ups
-- scale_id is TEXT FK (v6 TEXT PK on prs_scales)
-- ============================================================
CREATE TABLE patient_scale_assignments (
    psa_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id        UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    scale_id          TEXT NOT NULL REFERENCES prs_scales(scale_id) ON DELETE RESTRICT,
    assessment_stage  TEXT NOT NULL CHECK (assessment_stage IN (
                          'general_registration', 'main_clinical', 'followup'
                      )),
    assigned_by       UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    assignment_reason TEXT CHECK (assignment_reason IN (
                          'auto_disease_match', 'ca_selected', 'doctor_override'
                      )),
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    deactivated_at    TIMESTAMPTZ,
    deactivated_by    UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- TRIGGER: recalculate_final_result (v6, unchanged)
-- Fires AFTER INSERT OR UPDATE on prs_scale_results.
-- Aggregates all scale results for an instance into prs_final_results.
-- Marks instance as 'completed' when all scales are done.
-- ============================================================

CREATE OR REPLACE FUNCTION recalculate_final_result()
RETURNS TRIGGER AS $$
DECLARE
    v_instance      prs_assessment_instances%ROWTYPE;
    v_total         NUMERIC := 0;
    v_max           NUMERIC := 0;
    v_completed     INTEGER := 0;
    v_total_scales  INTEGER := 0;
    v_worst_sev     TEXT    := NULL;
    v_worst_label   TEXT    := NULL;
    v_summaries     JSONB   := '[]'::JSONB;
    v_all_flags     JSONB   := '[]'::JSONB;
    sev_order       INTEGER;
    worst_order     INTEGER := -1;
    r               RECORD;
BEGIN
    SELECT * INTO v_instance
    FROM prs_assessment_instances
    WHERE instance_id = NEW.instance_id;

    -- Count total scales for this disease
    SELECT COUNT(*) INTO v_total_scales
    FROM prs_disease_scale_map
    WHERE disease_id = v_instance.disease_id;

    -- Aggregate all scale results for this instance
    FOR r IN
        SELECT sr.*, sc.scale_code, sc.scale_name
        FROM prs_scale_results sr
        JOIN prs_scales sc ON sc.scale_id = sr.scale_id
        WHERE sr.instance_id = NEW.instance_id
    LOOP
        v_total     := v_total + COALESCE(r.calculated_value, 0);
        v_max       := v_max   + COALESCE(r.max_possible, 0);
        v_completed := v_completed + 1;

        -- Track worst severity across all scales
        sev_order := CASE r.severity_level
            WHEN 'severe'            THEN 4
            WHEN 'moderately-severe' THEN 3
            WHEN 'moderate'          THEN 2
            WHEN 'mild'              THEN 1
            ELSE 0
        END;
        IF sev_order > worst_order THEN
            worst_order   := sev_order;
            v_worst_sev   := r.severity_level;
            v_worst_label := r.severity_label;
        END IF;

        -- Build per-scale summary snapshot
        v_summaries := v_summaries || jsonb_build_object(
            'scale_code',     r.scale_code,
            'scale_name',     r.scale_name,
            'score',          r.calculated_value,
            'max_possible',   r.max_possible,
            'percentage',     CASE WHEN r.max_possible > 0
                                   THEN ROUND((r.calculated_value / r.max_possible) * 100, 2)
                                   ELSE NULL END,
            'severity_level', r.severity_level,
            'severity_label', r.severity_label
        );

        -- Collect risk flags from all scales
        IF r.risk_flags IS NOT NULL AND jsonb_array_length(r.risk_flags) > 0 THEN
            v_all_flags := v_all_flags || r.risk_flags;
        END IF;
    END LOOP;

    -- Upsert prs_final_results
    INSERT INTO prs_final_results (
        final_result_id,
        instance_id,
        calculated_value,
        max_possible,
        scales_completed,
        scales_total,
        overall_severity,
        overall_severity_label,
        scale_summaries,
        all_risk_flags,
        time_stamp
    ) VALUES (
        NEW.instance_id || '/' || v_instance.disease_id,
        NEW.instance_id,
        v_total,
        v_max,
        v_completed,
        v_total_scales,
        v_worst_sev,
        v_worst_label,
        v_summaries,
        v_all_flags,
        NOW()
    )
    ON CONFLICT (instance_id) DO UPDATE SET
        calculated_value        = EXCLUDED.calculated_value,
        max_possible            = EXCLUDED.max_possible,
        scales_completed        = EXCLUDED.scales_completed,
        scales_total            = EXCLUDED.scales_total,
        overall_severity        = EXCLUDED.overall_severity,
        overall_severity_label  = EXCLUDED.overall_severity_label,
        scale_summaries         = EXCLUDED.scale_summaries,
        all_risk_flags          = EXCLUDED.all_risk_flags,
        time_stamp              = EXCLUDED.time_stamp;

    -- Mark instance completed when all scales are scored
    IF v_completed >= v_total_scales THEN
        UPDATE prs_assessment_instances
        SET
            status       = 'completed',
            completed_at = NOW(),
            final_result = (
                SELECT final_result_id
                FROM prs_final_results
                WHERE instance_id = NEW.instance_id
            )
        WHERE instance_id = NEW.instance_id
          AND status != 'completed';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- DEFERRABLE INITIALLY DEFERRED: trigger fires at end-of-transaction, not per statement.
-- When the application submits all scale results for an instance in one transaction,
-- all N trigger calls fire at commit time. Each call is idempotent (ON CONFLICT DO UPDATE),
-- so the final aggregate is correct regardless of firing order.
-- Performance: O(N²) work but confined to commit phase; concurrent transactions unblocked.
CREATE CONSTRAINT TRIGGER trg_recalculate_final_result
    AFTER INSERT OR UPDATE ON prs_scale_results
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION recalculate_final_result();

-- SOURCE: 08_anamnesis_tables.sql

-- ============================================================
-- Anava Clinic — DB Schema
-- File 08: Anamnesis Tables
--
-- BASE RULE: Keep v6 4-table structure exactly.
-- Application code depends on this structure.
--
-- CHANGES FROM v6:
--   anamnesis_assessments:
--     + ADD cycle_id UUID  (NULL at registration, set on block-level updates)
--     + ADD version INTEGER (increments on each update to this patient's anamnesis)
--     ~ patient_id FK → profiles(id)  [was profiles(id) in v6 too — no change]
--     ~ removed UNIQUE(patient_id) — Anava allows multiple versions per patient
--       (one per block update); uniqueness enforced at application layer
--   anamnesis_questions, anamnesis_options, anamnesis_responses — UNCHANGED
--
-- SEED DATA (questions + options):
--   Questions seeded separately via admin portal or seed script.
--   anamnesis_questions contains all form questions (section-based).
-- ============================================================


-- ============================================================
-- TABLE 1: anamnesis_assessments
-- Header record — one per assessment event (registration + each update)
-- v6: TEXT PK "ANA/{patient_id[:8]}/NNN"
-- Anava adds: cycle_id, version
-- ============================================================
CREATE TABLE anamnesis_assessments (
    anamnesis_id TEXT PRIMARY KEY,
    -- composite format: "ANA/{patient_id_first8}/{version_padded3}"
    -- e.g. "ANA/a1b2c3d4/001" at registration, "ANA/a1b2c3d4/002" on first update
    patient_id   UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    submitted_by UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    -- assessment_taken_by ENUM defined in 07_prs_tables.sql
    taken_by     assessment_taken_by NOT NULL DEFAULT 'patient',
    -- NULL at registration (no block exists yet)
    cycle_id     UUID REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    -- 1 at registration, increments on each update
    version      INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    status       TEXT NOT NULL DEFAULT 'in_progress'
                     CHECK (status IN ('in_progress', 'completed')),
    completed_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Prevent duplicate versions for same patient
    UNIQUE (patient_id, version)
);


-- ============================================================
-- TABLE 2: anamnesis_questions
-- Seed/reference table — defines every question in the form.
-- Read-only for all clinical users. Admin/Super Admin write.
-- TEXT PK: "ANA/S02/Q003"  (section/question composite)
-- depends_on_question_id: conditional display logic
-- ============================================================
CREATE TABLE anamnesis_questions (
    question_id            TEXT PRIMARY KEY,
    section_number         INTEGER NOT NULL,
    section_title          TEXT NOT NULL,
    question_code          TEXT NOT NULL UNIQUE,  -- snake_case, used by API
    question_text          TEXT NOT NULL,
    answer_type            TEXT NOT NULL CHECK (answer_type IN (
                               'text', 'textarea', 'radio',
                               'select', 'checkbox', 'conditional_text'
                           )),
    is_required            BOOLEAN NOT NULL DEFAULT TRUE,
    display_order          INTEGER NOT NULL DEFAULT 0,
    depends_on_question_id TEXT REFERENCES anamnesis_questions(question_id),
    depends_on_value       TEXT,
    helper_text            TEXT,
    status                 BOOLEAN NOT NULL DEFAULT TRUE
);


-- ============================================================
-- TABLE 3: anamnesis_options
-- One row per selectable choice for radio/select/checkbox questions.
-- text/textarea/conditional_text questions have no rows here.
-- TEXT PK: "ANA/S02/Q003/O01"
-- ============================================================
CREATE TABLE anamnesis_options (
    option_id     TEXT PRIMARY KEY,
    question_id   TEXT NOT NULL REFERENCES anamnesis_questions(question_id) ON DELETE CASCADE,
    option_label  TEXT NOT NULL,
    option_value  TEXT NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE (question_id, option_value)
);


-- ============================================================
-- TABLE 4: anamnesis_responses
-- One row per question per assessment instance.
-- Upserted as patient fills form; locked after status=completed.
-- TEXT PK: "{anamnesis_id}|{question_id}"
-- response_value:  text/radio/select/conditional_text answers
-- response_values: TEXT[] for checkbox multi-select answers
-- ============================================================
CREATE TABLE anamnesis_responses (
    response_id     TEXT PRIMARY KEY,
    -- composite: "{anamnesis_id}|{question_id}"
    -- e.g. "ANA/a1b2c3d4/001|ANA/S02/Q007"
    anamnesis_id    TEXT NOT NULL REFERENCES anamnesis_assessments(anamnesis_id) ON DELETE CASCADE,
    question_id     TEXT NOT NULL REFERENCES anamnesis_questions(question_id),
    response_value  TEXT,
    response_values TEXT[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    -- Note: TEXT PK "{anamnesis_id}|{question_id}" already enforces uniqueness.
    -- A separate UNIQUE (anamnesis_id, question_id) would create a redundant second index.
);

-- SOURCE: 08b_patient_files.sql

-- ============================================================
-- Anava Clinic — DB Schema
-- File 08b: Patient File Storage Tables
--
-- Two separate tables for clean retrieval and dedicated queries:
--   patient_eeg_files          — EEG recordings + reports
--   patient_medical_history_files — documents brought from outside
--
-- S3 key is authoritative pointer in both tables.
-- Presigned URL generated on demand; files never move.
-- ============================================================


-- ============================================================
-- TABLE 1: patient_eeg_files
--
-- One row per EEG recording event.
-- performed_by: CA who operated the EEG machine
-- reviewed_by:  Doctor who interpreted findings (set later)
-- raw_data_s3_key: machine output (.edf / .eeg) — nullable if clinic
--                  only stores PDF report
-- report_s3_key:   generated PDF report — set after review
-- status drives workflow: raw_uploaded → report_pending → report_ready → reviewed
-- ============================================================
CREATE TABLE patient_eeg_files (
    eeg_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id       UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id        UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    cycle_id         UUID        REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    session_id       UUID        REFERENCES sessions(session_id) ON DELETE RESTRICT,
    -- CA who ran the EEG machine during the session
    performed_by     UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    -- Doctor who reviewed the recording and wrote findings (set after review)
    reviewed_by      UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    eeg_type         TEXT        NOT NULL DEFAULT 'resting_state'
                                     CHECK (eeg_type IN (
                                         'resting_state',
                                         'sleep_study',
                                         'ambulatory',
                                         'evoked_potential',
                                         'other'
                                     )),
    duration_minutes INTEGER,
    -- Raw machine output (.edf, .eeg) — NULL if clinic only uploads report
    raw_data_s3_key       TEXT        UNIQUE,
    raw_file_name         TEXT,
    raw_file_size         BIGINT,
    raw_checksum          TEXT,
    raw_checksum_algorithm TEXT       NOT NULL DEFAULT 'sha256',
    -- PDF report generated after doctor review
    report_s3_key         TEXT        UNIQUE,
    report_file_name      TEXT,
    report_file_size      BIGINT,
    report_checksum       TEXT,
    report_checksum_algorithm TEXT    NOT NULL DEFAULT 'sha256',
    -- Versioning: if findings are revised, superseded_by points to the correction.
    -- NULL = this is the current version.
    superseded_by         UUID        REFERENCES patient_eeg_files(eeg_id) ON DELETE RESTRICT,
    -- CA notes during recording session
    recording_notes  TEXT,
    -- Doctor's clinical interpretation after reviewing the EEG
    clinical_findings TEXT,
    -- NULL = not yet reviewed; TRUE/FALSE set by reviewing doctor
    is_abnormal      BOOLEAN,
    status           TEXT        NOT NULL DEFAULT 'raw_uploaded'
                                     CHECK (status IN (
                                         'raw_uploaded',     -- EEG data uploaded, awaiting report
                                         'report_pending',   -- CA flagged for doctor review
                                         'report_ready',     -- Doctor has written findings
                                         'reviewed'          -- Finalized, signed off
                                     )),
    performed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- TABLE 2: patient_medical_history_files
--
-- Documents the patient brings from outside (prior history).
-- NOT for documents generated at Anava.
-- Consent PDFs → consent_records.pdf_s3_key
-- Clinic-generated prescriptions → future patient_prescriptions table
--
-- document_date: date printed on the document (not upload date)
-- source_provider: hospital/clinic/doctor the document came from
-- ============================================================
CREATE TABLE patient_medical_history_files (
    mhf_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id      UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id       UUID        NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    -- NULL at self-registration (patient uploads from portal before any block)
    cycle_id        UUID        REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    -- patient themselves or staff who did upload on their behalf
    uploaded_by     UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    document_type   TEXT        NOT NULL CHECK (document_type IN (
                                    'past_prescription',     -- medications from prior doctors
                                    'lab_report',            -- blood work, pathology
                                    'imaging_report',        -- MRI, CT, X-ray, scan
                                    'hospital_discharge',    -- discharge summary
                                    'referral_letter',       -- referral from another doctor
                                    'vaccination_record',
                                    'insurance_document',
                                    'previous_assessment',   -- prior neuro/psych assessments
                                    'doctor_notes',          -- notes from past consultations
                                    'other'
                                )),
    s3_key              TEXT        NOT NULL UNIQUE,
    file_name           TEXT        NOT NULL,
    file_size           BIGINT,
    mime_type           TEXT,
    checksum            TEXT,
    checksum_algorithm  TEXT        NOT NULL DEFAULT 'sha256',
    description     TEXT,
    -- Date printed on the document itself (e.g. prescription date, test date)
    document_date   DATE,
    -- Hospital / clinic / doctor the document originated from
    source_provider TEXT,
    is_deleted      BOOLEAN     NOT NULL DEFAULT FALSE,
    deleted_by      UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    deleted_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- SOURCE: 09_consent_tables.sql

-- ============================================================
-- Anava Clinic — DB Schema
-- File 09: Consent Tables
-- consent_templates, consent_records, patient_clinic_transfers
-- RULE: Consent records NEVER deleted. Status-changed only.
-- ============================================================

-- ------------------------------------------------------------
-- consent_templates — master consent content per type per version
-- ------------------------------------------------------------
CREATE TABLE consent_templates (
    template_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    consent_type TEXT NOT NULL CHECK (consent_type IN (
                     'patient_onboarding',
                     'patient_clinic_exit',
                     'patient_clinic_transfer',
                     'patient_relocation_transfer',
                     'staff_onboarding',
                     'staff_offboarding',
                     'clinic_join_anava',
                     'clinic_leave_anava'
                 )),
    version        INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    title          TEXT NOT NULL,
    content        TEXT NOT NULL,
    -- SHA-256 hex of content. Proof of exact wording at time of consent signing.
    -- Application copies content_hash into consent_records.content_hash_at_signing.
    content_hash   TEXT GENERATED ALWAYS AS (
                       encode(sha256(content::bytea), 'hex')
                   ) STORED,
    effective_date DATE,
    expiry_date    DATE,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (consent_type, version)
);

-- ------------------------------------------------------------
-- consent_records — every consent event. Append-only. Never deleted.
-- patient_id or staff_id set depending on consent type.
-- witness_id set only for patient_onboarding (Receptionist witnesses).
-- pdf_s3_key points to the signed PDF stored in S3.
-- ------------------------------------------------------------
CREATE TABLE consent_records (
    consent_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    consent_type    TEXT NOT NULL CHECK (consent_type IN (
                        'patient_onboarding',
                        'patient_clinic_exit',
                        'patient_clinic_transfer',
                        'patient_relocation_transfer',
                        'staff_onboarding',
                        'staff_offboarding',
                        'clinic_join_anava',
                        'clinic_leave_anava'
                    )),
    template_id     UUID NOT NULL REFERENCES consent_templates(template_id) ON DELETE RESTRICT,
    patient_id      UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    staff_id        UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id       UUID NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'signed', 'revoked')),
    signed_at       TIMESTAMPTZ,
    signed_by       UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    witness_id      UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    ip_address      INET,
    signature_data  TEXT,
    pdf_s3_key      TEXT,
    -- Snapshot of consent_templates.content_hash at signing time.
    -- Proves what exact wording the signer consented to, even if template is later updated.
    content_hash_at_signing TEXT,
    revoked_at      TIMESTAMPTZ,
    revoked_by      UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_consent_signer CHECK (
        patient_id IS NOT NULL OR staff_id IS NOT NULL
    )
);

-- ------------------------------------------------------------
-- patient_clinic_transfers — clinic closure transfers + relocations
-- Also records when patient DECLINES transfer (status=declined)
-- active_cycle_id: if patient has live block, it carries over
-- consent_id links to the signed consent record
-- ------------------------------------------------------------
CREATE TABLE patient_clinic_transfers (
    pct_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    from_clinic_id  UUID NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    to_clinic_id    UUID NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    from_doctor_id  UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    to_doctor_id    UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    transfer_reason TEXT NOT NULL DEFAULT 'clinic_closure'
                        CHECK (transfer_reason IN (
                            'clinic_closure',
                            'patient_relocation',
                            'patient_request',
                            'doctor_transfer'
                        )),
    active_cycle_id UUID REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN (
                            'pending', 'consented', 'completed', 'declined'
                        )),
    consent_id      UUID REFERENCES consent_records(consent_id) ON DELETE RESTRICT,
    initiated_by    UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- SOURCE: 10_store_tables.sql

-- ============================================================
-- Anava Clinic — DB Schema
-- File 10: Store Tables
-- products, store_orders, order_items, inventory,
-- stock_transfers, device_assignments
--
-- Order matters: device_assignments references store_orders,
-- so store_orders must be created first.
-- ============================================================

-- ------------------------------------------------------------
-- products — store catalog (devices and accessories)
-- ------------------------------------------------------------
CREATE TABLE products (
    product_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    description TEXT,
    category    TEXT NOT NULL CHECK (category IN ('device', 'accessory')),
    price       NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
    sku         TEXT UNIQUE,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------
-- store_orders — patient purchase orders
-- Device orders require Doctor approval before dispatch.
-- Accessory orders skip to pending_dispatch directly.
-- treatment_plan_id required for device orders (validation).
-- ------------------------------------------------------------
CREATE TABLE store_orders (
    order_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id        UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id         UUID NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    initiated_by      UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    approved_by       UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    order_type        TEXT NOT NULL CHECK (order_type IN ('device', 'accessory')),
    status            TEXT NOT NULL DEFAULT 'pending_doctor_approval'
                          CHECK (status IN (
                              'pending_doctor_approval',
                              'doctor_approved',
                              'pending_dispatch',
                              'dispatched_to_clinic',
                              'received_at_clinic',
                              'collected_by_patient',
                              'cancelled'
                          )),
    total_amount      NUMERIC(10, 2) CHECK (total_amount >= 0),
    treatment_plan_id UUID REFERENCES treatment_plans(plan_id) ON DELETE RESTRICT,
    cancelled_by      UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    cancelled_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------
-- order_items — line items within a store order
-- ------------------------------------------------------------
CREATE TABLE order_items (
    item_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id   UUID NOT NULL REFERENCES store_orders(order_id) ON DELETE RESTRICT,
    product_id UUID NOT NULL REFERENCES products(product_id) ON DELETE RESTRICT,
    quantity   INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 1),
    unit_price NUMERIC(10, 2) NOT NULL CHECK (unit_price >= 0)
);

-- ------------------------------------------------------------
-- inventory — stock levels at each clinic location
-- Main branches hold real stock; individual clinics are transient
-- UNIQUE(product_id, clinic_id) — one row per product per clinic
-- ------------------------------------------------------------
CREATE TABLE inventory (
    inventory_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id   UUID NOT NULL REFERENCES products(product_id) ON DELETE RESTRICT,
    clinic_id    UUID NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    quantity     INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (product_id, clinic_id)
);

-- ------------------------------------------------------------
-- stock_transfers — stock movement log
-- from_type: super_admin (central → main branch) or
--            main_branch (main branch → individual clinic)
-- from_clinic_id NULL when from_type='super_admin'
-- order_id set when transfer is fulfilling a patient order;
--          NULL when it is a replenishment transfer
-- ------------------------------------------------------------
CREATE TABLE stock_transfers (
    st_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id     UUID NOT NULL REFERENCES products(product_id) ON DELETE RESTRICT,
    from_type      TEXT NOT NULL CHECK (from_type IN ('super_admin', 'main_branch')),
    from_clinic_id UUID REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    to_clinic_id   UUID NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    quantity       INTEGER NOT NULL CHECK (quantity >= 1),
    order_id       UUID REFERENCES store_orders(order_id) ON DELETE RESTRICT,
    status         TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'dispatched', 'received')),
    initiated_by   UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    received_by    UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dispatched_at  TIMESTAMPTZ,
    received_at    TIMESTAMPTZ,
    CONSTRAINT chk_stock_transfer_from CHECK (
        (from_type = 'super_admin' AND from_clinic_id IS NULL)
        OR (from_type = 'main_branch' AND from_clinic_id IS NOT NULL)
    )
);

-- ------------------------------------------------------------
-- device_assignments — tracks device purchase per patient
-- Created after all treatment sessions in a block complete
-- purchase_status machine: purchase_prompted → pending_payment
--   → purchased → collected
-- order_id set when Receptionist creates the store_order
-- ------------------------------------------------------------
CREATE TABLE device_assignments (
    da_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id       UUID NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    plan_id         UUID NOT NULL REFERENCES treatment_plans(plan_id) ON DELETE RESTRICT,
    assigned_by     UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    device_type     TEXT NOT NULL,
    purchase_status TEXT NOT NULL DEFAULT 'purchase_prompted'
                        CHECK (purchase_status IN (
                            'purchase_prompted',
                            'pending_payment',
                            'purchased',
                            'collected',
                            'returned'
                        )),
    order_id        UUID REFERENCES store_orders(order_id) ON DELETE RESTRICT,
    prompted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    purchased_at    TIMESTAMPTZ,
    collected_at    TIMESTAMPTZ,
    returned_at     TIMESTAMPTZ,
    returned_by     UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    return_reason   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- SOURCE: 11_payment_tables.sql

-- ============================================================
-- Anava Clinic — DB Schema
-- File 11: Payment Tables
-- Depends on: sessions (06), store_orders (10)
-- ============================================================

-- ------------------------------------------------------------
-- payments — Razorpay payment records
--
-- Exactly ONE of session_id or order_id must be set.
-- razorpay_order_id: created by backend, sent to frontend
-- razorpay_payment_id: filled by Razorpay webhook after success
-- idempotency_key: hash of Razorpay webhook event_id — prevents
--   duplicate processing when Razorpay retries webhook delivery.
--   Format: SHA-256 of (razorpay_event_id || payment_type)
-- gateway_response: raw Razorpay webhook payload for audit/reconciliation
-- waived_by: Clinic Admin only — can waive extended session payments
-- ------------------------------------------------------------
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

-- SOURCE: 12_logging_tables.sql

-- ============================================================
-- Anava Clinic — DB Schema
-- File 12: Logging Tables
-- audit_logs, activity_logs
-- RULE: Both tables are append-only. NEVER updated or deleted.
-- ============================================================

-- ------------------------------------------------------------
-- audit_logs — DB trigger change log
-- Written ONLY by database triggers (fn_audit_trigger).
-- No application INSERT/UPDATE/DELETE ever.
--
-- record_id TEXT (not UUID): supports both UUID PKs (most tables)
-- and TEXT PKs (prs_assessment_instances, anamnesis_assessments).
-- changed_by: UUID from app.current_user_id session setting.
--   No FK — log must survive even if profile is deactivated.
-- ------------------------------------------------------------
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

-- ------------------------------------------------------------
-- activity_logs — application semantic event log
-- Written by application code only (never DB triggers).
-- actor_role denormalized for fast analytics without joins.
-- request_id links all events from a single HTTP request.
-- clinic_id and region_id nullable (some events are system-wide).
-- actor_id ON DELETE RESTRICT: profiles should never be hard-deleted
-- in a healthcare system. Use is_active = FALSE for deactivation.
-- ------------------------------------------------------------
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

-- SOURCE: 12b_notifications.sql

-- ============================================================
-- Anava Clinic — DB Schema
-- File 12b: Notifications Table
--
-- User-facing in-app notifications. NOT an audit log.
-- is_read toggled by recipient; nothing else updated by recipient.
-- sender_id NULL = system-generated notification.
-- clinic_id NULL = system-wide / cross-clinic notification.
-- expires_at: notification auto-hides after this timestamp.
-- delivery_channel: how the notification is sent to user.
-- delivered_at: timestamp when delivery confirmed.
-- delivery_attempts: retry counter for failed deliveries.
--
-- Types:
--   appointment   — scheduled/cancelled/reminder
--   clinical      — PRS result ready, treatment plan issued
--   store         — order status changes, device collection
--   admin         — staff request approval/rejection, clinic status
--   consent       — consent form requires signature
--   system        — maintenance, announcements
-- ============================================================
CREATE TABLE notifications (
    notification_id  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    recipient_id     UUID        NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    sender_id        UUID        REFERENCES profiles(id) ON DELETE SET NULL,
    clinic_id        UUID        REFERENCES clinics(clinic_id) ON DELETE SET NULL,
    type             TEXT        NOT NULL DEFAULT 'system'
                                     CHECK (type IN (
                                         'appointment',
                                         'clinical',
                                         'store',
                                         'admin',
                                         'consent',
                                         'system'
                                     )),
    delivery_channel TEXT        NOT NULL DEFAULT 'in_app'
                                     CHECK (delivery_channel IN (
                                         'in_app', 'email', 'sms', 'push'
                                     )),
    title            TEXT        NOT NULL,
    body             TEXT,
    -- link-back to the relevant record (e.g. session_id, order_id, request_id)
    entity_type      TEXT,
    entity_id        UUID,
    metadata         JSONB       NOT NULL DEFAULT '{}',
    is_read          BOOLEAN     NOT NULL DEFAULT FALSE,
    read_at          TIMESTAMPTZ,
    delivered_at     TIMESTAMPTZ,
    delivery_attempts INTEGER    NOT NULL DEFAULT 0,
    expires_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- SOURCE: 13_indexes.sql

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
CREATE INDEX idx_ca_profile_id            ON clinical_assistants (profile_id);
CREATE INDEX idx_ca_supervising_doctor_id ON clinical_assistants (supervising_doctor_id);

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

-- SOURCE: 14_triggers.sql

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

-- SOURCE: 15_rls_policies.sql

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

CREATE POLICY rls_csa_insert ON clinic_staff_assignments FOR INSERT
WITH CHECK (
    rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin')
);

CREATE POLICY rls_csa_update ON clinic_staff_assignments FOR UPDATE
USING (
    rls_user_role() IN ('super_admin', 'regional_admin', 'clinic_admin')
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

CREATE POLICY rls_tp_insert ON treatment_plans FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'doctor'));

CREATE POLICY rls_tp_update ON treatment_plans FOR UPDATE
USING (rls_user_role() IN ('super_admin', 'doctor'));

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

CREATE POLICY rls_ts_insert ON treatment_sessions FOR INSERT
WITH CHECK (rls_user_role() IN ('super_admin', 'clinic_admin', 'clinical_assistant'));

CREATE POLICY rls_ts_update ON treatment_sessions FOR UPDATE
USING (rls_user_role() IN ('super_admin', 'clinic_admin', 'clinical_assistant'));

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

-- SOURCE: 16_seed_data.sql

-- ============================================================
-- Anava Clinic — DB Schema
-- File 16: Seed Data
-- 1. consent_templates (8 types, version 1)
-- 2. prs_diseases (14 neurological conditions)
-- 3. prs_scales (41 clinical instruments)
-- 4. prs_disease_scale_map
-- 5. anamnesis_questions + anamnesis_options
--
-- Idempotent: safe to re-run; ON CONFLICT clauses prevent duplicates.
-- ============================================================

BEGIN;

-- ============================================================
-- CONSENT TEMPLATES (8 types)
-- ============================================================

INSERT INTO consent_templates (consent_type, version, title, content, is_active) VALUES
(
    'patient_onboarding', 1,
    'Patient Onboarding Consent',
    'I, the undersigned patient, consent to enroll in the Anava Clinic neurological care program. '
    'I understand that my personal and medical information will be collected and used for the purpose '
    'of providing neurological assessment, treatment planning, and follow-up care. I consent to the '
    'collection of EEG data, administration of standardized psychological rating scales (PRS), and '
    'other clinical assessments as determined by the clinical team. I understand that a Receptionist '
    'will witness this consent signing. I understand that my records will be retained permanently as '
    'required by medical record-keeping regulations. I have the right to exit the program at any time.',
    TRUE
),
(
    'patient_clinic_exit', 1,
    'Patient Clinic Exit Consent',
    'I, the undersigned patient, voluntarily consent to discharge from Anava Clinic. I understand '
    'that my clinical records will be archived permanently in read-only status. I acknowledge that '
    'the treatment program may be incomplete and that I am exiting at my own discretion. I understand '
    'that re-joining the clinic in the future will require a new registration process.',
    TRUE
),
(
    'patient_clinic_transfer', 1,
    'Patient Clinic Transfer Consent',
    'I, the undersigned patient, consent to the transfer of my complete clinical records from '
    '[FROM_CLINIC] to [TO_CLINIC]. I understand this transfer is being facilitated due to the '
    'closure of [FROM_CLINIC]. I consent to the allocation of a new Doctor at the receiving clinic. '
    'I understand that my treatment will continue without interruption from where it was paused. '
    'My records at the original clinic will be retained permanently in read-only status.',
    TRUE
),
(
    'patient_relocation_transfer', 1,
    'Patient Relocation Transfer Consent',
    'I, the undersigned patient, consent to the transfer of my complete clinical records from '
    '[FROM_CLINIC] to [TO_CLINIC] due to my permanent relocation to a new region. I understand '
    'that this is not an exit from the Anava care program — my treatment will continue at the '
    'new clinic. I consent to the auto-allocation of a new Doctor at the receiving clinic. '
    'Any active appointment block will resume from its current position without restart. '
    'My records at the original clinic will be retained permanently in read-only status.',
    TRUE
),
(
    'staff_onboarding', 1,
    'Staff Onboarding Consent',
    'I, the undersigned staff member, consent to joining [CLINIC_NAME] as [ROLE]. I acknowledge '
    'receipt of the staff handbook and code of conduct. I understand my responsibilities regarding '
    'patient confidentiality, data protection, and clinical protocols as defined by Anava Clinic '
    'and Mana Health Sciences Group. I consent to the storage of my professional credentials '
    'and employment records in the Anava platform.',
    TRUE
),
(
    'staff_offboarding', 1,
    'Staff Offboarding Consent',
    'I, the undersigned staff member, acknowledge the termination of my association with '
    '[CLINIC_NAME] as [ROLE]. I understand that my access to the Anava platform will be '
    'revoked immediately upon signing this consent. I confirm that I have no outstanding '
    'patient responsibilities and that all pending clinical documentation has been completed. '
    'My employment records will be retained permanently as required by regulation.',
    TRUE
),
(
    'clinic_join_anava', 1,
    'Partner Clinic — Join Anava Network Consent',
    'We, the authorized representatives of [PARTNER_CLINIC_NAME], consent to joining the '
    'Anava Clinic partner network operated by Mana Health Sciences Group. We agree to operate '
    'under the Anava clinical protocols, patient care standards, and data governance framework. '
    'We acknowledge that Regional Admin oversight applies to our clinic operations and staff. '
    'We understand the obligations regarding patient record retention and data security.',
    TRUE
),
(
    'clinic_leave_anava', 1,
    'Partner Clinic — Leave Anava Network Consent',
    'We, the authorized representatives of [PARTNER_CLINIC_NAME], consent to the closure of '
    'our partnership with the Anava Clinic network operated by Mana Health Sciences Group. '
    'We acknowledge that all patient records must be transferred or archived before closure '
    'can be completed. We understand that data retention obligations continue after departure '
    'and that all records will remain in the Anava system in read-only status permanently.',
    TRUE
)
ON CONFLICT (consent_type, version) DO NOTHING;


-- ============================================================
-- PRS DISEASES — 14 conditions (v6 seed data, TEXT composite PKs)
-- disease_id format: 'DISEASENAME/2026'
-- Matches existing application code and seed_scales.py
-- ============================================================

INSERT INTO prs_diseases (disease_id, disease_code, disease_name, version, status) VALUES
('DEPRESSION/ANXIETY/2026',    'DEPRESSIONANXIETY',    'Depression/Anxiety',       'v1.0', TRUE),
('CHRONICPAIN/2026',           'CHRONICPAIN',          'Chronic Pain',             'v1.0', TRUE),
('FIBROMYALGIA/2026',          'FIBROMYALGIA',         'Fibromyalgia',             'v1.0', TRUE),
('MIGRAINE/2026',              'MIGRAINE',             'Migraine',                 'v1.0', TRUE),
('ATAXIA/2026',                'ATAXIA',               'Ataxia',                   'v1.0', TRUE),
('AFTERSTROKE/TBI/2026',       'AFTERSTROKETBI',       'After Stroke/TBI',         'v1.0', TRUE),
('DEMENTIA/2026',              'DEMENTIA',             'Dementia',                 'v1.0', TRUE),
('PARKINSONSDISEASE/2026',     'PARKINSONSDISEASE',    'Parkinson''s Disease',     'v1.0', TRUE),
('TINNITUS/2026',              'TINNITUS',             'Tinnitus',                 'v1.0', TRUE),
('INSOMNIA/2026',              'INSOMNIA',             'Insomnia',                 'v1.0', TRUE),
('MULTIPLESCLEROSIS/2026',     'MULTIPLESCLEROSIS',    'Multiple Sclerosis',       'v1.0', TRUE),
('ADHD/2026',                  'ADHD',                 'ADHD',                     'v1.0', TRUE),
('ALS/2026',                   'ALS',                  'ALS',                      'v1.0', TRUE),
('IRRITABLEBOWELDISEASE/2026', 'IRRITABLEBOWELDISEASE','Irritable Bowel Disease',  'v1.0', TRUE)
ON CONFLICT (disease_id) DO NOTHING;


-- ============================================================
-- PRS SCALES — 41 clinical instruments (v6 seed data)
-- scale_id format: 'SCALECODE/2026'
-- Matches existing application code and seed_scales.py
-- ============================================================

INSERT INTO prs_scales (scale_id, scale_code, scale_name, is_common_scale, num_diseases_used, applicable_for) VALUES
('AIS/2026',         'AIS',         'AIS - Athens Insomnia Scale',                                FALSE, 1,  'main_clinical'),
('ALSFRS-R/2026',    'ALSFRS-R',    'ALSFRS-R - ALS Functional Rating Scale - Revised',           FALSE, 1,  'main_clinical'),
('AMTS/2026',        'AMTS',        'AMTS - Abbreviated Mental Test Score',                       FALSE, 1,  'main_clinical'),
('ASRS-v1.1/2026',   'ASRS-v1.1',   'ASRS-v1.1 - Adult ADHD Self-Report Scale',                  FALSE, 1,  'main_clinical'),
('BDI-II/2026',      'BDI-II',      'BDI-II - Beck''s Depression Inventory Version 2',            TRUE,  5,  'main_clinical'),
('BARTHEL/2026',     'BARTHEL',     'Barthel Index',                                              TRUE,  2,  'main_clinical'),
('COMPASS-31/2026',  'COMPASS-31',  'COMPASS-31',                                                 TRUE,  14, 'all'),
('DASS-21/2026',     'DASS-21',     'DASS-21',                                                    TRUE,  11, 'all'),
('DHI/2026',         'DHI',         'DHI - Dizziness Handicap Inventory',                         TRUE,  2,  'main_clinical'),
('DN-4/2026',        'DN-4',        'DN-4',                                                       FALSE, 1,  'main_clinical'),
('DSRS/2026',        'DSRS',        'DSRS - Dementia Severity Rating Scale',                      FALSE, 1,  'main_clinical'),
('EQ-5D-5L/2026',    'EQ-5D-5L',    'EQ-5D-5L Health Questionnaire',                             TRUE,  12, 'all'),
('FFS/2026',         'FFS',         'FFS - Flinders Fatigue Scale',                               FALSE, 1,  'main_clinical'),
('FIQR/2026',        'FIQR',        'FIQR - Revised Fibromyalgia Impact Questionnaire',           FALSE, 1,  'main_clinical'),
('FSS/2026',         'FSS',         'FSS - Fatigue Severity Scale',                               FALSE, 1,  'main_clinical'),
('GAD-7/2026',       'GAD-7',       'GAD-7',                                                      TRUE,  5,  'general_registration'),
('GDS/2026',         'GDS',         'GDS - Global Deterioration Scale',                           FALSE, 1,  'main_clinical'),
('HDRS/2026',        'HDRS',        'HDRS - Hamilton Depression Rating Scale',                    FALSE, 1,  'main_clinical'),
('IADL/2026',        'IADL',        'IADL - Lawton Instrumental Activities of Daily Living Scale',FALSE, 1,  'main_clinical'),
('IBS-SSS/2026',     'IBS-SSS',     'IBS-SSS - IBS Symptom Severity Scale',                      FALSE, 1,  'main_clinical'),
('ISI/2026',         'ISI',         'ISI - Insomnia Severity Index',                              FALSE, 1,  'main_clinical'),
('KPS/2026',         'KPS',         'KPS - Karnofsky Performance Status Scale',                   FALSE, 1,  'main_clinical'),
('MADRS/2026',       'MADRS',       'MADRS - Montgomery and Asberg Depression Scale',             FALSE, 1,  'main_clinical'),
('MAS/2026',         'MAS',         'MAS - Modified Ashworth Scale',                              TRUE,  2,  'main_clinical'),
('MFIS/2026',        'MFIS',        'MFIS - Modified Fatigue Impact Scale',                       FALSE, 1,  'main_clinical'),
('MIDAS/2026',       'MIDAS',       'MIDAS - Migraine Disability Assessment',                     FALSE, 1,  'main_clinical'),
('MRC/2026',         'MRC',         'MRC - Medical Research Council Scale for Muscle Strength',   FALSE, 1,  'main_clinical'),
('MSQ/2026',         'MSQ',         'MSQ - Migraine-specific Quality of Life Questionnaire',      FALSE, 1,  'main_clinical'),
('MoCA/2026',        'MoCA',        'MoCA - Montreal Cognitive Assessment',                       TRUE,  4,  'main_clinical'),
('PDSS/2026',        'PDSS',        'PDSS - Parkinson''s Disease Sleep Scale',                    FALSE, 1,  'main_clinical'),
('PFS-16/2026',      'PFS-16',      'PFS-16 - Parkinson''s Disease Fatigue Scale',                FALSE, 1,  'main_clinical'),
('PSQI/2026',        'PSQI',        'PSQI - Pittsburgh Sleep Quality Index',                      TRUE,  5,  'general_registration'),
('PRS/2026',         'PRS',         'Pain Rating Scale',                                          TRUE,  4,  'main_clinical'),
('PainDETECT/2026',  'PainDETECT',  'PainDETECT',                                                 TRUE,  4,  'main_clinical'),
('SARA/2026',        'SARA',        'SARA - Scale for the Assessment and Rating of Ataxia',       TRUE,  2,  'main_clinical'),
('SNAP-IV/2026',     'SNAP-IV',     'SNAP-IV 26-Item Teacher and Parent Rating Scale',            FALSE, 1,  'main_clinical'),
('SS-QOL/2026',      'SS-QOL',      'SS-QOL - Stroke Specific Quality of Life Scale',             FALSE, 1,  'main_clinical'),
('SLEEP-50/2026',    'SLEEP-50',    'Sleep-50',                                                   FALSE, 1,  'main_clinical'),
('THI/2026',         'THI',         'THI - Tinnitus Handicap Inventory',                          FALSE, 1,  'main_clinical'),
('VAS/2026',         'VAS',         'VAS',                                                        FALSE, 1,  'main_clinical'),
('VVAS/2026',        'VVAS',        'VVAS - Visual Vertigo Analogue Scale',                       FALSE, 1,  'main_clinical')
ON CONFLICT (scale_id) DO NOTHING;


-- ============================================================
-- PRS DISEASE-SCALE MAP (v6 seed data)
-- ds_map_id format: 'DiseaseName/ScaleCode'
-- ============================================================

INSERT INTO prs_disease_scale_map (ds_map_id, disease_id, scale_id, display_order, is_required) VALUES
('Depression/Anxiety/EQ-5D-5L',               'DEPRESSION/ANXIETY/2026',    'EQ-5D-5L/2026',   1,  TRUE),
('Depression/Anxiety/COMPASS-31',              'DEPRESSION/ANXIETY/2026',    'COMPASS-31/2026',  2,  TRUE),
('Depression/Anxiety/DASS-21',                 'DEPRESSION/ANXIETY/2026',    'DASS-21/2026',     3,  TRUE),
('Depression/Anxiety/BDI-II',                  'DEPRESSION/ANXIETY/2026',    'BDI-II/2026',      4,  TRUE),
('Depression/Anxiety/GAD-7',                   'DEPRESSION/ANXIETY/2026',    'GAD-7/2026',       5,  TRUE),
('Depression/Anxiety/MADRS',                   'DEPRESSION/ANXIETY/2026',    'MADRS/2026',       6,  TRUE),
('Depression/Anxiety/PSQI',                    'DEPRESSION/ANXIETY/2026',    'PSQI/2026',        7,  TRUE),
('Chronic Pain/EQ-5D-5L',                      'CHRONICPAIN/2026',           'EQ-5D-5L/2026',   1,  TRUE),
('Chronic Pain/COMPASS-31',                    'CHRONICPAIN/2026',           'COMPASS-31/2026',  2,  TRUE),
('Chronic Pain/DASS-21',                       'CHRONICPAIN/2026',           'DASS-21/2026',     3,  TRUE),
('Chronic Pain/DN-4',                          'CHRONICPAIN/2026',           'DN-4/2026',        4,  TRUE),
('Chronic Pain/PainDETECT',                    'CHRONICPAIN/2026',           'PainDETECT/2026',  5,  TRUE),
('Chronic Pain/PRS',                           'CHRONICPAIN/2026',           'PRS/2026',         6,  TRUE),
('Chronic Pain/GAD-7',                         'CHRONICPAIN/2026',           'GAD-7/2026',       7,  TRUE),
('Chronic Pain/PSQI',                          'CHRONICPAIN/2026',           'PSQI/2026',        8,  TRUE),
('Fibromyalgia/EQ-5D-5L',                      'FIBROMYALGIA/2026',          'EQ-5D-5L/2026',   1,  TRUE),
('Fibromyalgia/COMPASS-31',                    'FIBROMYALGIA/2026',          'COMPASS-31/2026',  2,  TRUE),
('Fibromyalgia/PRS',                           'FIBROMYALGIA/2026',          'PRS/2026',         3,  TRUE),
('Fibromyalgia/PainDETECT',                    'FIBROMYALGIA/2026',          'PainDETECT/2026',  4,  TRUE),
('Fibromyalgia/FSS',                           'FIBROMYALGIA/2026',          'FSS/2026',         5,  TRUE),
('Fibromyalgia/VAS',                           'FIBROMYALGIA/2026',          'VAS/2026',         6,  TRUE),
('Fibromyalgia/FIQR',                          'FIBROMYALGIA/2026',          'FIQR/2026',        7,  TRUE),
('Migraine/EQ-5D-5L',                          'MIGRAINE/2026',              'EQ-5D-5L/2026',   1,  TRUE),
('Migraine/COMPASS-31',                        'MIGRAINE/2026',              'COMPASS-31/2026',  2,  TRUE),
('Migraine/MIDAS',                             'MIGRAINE/2026',              'MIDAS/2026',       3,  TRUE),
('Migraine/MSQ',                               'MIGRAINE/2026',              'MSQ/2026',         4,  TRUE),
('Migraine/PRS',                               'MIGRAINE/2026',              'PRS/2026',         5,  TRUE),
('Migraine/DASS-21',                           'MIGRAINE/2026',              'DASS-21/2026',     6,  TRUE),
('Migraine/PSQI',                              'MIGRAINE/2026',              'PSQI/2026',        7,  TRUE),
('Migraine/BDI-II',                            'MIGRAINE/2026',              'BDI-II/2026',      8,  TRUE),
('Ataxia/EQ-5D-5L',                            'ATAXIA/2026',                'EQ-5D-5L/2026',   1,  TRUE),
('Ataxia/COMPASS-31',                          'ATAXIA/2026',                'COMPASS-31/2026',  2,  TRUE),
('Ataxia/DHI',                                 'ATAXIA/2026',                'DHI/2026',         3,  TRUE),
('Ataxia/SARA',                                'ATAXIA/2026',                'SARA/2026',        4,  TRUE),
('Ataxia/DASS-21',                             'ATAXIA/2026',                'DASS-21/2026',     5,  TRUE),
('Ataxia/VVAS',                                'ATAXIA/2026',                'VVAS/2026',        6,  TRUE),
('Ataxia/BDI-II',                              'ATAXIA/2026',                'BDI-II/2026',      7,  TRUE),
('After Stroke/TBI/COMPASS-31',                'AFTERSTROKE/TBI/2026',       'COMPASS-31/2026',  1,  TRUE),
('After Stroke/TBI/KPS',                       'AFTERSTROKE/TBI/2026',       'KPS/2026',         2,  TRUE),
('After Stroke/TBI/SS-QOL',                    'AFTERSTROKE/TBI/2026',       'SS-QOL/2026',      3,  TRUE),
('After Stroke/TBI/MAS',                       'AFTERSTROKE/TBI/2026',       'MAS/2026',         4,  TRUE),
('After Stroke/TBI/MRC',                       'AFTERSTROKE/TBI/2026',       'MRC/2026',         5,  TRUE),
('After Stroke/TBI/DASS-21',                   'AFTERSTROKE/TBI/2026',       'DASS-21/2026',     6,  TRUE),
('After Stroke/TBI/MoCA',                      'AFTERSTROKE/TBI/2026',       'MoCA/2026',        7,  TRUE),
('After Stroke/TBI/BARTHEL',                   'AFTERSTROKE/TBI/2026',       'BARTHEL/2026',     8,  TRUE),
('After Stroke/TBI/PainDETECT',                'AFTERSTROKE/TBI/2026',       'PainDETECT/2026',  9,  TRUE),
('Dementia/EQ-5D-5L',                          'DEMENTIA/2026',              'EQ-5D-5L/2026',   1,  TRUE),
('Dementia/COMPASS-31',                        'DEMENTIA/2026',              'COMPASS-31/2026',  2,  TRUE),
('Dementia/AMTS',                              'DEMENTIA/2026',              'AMTS/2026',        3,  TRUE),
('Dementia/MoCA',                              'DEMENTIA/2026',              'MoCA/2026',        4,  TRUE),
('Dementia/DSRS',                              'DEMENTIA/2026',              'DSRS/2026',        5,  TRUE),
('Dementia/GDS',                               'DEMENTIA/2026',              'GDS/2026',         6,  TRUE),
('Dementia/IADL',                              'DEMENTIA/2026',              'IADL/2026',        7,  TRUE),
('Dementia/DASS-21',                           'DEMENTIA/2026',              'DASS-21/2026',     8,  TRUE),
('Parkinson''s Disease/COMPASS-31',            'PARKINSONSDISEASE/2026',     'COMPASS-31/2026',  1,  TRUE),
('Parkinson''s Disease/PDSS',                  'PARKINSONSDISEASE/2026',     'PDSS/2026',        2,  TRUE),
('Parkinson''s Disease/PFS-16',                'PARKINSONSDISEASE/2026',     'PFS-16/2026',      3,  TRUE),
('Parkinson''s Disease/MoCA',                  'PARKINSONSDISEASE/2026',     'MoCA/2026',        4,  TRUE),
('Parkinson''s Disease/PainDETECT',            'PARKINSONSDISEASE/2026',     'PainDETECT/2026',  5,  TRUE),
('Tinnitus/EQ-5D-5L',                          'TINNITUS/2026',              'EQ-5D-5L/2026',   1,  TRUE),
('Tinnitus/COMPASS-31',                        'TINNITUS/2026',              'COMPASS-31/2026',  2,  TRUE),
('Tinnitus/THI',                               'TINNITUS/2026',              'THI/2026',         3,  TRUE),
('Tinnitus/DASS-21',                           'TINNITUS/2026',              'DASS-21/2026',     4,  TRUE),
('Tinnitus/GAD-7',                             'TINNITUS/2026',              'GAD-7/2026',       5,  TRUE),
('Tinnitus/PSQI',                              'TINNITUS/2026',              'PSQI/2026',        6,  TRUE),
('Insomnia/EQ-5D-5L',                          'INSOMNIA/2026',              'EQ-5D-5L/2026',   1,  TRUE),
('Insomnia/COMPASS-31',                        'INSOMNIA/2026',              'COMPASS-31/2026',  2,  TRUE),
('Insomnia/DASS-21',                           'INSOMNIA/2026',              'DASS-21/2026',     3,  TRUE),
('Insomnia/GAD-7',                             'INSOMNIA/2026',              'GAD-7/2026',       4,  TRUE),
('Insomnia/PSQI',                              'INSOMNIA/2026',              'PSQI/2026',        5,  TRUE),
('Insomnia/AIS',                               'INSOMNIA/2026',              'AIS/2026',         6,  TRUE),
('Insomnia/FFS',                               'INSOMNIA/2026',              'FFS/2026',         7,  TRUE),
('Insomnia/ISI',                               'INSOMNIA/2026',              'ISI/2026',         8,  TRUE),
('Insomnia/SLEEP-50',                          'INSOMNIA/2026',              'SLEEP-50/2026',    9,  TRUE),
('Multiple Sclerosis/EQ-5D-5L',               'MULTIPLESCLEROSIS/2026',     'EQ-5D-5L/2026',   1,  TRUE),
('Multiple Sclerosis/COMPASS-31',             'MULTIPLESCLEROSIS/2026',     'COMPASS-31/2026',  2,  TRUE),
('Multiple Sclerosis/DHI',                    'MULTIPLESCLEROSIS/2026',     'DHI/2026',         3,  TRUE),
('Multiple Sclerosis/SARA',                   'MULTIPLESCLEROSIS/2026',     'SARA/2026',        4,  TRUE),
('Multiple Sclerosis/MFIS',                   'MULTIPLESCLEROSIS/2026',     'MFIS/2026',        5,  TRUE),
('Multiple Sclerosis/MoCA',                   'MULTIPLESCLEROSIS/2026',     'MoCA/2026',        6,  TRUE),
('Multiple Sclerosis/BARTHEL',                'MULTIPLESCLEROSIS/2026',     'BARTHEL/2026',     7,  TRUE),
('ADHD/EQ-5D-5L',                             'ADHD/2026',                  'EQ-5D-5L/2026',   1,  TRUE),
('ADHD/COMPASS-31',                           'ADHD/2026',                  'COMPASS-31/2026',  2,  TRUE),
('ADHD/ASRS-v1.1',                            'ADHD/2026',                  'ASRS-v1.1/2026',   3,  TRUE),
('ADHD/DASS-21',                              'ADHD/2026',                  'DASS-21/2026',     4,  TRUE),
('ADHD/SNAP-IV',                              'ADHD/2026',                  'SNAP-IV/2026',     5,  TRUE),
('ALS/EQ-5D-5L',                              'ALS/2026',                   'EQ-5D-5L/2026',   1,  TRUE),
('ALS/COMPASS-31',                            'ALS/2026',                   'COMPASS-31/2026',  2,  TRUE),
('ALS/DASS-21',                               'ALS/2026',                   'DASS-21/2026',     3,  TRUE),
('ALS/BDI-II',                                'ALS/2026',                   'BDI-II/2026',      4,  TRUE),
('ALS/MAS',                                   'ALS/2026',                   'MAS/2026',         5,  TRUE),
('ALS/GAD-7',                                 'ALS/2026',                   'GAD-7/2026',       6,  TRUE),
('ALS/ALSFRS-R',                              'ALS/2026',                   'ALSFRS-R/2026',    7,  TRUE),
('Irritable Bowel Disease/EQ-5D-5L',          'IRRITABLEBOWELDISEASE/2026', 'EQ-5D-5L/2026',   1,  TRUE),
('Irritable Bowel Disease/COMPASS-31',        'IRRITABLEBOWELDISEASE/2026', 'COMPASS-31/2026',  2,  TRUE),
('Irritable Bowel Disease/IBS-SSS',           'IRRITABLEBOWELDISEASE/2026', 'IBS-SSS/2026',     3,  TRUE),
('Irritable Bowel Disease/PRS',               'IRRITABLEBOWELDISEASE/2026', 'PRS/2026',         4,  TRUE),
('Irritable Bowel Disease/DASS-21',           'IRRITABLEBOWELDISEASE/2026', 'DASS-21/2026',     5,  TRUE),
('Irritable Bowel Disease/BDI-II',            'IRRITABLEBOWELDISEASE/2026', 'BDI-II/2026',      6,  TRUE),
('Irritable Bowel Disease/HDRS',              'IRRITABLEBOWELDISEASE/2026', 'HDRS/2026',        7,  TRUE)
ON CONFLICT (ds_map_id) DO NOTHING;

-- NOTE: prs_questions, prs_options, prs_scale_question_map,
-- prs_disease_question_map seed data comes from PRS_DET.xlsx.
-- Run: python backend/scripts/seed_scales.py


-- ============================================================
-- ANAMNESIS QUESTIONS (21 questions, 8 sections)
-- Exact from v6 MasterDB.sql — do not modify question_ids.
-- ============================================================

INSERT INTO anamnesis_questions
    (question_id, section_number, section_title, question_code, question_text,
     answer_type, is_required, display_order, depends_on_question_id, depends_on_value, helper_text)
VALUES
('ANA/S01/Q001', 1, 'Chief Complaint & Diagnosis', 'chief_complaint',
 'Why are you here today? / Primary Diagnosis',
 'textarea', TRUE, 1, NULL, NULL, 'Describe the main reason for this visit and any existing diagnosis'),

('ANA/S02/Q001', 2, 'Main Symptoms', 'main_symptoms',
 'What are your main symptoms?',
 'textarea', TRUE, 2, NULL, NULL, 'Describe the primary symptoms you are experiencing'),

('ANA/S02/Q002', 2, 'Main Symptoms', 'initial_symptoms',
 'What were the initial symptoms?',
 'textarea', TRUE, 3, NULL, NULL, 'Describe how your symptoms first appeared'),

('ANA/S02/Q003', 2, 'Main Symptoms', 'diagnosis_related',
 'Is there a diagnosis related to the symptoms?',
 'radio', TRUE, 4, NULL, NULL, NULL),

('ANA/S02/Q004', 2, 'Main Symptoms', 'diagnosis_details',
 'If yes, please specify the diagnosis',
 'conditional_text', FALSE, 5, 'ANA/S02/Q003', 'yes', 'Please specify the confirmed or suspected diagnosis'),

('ANA/S02/Q005', 2, 'Main Symptoms', 'symptoms_start',
 'When did the symptoms start?',
 'text', TRUE, 6, NULL, NULL, 'e.g. 3 months ago, January 2024'),

('ANA/S02/Q006', 2, 'Main Symptoms', 'symptoms_duration',
 'For how long have you had these symptoms?',
 'text', TRUE, 7, NULL, NULL, 'e.g. 2 weeks, 6 months, 2 years'),

('ANA/S02/Q007', 2, 'Main Symptoms', 'symptoms_frequency',
 'How often do you have these symptoms?',
 'select', TRUE, 8, NULL, NULL, NULL),

('ANA/S02/Q008', 2, 'Main Symptoms', 'symptoms_intensity',
 'How intense or severe are these symptoms?',
 'select', TRUE, 9, NULL, NULL, NULL),

('ANA/S02/Q009', 2, 'Main Symptoms', 'symptoms_progression',
 'Are the symptoms getting better, worse, or staying about the same?',
 'select', TRUE, 10, NULL, NULL, NULL),

('ANA/S03/Q001', 3, 'Secondary Symptoms', 'secondary_symptoms',
 'What are your secondary symptoms? (select all that apply)',
 'checkbox', FALSE, 11, NULL, NULL, 'Check all that apply'),

('ANA/S03/Q002', 3, 'Secondary Symptoms', 'secondary_symptoms_details',
 'Additional details about secondary symptoms',
 'textarea', FALSE, 12, NULL, NULL, 'Please provide more details about the checked symptoms'),

('ANA/S04/Q001', 4, 'Operations / Surgeries', 'has_operations',
 'Have you had any operations or surgeries?',
 'radio', TRUE, 13, NULL, NULL, NULL),

('ANA/S04/Q002', 4, 'Operations / Surgeries', 'operations_details',
 'If yes, please provide details',
 'conditional_text', FALSE, 14, 'ANA/S04/Q001', 'yes',
 'Include: which operations, how many, when performed, post-surgery condition / effects'),

('ANA/S05/Q001', 5, 'Previous or Ongoing Treatments', 'previous_treatments',
 'Previous or ongoing treatments (physiotherapy, speech therapy, psychotherapy, etc.)',
 'textarea', FALSE, 15, NULL, NULL,
 'Include: type of treatment, how long, how often, outcomes / improvements'),

('ANA/S06/Q001', 6, 'Medications & Supplements', 'current_medications',
 'Current medications and supplements',
 'textarea', FALSE, 16, NULL, NULL, 'List all current medications and supplements with dosages'),

('ANA/S07/Q001', 7, 'Brain MRI & Other Scans', 'has_brain_mri',
 'Have you had a Brain MRI?',
 'radio', TRUE, 17, NULL, NULL, NULL),

('ANA/S07/Q002', 7, 'Brain MRI & Other Scans', 'mri_details',
 'If yes, when was it performed and what were the results?',
 'conditional_text', FALSE, 18, 'ANA/S07/Q001', 'yes',
 'Include: date of MRI, results, any other relevant findings'),

('ANA/S07/Q003', 7, 'Brain MRI & Other Scans', 'other_scans',
 'Other scans (CT, EEG, EMG, etc.)',
 'textarea', FALSE, 19, NULL, NULL, 'List any other diagnostic scans or tests performed'),

('ANA/S08/Q001', 8, 'Neuromodulation Experience', 'has_neuromodulation',
 'Have you used any neuromodulation techniques before?',
 'radio', TRUE, 20, NULL, NULL, NULL),

('ANA/S08/Q002', 8, 'Neuromodulation Experience', 'neuromodulation_details',
 'If yes, please specify devices used and experience',
 'conditional_text', FALSE, 21, 'ANA/S08/Q001', 'yes',
 'Include: type of device, duration of use, effectiveness, any side effects')

ON CONFLICT (question_id) DO UPDATE SET
    question_text  = EXCLUDED.question_text,
    helper_text    = EXCLUDED.helper_text,
    section_title  = EXCLUDED.section_title,
    display_order  = EXCLUDED.display_order;


-- ============================================================
-- ANAMNESIS OPTIONS (31 options for radio/select/checkbox questions)
-- ============================================================

INSERT INTO anamnesis_options (option_id, question_id, option_label, option_value, display_order)
VALUES
('ANA/S02/Q003/O01', 'ANA/S02/Q003', 'Yes', 'yes', 1),
('ANA/S02/Q003/O02', 'ANA/S02/Q003', 'No',  'no',  2),

('ANA/S02/Q007/O01', 'ANA/S02/Q007', 'Daily',                'daily',              1),
('ANA/S02/Q007/O02', 'ANA/S02/Q007', 'Several times a week', 'several-times-week', 2),
('ANA/S02/Q007/O03', 'ANA/S02/Q007', 'Weekly',               'weekly',             3),
('ANA/S02/Q007/O04', 'ANA/S02/Q007', 'Monthly',              'monthly',            4),
('ANA/S02/Q007/O05', 'ANA/S02/Q007', 'Occasionally',         'occasionally',       5),

('ANA/S02/Q008/O01', 'ANA/S02/Q008', 'Mild',        'mild',       1),
('ANA/S02/Q008/O02', 'ANA/S02/Q008', 'Moderate',    'moderate',   2),
('ANA/S02/Q008/O03', 'ANA/S02/Q008', 'Severe',      'severe',     3),
('ANA/S02/Q008/O04', 'ANA/S02/Q008', 'Very Severe', 'very-severe',4),

('ANA/S02/Q009/O01', 'ANA/S02/Q009', 'Getting better',         'better',      1),
('ANA/S02/Q009/O02', 'ANA/S02/Q009', 'Getting worse',          'worse',       2),
('ANA/S02/Q009/O03', 'ANA/S02/Q009', 'Staying about the same', 'same',        3),
('ANA/S02/Q009/O04', 'ANA/S02/Q009', 'Fluctuating',            'fluctuating', 4),

('ANA/S03/Q001/O01', 'ANA/S03/Q001', 'Sleep Issues',           'sleep',            1),
('ANA/S03/Q001/O02', 'ANA/S03/Q001', 'Concentration Problems', 'concentration',    2),
('ANA/S03/Q001/O03', 'ANA/S03/Q001', 'Memory Issues',          'memory',           3),
('ANA/S03/Q001/O04', 'ANA/S03/Q001', 'Gastrointestinal Issues','gastrointestinal', 4),
('ANA/S03/Q001/O05', 'ANA/S03/Q001', 'Mood Fluctuations',      'mood',             5),
('ANA/S03/Q001/O06', 'ANA/S03/Q001', 'Fatigue',                'fatigue',          6),
('ANA/S03/Q001/O07', 'ANA/S03/Q001', 'Weakness',               'weakness',         7),
('ANA/S03/Q001/O08', 'ANA/S03/Q001', 'Pain',                   'pain',             8),
('ANA/S03/Q001/O09', 'ANA/S03/Q001', 'Depression/Anxiety',     'depression',       9),
('ANA/S03/Q001/O10', 'ANA/S03/Q001', 'Bladder Function Issues','bladder',          10),

('ANA/S04/Q001/O01', 'ANA/S04/Q001', 'Yes', 'yes', 1),
('ANA/S04/Q001/O02', 'ANA/S04/Q001', 'No',  'no',  2),

('ANA/S07/Q001/O01', 'ANA/S07/Q001', 'Yes', 'yes', 1),
('ANA/S07/Q001/O02', 'ANA/S07/Q001', 'No',  'no',  2),

('ANA/S08/Q001/O01', 'ANA/S08/Q001', 'Yes', 'yes', 1),
('ANA/S08/Q001/O02', 'ANA/S08/Q001', 'No',  'no',  2)

ON CONFLICT (option_id) DO NOTHING;

COMMIT;
