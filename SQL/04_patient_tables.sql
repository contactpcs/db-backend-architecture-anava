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
