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
