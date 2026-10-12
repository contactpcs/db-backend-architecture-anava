# Anava Final Report — Implementation Plan v1

Status: LOCKED, 2026-10-12. All decisions confirmed by the product owner (section 7 resolved with defaults).
Order of work: Doc (this file) → Schema (SQL 117) → Code. No code before schema is locked.

## 1. What the feature is

A doctor prints a **Final Report** for a patient. Before printing the doctor picks one of two scopes:

| Scope | Meaning |
|---|---|
| `overall` | Everything from the patient's start to today |
| `last_consultation` | Only the most recent consultation (its anamnesis, notes, PRS, sessions in its window) |

The doctor types a mandatory **Diagnosis**, ticks which doctor's notes to include, ticks a
"I confirm" checkbox that stamps the doctor's name as a signature, and prints. At that moment the report
is frozen, stored, and appears in the patient's profile ("My Results" → Final Reports).

## 2. Decisions (from the product owner)

1. Two scopes: overall, last consultation (above).
2. E-sign = one confirm checkbox. Doctor's name is rendered in a signature / handwritten-style font.
   No drawn pad, no OTP.
3. A report row is created **only when the doctor signs**. No draft table state. Preview before
   signing is computed live from existing tables and never stored.
4. Doctor selects which clinical notes go in the report.
5. Anamnesis: print all questions and all responses (no "key question" list).
6. Brain mapping: **not implemented yet**. Section slot reserved in the template, renders "Not available".
7. Medication: full history (active and stopped).
8. Older reports stay visible. A new report never edits an old one.
9. Only doctors generate. Doctors see previous reports of their patients. Admins read previous ones.
10. PRS: last 5 PRS scores of any origin (consultation or device session), scales bundled under the
    disease/condition; device-session ones also carry the selected condition and the device.
    Plus a "last 5 weeks progress" row for the patient.
11. Signed report is pushed to the patient profile automatically. Patient can open any of their
    reports anytime from "My Results", with sort and filter.

## 3. Section → data source map

| # | Report section | Source (existing) | Notes |
|---|---|---|---|
| 1 | Anamnesis | `anamnesis_assessments` + `anamnesis_responses` + `reference.anamnesis_questions` | Consultation anamnesis only (`assessment_stage = 'main'`). `overall`: every consultation's, newest first. `last_consultation`: that consultation's one. All Q&A. Registration anamnesis is not read (owner decision 2026-10-12). |
| 2 | Diagnosis | **new**: typed on the report | Required. See "Still open" Q-A. |
| 3 | Brain mapping | none | Placeholder `{"available": false}` in snapshot |
| 4 | PRS | `prs_assessment_instances`, `prs_scale_results`, `disease_composite_scores`, `device_session_prs_responses`, `followup_prs_responses` | Clinical PRS only: the general registration PRS (`assessment_stage = 'general_registration'`) is not read (owner decision 2026-10-12). Last 5 instances, any origin. Aggregate score + scale-wise breakdown, grouped by disease. Device-session items carry device + selected condition (`protocol_conditions`). Plus 5-week progress row (reuse `compute_weekly_trend`, weeks=5, one row per disease). |
| 5 | Doctor's notes | `patient_clinical_notes` | Only note ids the doctor ticked |
| 6 | Medication | `prescribed_medicines` | Full history, active + stopped |
| 7 | Treatment plan | `treatment_protocols` / `protocol_plan` + placements + dosing + `protocol_conditions` + `protocol_device_sessions` | Placement, dosage, schedule of sessions. `overall`: every protocol instance. `last_consultation`: the active/most recent one. |
| 8 | Session details | `device_sessions` + symptoms/adverse events child tables | Per session: date, status, actual vs prescribed intensity/duration, symptom + adverse-event counts. |
| 9 | Signature | **new** | `{typed_name, license_number, signed_at}` + content hash |

## 4. Schema (SQL 117, one table)

`core.final_reports` — rows exist only once signed.

