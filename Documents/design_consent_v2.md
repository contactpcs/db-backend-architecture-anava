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
| D3 | (revised 2026-10-21) Under 18 → guardian details mandatory at registration, guardian signs. 18+ with guardian details filled in (desk or self-registration) → guardian signs; reason optional: `lacks_capacity`, `lawful_guardian`, `nominated_representative`, default `patient_requested` ("Patient asked for a guardian's help"). No guardian details → patient signs. Desk route: login mobile must be the guardian's. Counsel to confirm guardian consent for a capable adult. |
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

---

# Consent v3 — summary + full HTML form

Status: approved 2026-10-21 (chat decisions). SQL/v1/123_consent_full_html.sql
(alembic 0069), consent module, Combo frontend.

## Decisions

| # | Decision |
|---|---|
| H1 | Layered notice + clickwrap. `content` is the **summary** the signer reads above the checkboxes; `full_html` is the **complete, binding form** behind a "Read the full consent form" link. No drawn/typed signature. |
| H2 | Opening the full form is not required before "I agree"; the first time the signer opens it is logged (`full_viewed_at`). |
| H3 | `full_html` is stored in the DB on the template row, byte-for-byte as uploaded (never rewritten), frozen on publish with the rest of the template. One HTML per (type, role, language, version). |
| H4 | Unsafe HTML is **rejected** on save, not cleaned: `<script>`, `on*=` handlers, `javascript:` URLs, `<iframe>/<frame>/<object>/<embed>/<form>/<base>/<meta http-equiv>/<link>`, any external `http(s):`/`//` resource in `src`/`href`(stylesheet)/`url()`/`@import`; size cap 1 MB. Only `data:` images/fonts. `<a href>` links to external sites are allowed (they do not load anything). |
| H5 | Shown in `<iframe sandbox srcdoc>` (no scripts, opaque origin). PDF rendered from the same filled HTML with WeasyPrint, network blocked (data: URIs only). |
| H6 | Details are filled automatically from a fixed placeholder catalogue (below); the super admin only places them. Unknown placeholders, or ones not valid for the consent type, are rejected on save. Values are HTML-escaped in `full_html`. |
| H7 | `<div data-show-if="guardian">…</div>` is kept only when a guardian signs; `data-show-if="self"` only when the person signs for themselves. |
| H8 | Time placeholders (`[CONSENT_DATE]`, `[SIGNED_AT]`) are left as tokens when hashing, and show "On acceptance" / today's date before signing. |
| H9 | At signing, every filled value is snapshotted to `consent_records.fill_values` (JSONB), so the record re-renders identically forever, and `rendered_full_hash` stores the hash of the filled full HTML. `rendered_hash` now covers summary + clauses + full HTML. |
| H10 | Signed PDF = the filled full HTML + an appended evidence page (summary shown, checkbox answers, signer, time, IP, device, hashes). Templates without `full_html` (v1/v2) keep the fpdf2 layout. |
| H11 | S3 key uses `patients.patient_id` (not the profile id): `regions/{r}/clinics/{c}/consents/patients/{patient_id}/{type}_v{n}_{uuid7}.pdf`. |
| H12 | Not now: guardian ID proof, staff employee code. |
| H13 | Signing is refused (409 `CONSENT_TEXT_CHANGED`) when a newer version of the form went live while the signer was reading; they reload and accept the current one. |
| H14 | Template create/edit/delete/publish/preview need an ACTIVE super admin (`ACCOUNT_INACTIVE`) — consent routes are open to inactive accounts so they can sign their own onboarding. |
| H16 | The summary page shows NO personal details: personal placeholders (names, DOB, age, gender, MRN, phone, email, address, guardian fields, staff identity, licence) are refused in the title, summary and checkbox labels (`CONSENT_TEMPLATE_PERSONAL_IN_SUMMARY`) and the guardian banner is gone from the consent screen. They belong in the full form. Clinic, role, version and dates stay allowed. |
| H17 | Input fields in the full form are filled by the system and locked: `<input name="PATIENT_NAME">` / `<textarea name="patient_address">` (name = a catalogue placeholder, any case) is rewritten to `type="text" value="…" readonly`; any value the admin typed is dropped. Unknown names, unnamed fields, non-text types (checkbox, hidden, submit…) and `name="SIGNATURE_BLOCK"` are refused. Bound fields count as used placeholders, so their values are in `fill_values` and printed in the PDF. |
| H18 | Per-type field rules (fill.py `required_placeholders` / `recommended_placeholders`). Required — patient types: PATIENT_NAME, PATIENT_DOB, GUARDIAN_NAME, GUARDIAN_RELATION (relationship = proof of the guardian's authority, added 2026-10-21) (+ FROM_CLINIC, TO_CLINIC for transfers); staff: STAFF_NAME, STAFF_ROLE (+ DOCTOR_LICENSE_NO for doctor); clinic: PARTNER_CLINIC_NAME, SIGNER_NAME; all: CONSENT_DATE, SIGNATURE_BLOCK. Publishing a full form without them is refused (`CONSENT_TEMPLATE_MISSING_FIELDS`); drafts may be incomplete. `POST /consent-templates/analyze` returns every field the uploaded HTML captures (input/text), missing required/recommended, unknown names and unsafe HTML; `GET /consent-templates/starter` gives a ready-structured form per type (starters.py). The admin editor shows the guide, the starter and the live check; Publish is disabled until the check passes. |
| H15 | SQL 123 adds its own trigger (`trg_consent_template_guard_full_html`) instead of replacing 122's function, so re-running 122 can never drop the full-form freeze. |

## Placeholder catalogue

All types: `[SIGNER_NAME] [CLINIC_NAME] [CLINIC_ADDRESS] [CLINIC_PHONE] [CONSENT_DATE]
[SIGNED_AT] [CONSENT_ID] [CONSENT_VERSION] [SIGNATURE_BLOCK]`.

Patient types (`patient_*`): `[PATIENT_NAME] [PATIENT_DOB] [PATIENT_AGE] [PATIENT_GENDER]
[PATIENT_MRN] [PATIENT_PHONE] [PATIENT_EMAIL] [PATIENT_ADDRESS] [GUARDIAN_NAME]
[GUARDIAN_RELATION] [GUARDIAN_PHONE] [GUARDIAN_REASON]`; transfers also
`[FROM_CLINIC] [TO_CLINIC]` (from the patient's transfer row).

Staff types (`staff_*`): `[STAFF_NAME] [STAFF_ROLE] [ROLE] [STAFF_EMAIL] [STAFF_PHONE]
[STAFF_DOB] [STAFF_GENDER] [DOCTOR_LICENSE_NO] [DOCTOR_SPECIALIZATION] [REGION_NAME]`.

Clinic types (`clinic_*`): `[PARTNER_CLINIC_NAME]`.

Data minimisation: no blood group, allergies, insurance, occupation, marital
status or emergency contact. Missing values render as "—".

## API

- `POST/PATCH /consent-templates` accept `full_html` (nullable).
- `GET /consent-templates/placeholders?consent_type=` — catalogue for the editor.
- `POST /consent-templates/{id}/preview?format=html|pdf` — sample data, super admin.
- `GET /consent-records/{id}/full` — filled full HTML (`{html, rendered_full_hash}`);
  logs `full_viewed_at` the first time the subject opens a pending record.
- Sign is unchanged; the hash it checks now includes the full HTML.
