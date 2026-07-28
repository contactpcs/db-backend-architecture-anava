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
