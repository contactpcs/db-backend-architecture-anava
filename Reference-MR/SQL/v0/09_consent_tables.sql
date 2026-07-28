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
