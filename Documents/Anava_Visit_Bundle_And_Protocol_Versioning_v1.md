# Visit bundle + protocol versioning — contract v1

For sign-off before schema/code. Mirrors the contract style of `FRONTEND_PROTOCOL_FLOW_ENDPOINTS.md`.

## Problem

Doctor portal shows a toggle strip per patient (Initial Consultation, Follow-up 1, Follow-up 2...) — frontend already builds this from the existing appointment list. Clicking a toggle has nothing to show: anamnesis, PRS, and protocol records are stored patient-wide today, not tied to which visit produced them. Separately, amending a protocol has no lineage — editing means cancel-and-reissue with no link to what it replaced.

## What's changing

### 1. New nullable `appointment_id` columns

- `core.anamnesis_assessments.appointment_id` → UUID, references `core.appointments`. Set when an anamnesis is captured/edited during a specific visit. NULL on all pre-migration rows (no lineage recoverable — historical anamnesis stays visible only via the patient-wide flat list, not attached to any toggle).
- `core.prs_assessment_instances.appointment_id` → same shape, same reasoning. Distinct from the existing `session_id` column (device-session specific) — this new column is the general "which visit assigned this" answer, works for any appointment type.

Both validated same-patient at write time (same pattern already used for `protocol_plan.authored_in_appointment_id`): the appointment must belong to the same `patient_id` as the record being created, or the write is rejected.

### 2. Protocol major.minor versioning

New columns on `core.protocol_plan`: `version_major INT NOT NULL DEFAULT 1`, `version_minor INT NOT NULL DEFAULT 0`.

Rule:
- **New protocol, no relation to any existing one** → next major number within its `instance_id` (episode of care), minor = 0. Displayed as `"1"`, `"2"`, `"3"`...
- **Amendment of an existing protocol** (doctor tweaks the current prescription) → caller passes `supersedes_protocol_id` = the protocol being amended. Server inherits its major, bumps minor by 1. Displayed as `"1.1"`, `"1.2"`... The amended-over row flips to `status = 'superseded'` in the same transaction — it stops being editable but stays readable for history.
- A protocol can only be superseded once — supersedes target must currently be `status IN ('draft','active')`. Attempting to amend an already-superseded/cancelled/completed row is rejected.

Example matching the walkthrough that motivated this: initial visit → protocol "1". Follow-up 1, slight edit → "1.1". Later, doctor starts a genuinely new protocol → "2". Edits to that → "2.1", "2.2", "2.3", "2.4". A further new protocol after that → "3".

Pre-migration rows: all default to `major=1, minor=0` — no real lineage before this ships, documented as a known limitation, not backfilled.

### 3. New read endpoint

`GET /patients/{patient_id}/visits/{appointment_id}/summary`

Returns everything tied to one visit:
- `registration` — only present when the visit's `appointment_type = 'initial'` (existing registration/intake data, unchanged, not visit-versioned since intake happens once).
- `anamnesis` — the row where `appointment_id` matches this visit.
- `prs_instances` — PRS assessment instances where `appointment_id` matches this visit.
- `protocols` — `protocol_plan` rows where `authored_in_appointment_id` matches this visit, each carrying its version label (`"N"` or `"N.M"`) and, for history, the full amendment chain walked back through `supersedes_protocol_id`.

**PRS and protocol carry forward; anamnesis does not.** If a visit has no PRS/protocol recorded specifically for it, the endpoint falls back to whatever was current as of that visit's date (most recent prior instance per PRS disease thread; the latest protocol version in the patient's open instance) and returns that instead, with `"inherited": true` on the record so the frontend can label it "carried from an earlier visit" rather than presenting it as this visit's own. This is a read-time fallback, not a copy — no new row is written just by viewing a visit. Anamnesis is different: it is genuinely per-visit, never inherited. The initial visit's is version 1; a follow-up's `anamnesis` field is simply `null` ("no anamnesis taken") unless the doctor actually captured one at that specific visit.

A doctor changes what's shown two ways, both through the existing create endpoints, no new write surface:
- **Take a new analysis / start fresh** — call anamnesis-start or PRS-start with `appointment_id` set to this visit. For PRS, this replaces the inherited fallback with this visit's own (`inherited: false`); for anamnesis, this is the only way a follow-up gets one at all.
- **Amend the inherited protocol** — call protocol-create with `supersedes_protocol_id` set to the inherited version's `protocol_id` (and `authored_in_appointment_id` set to this visit). The amendment becomes the new current version, appearing at this visit and inherited by every later visit until changed again.

Device-session (zap) appointments are NOT included in the toggle strip and have no `/summary` of their own — they stay reachable through the existing protocol-sessions list, nested under whichever consultation visit owns them.

## Doctor-facing behavior this enables

Doctor opens a patient, clicks Follow-up 1 → sees exactly what was captured/prescribed at that visit (or the fallback intake anamnesis if this is the initial visit). Doctor can:
- **Change** something recorded at this visit → re-submit through the existing create endpoints with `appointment_id` (anamnesis/PRS) or `supersedes_protocol_id` + `authored_in_appointment_id` (protocol) set — no new write endpoints, existing create paths gain the missing linkage fields.
- **Add new** → same create endpoints, just without `supersedes_protocol_id` for a protocol (starts a new major lineage) or a fresh anamnesis/PRS instance tagged to this visit.

## Explicitly out of scope for v1

- No numbering/labeling logic for the toggle strip itself (frontend already handles Follow-up N ordering from the flat appointment list).
- No toggle for individual device-session appointments.
- No backfill of historical protocol lineage or historical anamnesis/PRS appointment linkage — those simply won't show under any visit toggle in the summary view, only in the existing flat lists.

---
Sign-off needed before schema migration (`SQL/v1/59_visit_bundle_and_protocol_versioning.sql`) is written.
