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

- **PRS aggregate** (superseded 2026-10-13, see section 10): each row's score was first computed from
  that one sitting's own scales only.
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

## 9. Report template (2026-10-13)

The report now follows the "Patient Medical Report and Treatment Plan" template package
(`anava-patient-report-template`, design language of Form ACPL-PT v2.0). Frontend only; the snapshot
and the API are unchanged.

- `Combo/frontend/src/components/reports/final-report.css` is a port of the package's
  `patient_report.css` (same class names and tokens, scoped under `.anava-report`).
  `FinalReportDocument.tsx` mirrors `patient_report.html.j2`. Logos and fonts (Lato, Gelasio, OFL)
  live in `src/assets/report/`.
- Report ID (superseded 2026-10-13, see section 10): was first the leading 16 characters of `content_hash`.
- An unsigned preview carries the DRAFT watermark and an "Awaiting signature" seal.
- All dates print in IST, whatever the viewer's timezone.
- **Printing stays in the browser**, not the server-side WeasyPrint render the package's README asks
  for. WeasyPrint needs Pango/GTK and does not run on the Windows dev machines (checked: it fails to
  load `libgobject`). The running header (pages 2+) and the "Page X of Y" footer are CSS page-margin
  boxes, which Chrome and Edge 131+ print; other browsers print the report without them.
  Server-side rendering remains the way to get one stored PDF per Report No.; it needs the system
  libraries added to the backend Docker image and a way to run it locally.
- Differences from the reference that the browser route forces: in the running header the patient
  name is not bold, and the footer has no rose dot before "Page".
- Dropped because the template has no place for them: the full-page logo watermark (the template
  uses the faint arcs behind the title) and the handwriting-style signature (the template uses the
  "Electronically signed" seal).
- Template fields we have no data for yet: patient age, doctor qualification and registration
  council, clinic email, a signature image.

## 10. Owner changes (2026-10-13)

- **Report ID = `final_reports.report_id`**, the ID the report is stored under. Shown in the footer
  only, in small type: under the footer line of every printed page, and at the foot of the sheet on
  screen. Not in the patient panel or the signature block (owner, 2026-10-13). It exists only once the
  report is signed, so a preview's printed footer reads "Draft — not signed".
  `content_hash` is still stored as tamper evidence but is no longer printed.
- **Letterhead:** the CIN / Udyam line is removed. Company name and contact line stay.
- **[Superseded 2026-10-14, see section 11]** **PRS rows list every scale.** A sitting often answers only some scales (a device session, one or
  two). Each row now shows all of the disease's scales: the ones answered in that sitting, and for
  each other scale the patient's latest earlier result, marked "from <date>". The row's overall score
  is the weighted composite of those, i.e. the patient's composite as of that sitting — the same
  as-of-latest-per-scale rule the disease composite uses, evaluated at the sitting's date, so a later
  result never changes an earlier row. Results carried forward may come from before a
  `last_consultation` window. Registration PRS is still never used as a source.
  Snapshot change: each scale gains `carried` and `taken_at`. Reports signed earlier do not have them
  and print as before.
- **Weekly progress:** each week shows the score as "/100" with the change from the previous week
  below it (minus = improvement, since lower is better; green for better, rose for worse).
- **Section titles:** 07 is "Treatment protocol", 08 is "Device session details".
- **Duplicate device-session PRS (data issue, not a report bug).** Before commit 67735a3 (2026-09-23)
  opening a session's PRS again created a new PRS instead of reopening the first, so a scale answered
  three times left three completed PRS and the report printed three rows for one session.
  2026-10-13: the leftovers on the dev DB (`Anava_App_v1`) were voided with
  `SQL/v1/_fix_void_duplicate_session_prs.sql` (2 PRS, one patient, session #10; the last attempt is
  kept). Voided PRS are now also left out of the scores-summary lists and are never resumed
  (`prs/repository.py`). Still open: one other session holds two full PRS that are BOTH linked to
  session records (left alone, needs a decision); `disease_composite_scores` keeps the composite rows
  computed from the voided attempts (the doctor dashboard still shows them); and the database has no
  unique rule for one PRS per appointment + condition, only the code check. Other environments have
  not been cleaned.
- **PRS is reported in two kinds** (owner, 2026-10-13).
  - *Main PRS* (consultation or standalone): grouped under its condition, with the overall score and
    weekly progress, as before.
  - *Device session PRS*: its own block labelled "Device session PRS", with the protocol's device and
    conditions beside it. One row per session (a session's scales are merged even when they were
    saved as separate PRS), scale-wise scores only, no overall score, and weekly progress per scale in
    the scale's own points with a grey +/- change.
  - Why: a protocol prescribes individual scales. When one is opened, the frontend files it under the
    first catalogue condition, alphabetically, that contains the scale
    (`prsAssessment.service.ts` `resolveDiseaseAndScaleId`), so BDI-II for a depression protocol is
    stored under "ALS". That label is not printed and no composite is computed from it.
    The stored `disease_id` itself is NOT fixed: the doctor dashboard and the patient's My Results
    still show those PRS under "ALS" / "ADHD".
  - Snapshot change: `prs.device_sessions` (new). Reports signed earlier have none and print as before.
  - A PRS is a device-session PRS only when it was itself started at a device-session appointment.
    A standalone main PRS that a session record merely points at stays the main PRS of its condition.
    Checked 2026-10-13 on the dev DB, `overall` scope, 20 patients: 17 main PRS in the database = 17
    rows shown, 13 device sessions with PRS = 13 rows shown, and all 3 patients who have both kinds
    get both blocks. Each block still lists at most the last 5 rows per condition / per protocol.
