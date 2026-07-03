-- ============================================================
-- Anava Clinic — DB Schema
-- File 24: Patient self-registration + receptionist approval gate
--
-- Two legitimate patient onboarding paths now exist:
--   - Staff-registered (existing, unaffected): a receptionist registers a
--     walk-in patient in person. Witness is physically present, consent
--     activates the account immediately, same as before this change.
--   - Self-registered (new): a patient reaches the public site and works
--     through the entire 6-step registration_status machine themselves
--     (demographics -> disease_selected -> consent_signed ->
--     anamnesis_complete -> general_prs_complete -> registration_complete),
--     no receptionist involved, no witness available. The account stays
--     inactive (profiles.is_active = FALSE) through every one of those
--     steps. Only once registration_status = 'registration_complete' does
--     a receptionist see the request and approve/reject it — approval is
--     what finally flips is_active = TRUE.
--
-- approval_status stays 'not_required' forever for staff-registered
-- patients (no gate, matches pre-existing behavior exactly). Self-
-- registered patients start 'pending', become 'approved'/'rejected' only
-- after a receptionist decides.
-- ============================================================

ALTER TABLE patients ADD COLUMN self_registered BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE patients ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'not_required'
    CHECK (approval_status IN ('not_required', 'pending', 'approved', 'rejected'));
ALTER TABLE patients ADD COLUMN approved_by UUID REFERENCES profiles(id) ON DELETE RESTRICT;
ALTER TABLE patients ADD COLUMN approved_at TIMESTAMPTZ;
ALTER TABLE patients ADD COLUMN rejection_reason TEXT;
