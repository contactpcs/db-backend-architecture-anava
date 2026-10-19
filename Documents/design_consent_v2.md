# Consent v2 — design

Status: approved 2026-10-16 (chat decisions). Built in SQL/v1/122_consent_v2.sql
(alembic 0068), consent module, reception registration, Combo frontend.

## Goals

1. Consent is valid only when the data principal (patient, or the guardian
   holding the patient's login) accepts it in their own authenticated session.
   Staff never sign or tick for a patient.
2. Every signature is provable: exact text shown, every checkbox answer,
   server-captured IP + user agent, auth method, signer identity, hash, and an
   immutable PDF copy in the compliance bucket.
3. Super admin manages consent templates (text + any number of checkboxes)
   from the portal, with versioning. Published versions are immutable.

## Decisions

| # | Decision |
|---|---|
| D1 | Receptionist registers; no password, no consent, no desk OTP. Cognito `AdminCreateUser` sends a temporary password by SMS (mobile) or email. Patient sets own password at first login (`NEW_PASSWORD_REQUIRED`, existing screen). |
| D2 | Wrong contact: receptionist can resend the login, optionally with a corrected contact, until the patient first logs in. |
| D3 | Minor (<18 at signing, from DOB) or guardian designated → guardian signs. Login contact must be the guardian's mobile. Adult + guardian requires a reason: `lacks_capacity`, `lawful_guardian`, `nominated_representative`. |
| D4 | Checkboxes are JSONB on the template (`clauses`), answers are JSONB on the record (`responses`) with the label text copied in. |
| D5 | PDF in the compliance bucket: `regions/{r}/clinics/{c}/consents/patients/{p}/{type}_v{n}_{uuid7}.pdf` (staff: `.../consents/staff/{s}/...`). DB record is the legal record; PDF is a copy, regenerated on demand if creation failed. |
| D6 | Patient self-signup disabled behind `PATIENT_SELF_SIGNUP_ENABLED` (default false). |
| D7 | Re-consent: publishing a new `patient_onboarding` version can require every existing patient to re-sign; they are gated to the consent screen until they do. |
| D8 | Receptionist-set passwords of existing patients: `scripts/force_patient_password_reset.py` (dry run by default). Login returns `PASSWORD_RESET_REQUIRED`; patient uses forgot-password. |

## Data model (SQL 122)

`reference.consent_templates` + `language TEXT DEFAULT 'en'`, `clauses JSONB`
(`[{code, label, required}]`), `published_at TIMESTAMPTZ` (NULL = draft).
- `content_hash` computed by trigger: sha256(title, content, clauses).
- Published rows: content/clauses/type/role/version/language frozen; delete blocked.
- One active row per (type, role, language) — partial unique index.
- Existing active v1 rows: frozen as published; a v2 copy with one required
  clause ("I have read and understood…") becomes active. v1 hashes untouched.

`compliance.consent_records` + `rendered_title`, `rendered_content`,
`rendered_hash`, `responses JSONB`, `user_agent`, `auth_method`,
`signer_type` (self|guardian), `signer_name`, `signer_relation`,
`signer_contact`, `guardian_reason`, `pdf_sha256`, `pdf_version_id`,
`pdf_generated_at`.
- Trigger: once signed, only `status → revoked` (+ revoked_at/by) and PDF
  fields (NULL → value) may change.

`core.patients` + `guardian_reason`.

## Signing flow

1. Registration creates a `pending` record on the active template.
2. `GET /consent-records/me/pending` — re-points a pending record to the
   currently active template, renders placeholders (`[PATIENT_NAME]`,
   `[CLINIC_NAME]`, `[ROLE]`, `[SIGNER_NAME]`), returns text, clauses, signer,
   `rendered_hash`.
3. `PATCH /consent-records/{id}/status {status: signed, accepted: [codes], rendered_hash}`
   - caller must be the record's subject
   - re-render; hash must match what the client displayed (else 409)
   - every required clause accepted; unknown codes rejected
   - signer resolved from patient row (minor → guardian mandatory)
   - IP from request context, user agent from header, auth method from mode
4. PDF built (fpdf2, Unicode font), stored in compliance bucket with Object
   Lock when configured; key + sha256 + version id saved on the record.
5. `GET /consent-records/{id}/document` — signed snapshot (or live render if
   pending). `GET /consent-records/{id}/pdf` — presigned download.

## Template admin (super_admin)

`POST /consent-templates` (draft, version auto-incremented),
`PATCH /consent-templates/{id}` (draft only), `DELETE` (draft only),
`POST /consent-templates/{id}/publish {require_reconsent}`.
Clause rules: 1–20 clauses, code `^[a-z0-9_]{2,40}$` unique, label 1–1000 chars.

## Out of scope (later)

Withdrawal effects + erasure, language picker UI (column ready), clinical
informed consent for device sessions, consent-manager integration.

## AWS / legal follow-ups

- Cognito invitation message (SMS needs a DLT-registered template in India);
  `TemporaryPasswordValidityDays`.
- `CONSENT_OBJECT_LOCK_DAYS` once counsel sets retention.
- Counsel drafts v3 wording; super admin publishes it with re-consent.
