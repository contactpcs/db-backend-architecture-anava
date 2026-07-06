-- ============================================================
-- 28_consent_redesign.sql
--
-- Two changes, requested together after the regional_admin consent bug
-- (SQL/27) turned out to be one symptom of a bigger problem: consent-record
-- creation was duplicated across 4 call sites (staff/admin/patients
-- services) with slightly different logic each time, and profiles.is_active
-- was overloaded to mean three different things at once (admin on/off
-- switch, "has consent been signed", and — for patients only — "has the
-- entire 6-step registration finished").
--
-- 1. consent_templates gets a per-role split for staff_onboarding: instead
--    of one shared template with a [ROLE] placeholder, each of the 6 staff
--    roles gets its own independently-editable row. patient_onboarding and
--    the other 6 consent types are untouched (role IS NULL) — they don't
--    vary by role and aren't wired into any live "entry" flow.
--
-- 2. profiles gains consent_signed, a dedicated flag separate from
--    is_active. is_active keeps meaning exactly what it always has (the
--    real access gate + the admin's manual on/off switch); consent_signed
--    is purely "has this account signed its required onboarding consent".
--    For staff roles the two are kept in sync at sign-time; for patients
--    is_active still only flips at registration-complete/approval
--    (unchanged) and consent_signed is an informational companion flag.
-- ============================================================

ALTER TABLE profiles
    ADD COLUMN consent_signed BOOLEAN NOT NULL DEFAULT TRUE;

-- The DEFAULT TRUE above is correct for NEW rows going forward (every
-- creation call site that requires consent explicitly passes FALSE), but it
-- blanket-backfills TRUE onto EXISTING rows too — wrong for any profile
-- that's currently is_active=FALSE (never finished onboarding), which would
-- otherwise show consent_signed=TRUE despite never having signed anything.
-- Derive the true value from consent_records for exactly those rows.
UPDATE profiles p
SET consent_signed = EXISTS (
    SELECT 1 FROM consent_records cr
    WHERE (cr.staff_id = p.id OR cr.patient_id = p.id) AND cr.status = 'signed'
)
WHERE p.is_active = FALSE;

ALTER TABLE consent_templates
    ADD COLUMN role TEXT CHECK (role IS NULL OR role IN (
        'super_admin', 'regional_admin', 'clinic_admin',
        'doctor', 'clinical_assistant', 'receptionist'
    ));

ALTER TABLE consent_templates
    DROP CONSTRAINT consent_templates_consent_type_version_key;

ALTER TABLE consent_templates
    ADD CONSTRAINT uq_consent_templates_type_role_version
        UNIQUE NULLS NOT DISTINCT (consent_type, role, version);

-- Retire the old shared staff_onboarding template (UPDATE not DELETE —
-- consent_records.template_id is ON DELETE RESTRICT, and past signed
-- records still point at it).
UPDATE consent_templates
SET is_active = FALSE
WHERE consent_type = 'staff_onboarding' AND role IS NULL;

INSERT INTO consent_templates (consent_type, role, version, title, content, is_active) VALUES
(
    'staff_onboarding', 'doctor', 1,
    'Doctor Onboarding Consent',
    'I, the undersigned Doctor, consent to joining [CLINIC_NAME]. I acknowledge receipt of the '
    'staff handbook and code of conduct. I understand my responsibilities regarding patient '
    'confidentiality, data protection, clinical protocols, and my authority to authorize assessment '
    'protocols and treatment plans as defined by Anava Clinic and Mana Health Sciences Group. '
    'I consent to the storage of my professional credentials (license, specialization, hospital '
    'affiliation) and employment records in the Anava platform.',
    TRUE
),
(
    'staff_onboarding', 'clinical_assistant', 1,
    'Clinical Assistant Onboarding Consent',
    'I, the undersigned Clinical Assistant, consent to joining [CLINIC_NAME]. I acknowledge receipt '
    'of the staff handbook and code of conduct. I understand my responsibilities regarding patient '
    'confidentiality, data protection, and my role in designing assessment protocols and '
    'administering Session 1/3 diagnostics as defined by Anava Clinic and Mana Health Sciences '
    'Group. I consent to the storage of my professional credentials and employment records in the '
    'Anava platform.',
    TRUE
),
(
    'staff_onboarding', 'receptionist', 1,
    'Receptionist Onboarding Consent',
    'I, the undersigned Receptionist, consent to joining [CLINIC_NAME]. I acknowledge receipt of the '
    'staff handbook and code of conduct. I understand my responsibilities regarding patient '
    'registration, appointment scheduling, witnessing patient consent, and data protection as '
    'defined by Anava Clinic and Mana Health Sciences Group. I consent to the storage of my '
    'employment records in the Anava platform.',
    TRUE
),
(
    'staff_onboarding', 'clinic_admin', 1,
    'Clinic Admin Onboarding Consent',
    'I, the undersigned Clinic Admin, consent to joining [CLINIC_NAME]. I acknowledge receipt of the '
    'staff handbook and code of conduct. I understand my responsibilities regarding daily clinic '
    'operations, staff requests, payment waivers, and data protection as defined by Anava Clinic and '
    'Mana Health Sciences Group. I consent to the storage of my professional credentials and '
    'employment records in the Anava platform.',
    TRUE
),
(
    'staff_onboarding', 'regional_admin', 1,
    'Regional Admin Onboarding Consent',
    'I, the undersigned Regional Admin, consent to joining Anava Clinic for my assigned region. I '
    'acknowledge receipt of the staff handbook and code of conduct. I understand my responsibilities '
    'regarding staff approval, main-branch stock management, and regional oversight as defined by '
    'Anava Clinic and Mana Health Sciences Group. I consent to the storage of my professional '
    'credentials and employment records in the Anava platform.',
    TRUE
),
(
    'staff_onboarding', 'super_admin', 1,
    'Super Admin Onboarding Consent',
    'I, the undersigned Super Admin, consent to joining Anava Clinic with system-wide access. I '
    'acknowledge receipt of the staff handbook and code of conduct. I understand my responsibilities '
    'regarding system-wide administration, central stock, and platform bootstrap as defined by Anava '
    'Clinic and Mana Health Sciences Group. I consent to the storage of my professional credentials '
    'and employment records in the Anava platform.',
    TRUE
)
ON CONFLICT (consent_type, role, version) DO NOTHING;
