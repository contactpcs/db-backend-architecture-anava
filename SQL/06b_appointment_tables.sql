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
