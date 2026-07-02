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