- **Later the same day (owner):**
  - Device-session PRS is one block per **protocol instance**: "Device session PRS · Instance 1",
    "· Instance 2", ... (`protocol_instances.instance_number`), with the device and conditions beside
    it. Every instance among the report's treatment protocols gets a block, also with no PRS yet
    ("No PRS recorded at this device's sessions yet."). Treatment protocol cards carry the same
    "Instance n" and are listed in that order.
  - Device-session weekly progress is ONE row, not one per scale. Its number, and the Overall column of
    each session row, is the plain average of the session's scales (direction-corrected %; `assembler._session_score`). Equal weights on purpose: no condition formula
    applies. A scale stored with 0 % pulls the average down.
  - Main PRS shows the last 3 per condition (was 5); device sessions the last 5 per instance. Main PRS
    blocks print before the device-session blocks.
  - Watermark: the Anava logo in the centre of every page (the arcs behind the title are gone).

## 11. Final Report as a workspace section, with per-section picking (2026-10-14, leadership request)

What leadership asked for: the Final Report is no longer a pop-up window. It is a section of the
doctor's patient workspace, laid out like the Treatment Plan section (header, action buttons, cards).
Printing still produces the "Patient Medical Report and Treatment Plan" template. Every section card
has a pencil icon; it opens the patient's full history for that section, and the doctor ticks what
goes into the report.

No database change: the selection only changes what is frozen into `final_reports.snapshot`.

### API
- `POST /patients/{id}/final-report/preview` (replaces the GET) — body `{scope, selection?}`. Returns
  the sections built with the selection, the effective `selection`, and `candidates` per section
  (everything the doctor can pick from). Nothing is stored.
- `POST /patients/{id}/final-reports` (sign) — body gains `selection`. The server rebuilds the
  sections from it; the client never sends report content.

### Selection (every key optional; missing = the scope's default)
| Key | Section | Picks from | Default |
|---|---|---|---|
| `anamnesis_ids` | 01 Anamnesis | every consultation anamnesis | overall: all, last consultation: that visit's |
| `prs_ids` | 04 PRS, main | every main PRS record (any condition) | last 3 per condition in the window |
| `session_prs_ids` | 04 PRS, device sessions | every device-session PRS record (PRS instance id) | last 5 per instance in the window |
| `note_ids` | 05 Doctor's notes | every clinical note | notes in the window |
| `medicine_ids` | 06 Medication | every prescribed medicine | all |
| `protocol_ids` | 07 Treatment protocol | every protocol except draft / cancelled (superseded versions included) | overall: active + completed; last consultation: active |
| `session_ids` | 08 Device session details | every run device session | sessions in the window |

- An id that is not the patient's is simply not found and has no effect (the candidate lists are
  built from the patient's own records). Notes keep the explicit 403 for a foreign note.
- Weekly progress rows always use all of the patient's PRS, whatever rows are ticked.
- ICD-10 codes under the diagnosis follow the ticked protocols. Device-session PRS blocks are shown
  for the ticked protocols' instances.
- 02 Diagnosis: its pencil lists the diagnoses of the patient's previous signed reports; picking one
  fills the box. 03 Brain mapping has no pencil (not built). 09 Signature has none.

### Screen
- Sidebar "Final Report", the banner buttons and "Convert to Final Report" open the section; the
  pop-up is removed.
- Header: title, Draft / Signed pill, scope switch (Overall / Last consultation), Sign & Print.
- One card per report section, in report order, summarising what is ticked, pencil top-right.
- Bottom: confirm-signature checkbox and the previous signed reports.
- After signing: cards read-only, "Print" prints the signed snapshot in the template.
- 2026-10-14: the anamnesis and PRS pencils show every earlier record in full, not just its date:
  each anamnesis with all its questions and answers, each PRS (main and device session) with its
  overall score and every scale. The candidates carry these (`candidates.anamnesis[].sections`,
  `candidates.prs[] / session_prs[]` `score`, `severity_label`, `scales`).
- 2026-10-14 (owner): **PRS is reported record by record.** Every row, in the report and in the
  pencil, is one PRS (`prs_assessment_instances` row) with only the scales answered in it. No
  carrying earlier results into a row ("from <date>" is gone) and no merging the PRS of one device
  session into one row. The pencil lists every PRS record of the patient, completed and in
  progress. Defaults print completed PRS only; an in-progress PRS is printed only when ticked and is
  marked "In progress". Weekly progress counts completed PRS only. Still excluded: voided PRS
  (duplicates) and the registration PRS. `session_prs_ids` are now PRS instance ids. Reports signed
  before this keep their snapshot (rows with `carried` scales still print the "from" date).