| Column | Type | Note |
|---|---|---|
| `report_id` | UUID PK | |
| `patient_id` | UUID NOT NULL | FK `core.profiles(id)` (NOTES.md convention) |
| `clinic_id` | UUID NOT NULL | FK `core.clinics` |
| `doctor_id` | UUID NOT NULL | FK `core.profiles(id)` |
| `scope` | TEXT NOT NULL | CHECK `overall` / `last_consultation` |
| `appointment_id` | UUID NULL | the consultation used for `last_consultation`; FK `core.appointments` |
| `diagnosis` | TEXT NOT NULL | CHECK not blank |
| `snapshot` | JSONB NOT NULL | all sections frozen at sign time |
| `signature` | JSONB NOT NULL | typed name, licence no., font style, signed_at |
| `content_hash` | TEXT NOT NULL | sha256 of canonical snapshot + diagnosis + signature |
| `signed_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |

Lock = immutability enforced in the database:
- `anava_app` gets **SELECT, INSERT only**. No UPDATE, no DELETE grant.
- Trigger `BEFORE UPDATE OR DELETE` raises, so even a future grant mistake cannot edit a signed report.
- Audit trigger `ops.fn_audit_trigger('report_id')`, same as `prescribed_medicines`.
- Index `(patient_id, signed_at DESC)`, index `(doctor_id, signed_at DESC)`.
- RLS ENABLE + FORCE:
  - SELECT: super_admin / regional_admin / system; clinic_admin of own clinic; doctor of own clinic;
    patient where `patient_id = rls_user_id()`.
  - INSERT: doctor only, `doctor_id = rls_user_id()` and `clinic_id = rls_clinic_id()`.
- Retention: clinical, Bucket 2 (anonymise with patient, never hard-delete).
- Alembic: new revision after 0062. Note 111-116 have no revisions yet; check `alembic_version`
  on target RDS first (SQL Apply Order Trap).

Snapshot is JSON so the patient view and print always show exactly what the doctor signed, even if
anamnesis, notes, or protocols change later.

## 5. API (new module `app/modules/final_reports`)

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/patients/{patient_id}/final-report/preview?scope=overall\|last_consultation` | doctor | Live-assembled sections for the screen. Not stored. |
| POST | `/patients/{patient_id}/final-reports` | doctor | Body: `scope`, `diagnosis`, `note_ids[]`, `confirm_sign: true`. Re-assembles server-side (never trusts client snapshot), freezes, hashes, inserts, emits event. |
| GET | `/patients/{patient_id}/final-reports` | doctor, admins | List previous reports (metadata only). |
| GET | `/final-reports/{report_id}` | doctor, admins, owning patient | Full frozen report. |
| GET | `/me/final-reports?scope=&from=&to=&doctor_id=&sort=` | patient | My Results list with filter + sort. |

Rules enforced in the service:
- `confirm_sign` must be true, `diagnosis` non-blank → else 422.
- `note_ids` must belong to this patient (and this clinic) → else 403.
- Doctor must be assigned to the patient (`doctor_patient_assignments`) plus the existing
  ownership check pattern. App-layer check kept even though RLS is enforced.
- `last_consultation` with no completed consultation → 409 with a clear code.
- Outbox event `final_report_signed` → patient notification (existing outbox + notifications path).

Print: backend returns the frozen JSON; the browser prints it (frontend scope, no PDF library).
If a server-side PDF is wanted later it is an additive `pdf_s3_key` column, not a rework.

## 6. Build phases

| Phase | Work | Done when |
|---|---|---|
| 0 | This doc signed off | open questions answered |
| 1 | SQL 117 + alembic revision | applied to a scratch DB; RLS / grant / trigger verified (insert ok, update rejected, patient sees only own) |
| 2 | Pure assembler functions per section (no DB inside) + repository reads | unit tests pass for empty and full inputs |
| 3 | Router + service + schemas, event emit | endpoint tests pass |
| 4 | Tests: sign gate, note ownership, immutability, RLS, last-5 cap, empty sections, scope windows | CI green |
| 5 | Apply to RDS, smoke test via API | report signed and visible from patient token |
| 6 | Handoff doc for frontend: endpoints + JSON shape (services/endpoints only, no UI) | |

## 7. Resolved (defaults accepted 2026-10-12)

- **Q-A** Report also lists the protocol wizard's ICD-10 codes read-only under the typed diagnosis.
- **Q-B** `last_consultation` window = previous consultation date -> last consultation date. PRS, notes, sessions inside the window; treatment plan = protocol active then; medication = full history.
- **Q-C** Session details = counts + deviations only. CA free-text notes excluded (internal).
- **Q-D** Frontend prints the frozen JSON. No backend PDF (additive `pdf_s3_key` later if wanted).
- **Q-E** PRS block and 5-week row repeat per disease when a patient has several.

## 8. As built (2026-10-12)

Backend `app/modules/final_reports` (assembler / repository / service / router), SQL 117, alembic 0063,
relay handler `final_report_signed`. Frontend in `Combo/frontend`: doctor sign modal, shared report
document, patient My Results > Final Reports tab + report page, previous-reports list for doctor and
admins, notification deep link.

Decisions taken while building (all inside the locked plan, recorded so nobody has to rediscover them):

- **PRS aggregate is per sitting.** Each of the last 5 rows shows that one PRS's scales run through the
  disease's weight formula. `disease_composite_scores` was not used: it is an as-of-latest blend and
  would print the same number on every row. A scale with no stored percentage shows its raw value and
  leaves the aggregate blank.
- **5-week progress row uses PRS of any origin**, per disease. The doctor dashboard's weekly table uses
  device-session captures only, so the two can differ for a patient who also has consultation PRS.
- **Treatment Plan lists active and completed protocols.** Draft, cancelled and superseded (replaced by
  an amendment) versions are left out; sessions run under a superseded version still appear in Session
  Details.
- **Who may generate:** a doctor of the patient's clinic who is actively assigned to the patient, or was
  the doctor on one of the patient's consultations (covering doctor).
- **`last_consultation` window:** previous started consultation `started_at` (exclusive) to the last
  one's `completed_at`, or now while it is still open. Notes are the doctor's pick in both scopes; the
  picker pre-ticks the ones in the window.
- **Treatment Plan lock in the doctor workspace** now happens only when a report is actually signed.
  Before, just opening the old print dialog locked it.
- **Session Details** lists sessions that were run (completed or stopped early) only.

Not done yet: alembic 0063 is NOT applied to any database. `/final-reports` writes and lists return 500
until it is; the preview endpoint works without it.
