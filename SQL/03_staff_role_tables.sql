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
--
-- clinic_id: denormalized primary-clinic fast-lookup, added in
-- File 20 (20_doctor_clinic_id.sql) after clinics exists. Kept in
-- sync at write time; clinic_staff_assignments remains the source
-- of truth for multi-clinic doctor membership.
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
