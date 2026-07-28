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
