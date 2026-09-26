# Data Capture Audit — full application, 2026-09-21

Supersedes `DATA_CAPTURE_GAP_REPORT.md` (2026-08-24). Method: live RDS DB (read-only, one rolled-back write test) + current code, all 17 modules, workers, core infra.

Evidence tags: **[LIVE]** verified by query against the real DB. **[CODE]** verified by reading the code myself. **[AGENT]** reported by a code-audit agent, not re-read by me. **[AGENT✓]** agent claim I spot-checked and confirmed.

Nothing in this report has been fixed yet. Fix batches are in section 4, tests in section 5, checklist in section 6.

---

## 1. Verdict

Data capture is **not** safe to call complete. The write path (tables, FKs, most module fields) is largely correct, and the protocol/device-session spine is internally consistent (0 mismatches on 12 cross-entity link checks). But there are seven ways data is currently being lost, silently dropped, or left unattributable, and none of them raise an error:

| # | What is silently failing | Blast radius |
|---|---|---|
| DC-01 | Notification pipeline has never run (0 notifications ever, 1,284 outbox rows unpublished) | every patient/staff notification |
| DC-02 | Payment writes made by a patient session are silently dropped by RLS (0 rows updated, no error) | reschedule payment carry-forward, cancellation refunds |
| DC-03 | Audit trigger on `anamnesis_assessments` has been **disabled** by hand | 32 of 35 clinical anamnesis records have no audit trail |
| DC-04 | `audit_logs.ip_address / request_id / clinic_id` are 100% NULL; no actor role stored | every audit row is unattributable to a request or IP |
| DC-05 | Rows have disappeared from audited tables with no DELETE audit | audit trail can be bypassed |
| DC-06 | System/bulk status changes skip the appointment audit log | 255 cancellations + 12 missed have no trail |
| DC-07 | Completed clinical answers can be rewritten, and response tables have no audit trigger | clinical record integrity |

Previously flagged items now **fixed** (closed): PRS scale↔instance link, cross-patient PRS-link check, waived-payment unblock, spoofable `POST /payments`, payments clinic scope, PatientRead fields, `session_id`-retirement leftovers in clinical.

---

## 2. Outlier register

### P0 — data currently lost or unrecorded

#### DC-01 Event relay never runs  [LIVE][CODE]
- **Evidence:** `ops.outbox_events` 1,284 rows, `published_at` NULL on all, `publish_attempts` 0 on all. `core.notifications` 0 rows ever. `app/main.py` lifespan starts only partition maintenance, hold sweeper, no-show sweeper. Dockerfile CMD is uvicorn only; docker-compose has no worker. `relay.out.log` shows one manual dev run on 2026-07-07.
- **RLS is not a blocker:** rolled-back test as the migration role: outbox `UPDATE` affected 1 row and `notifications INSERT` succeeded. Wiring the relay in is enough for it to work.
- **Consequences once wired (from code review) [AGENT]:**
  - 66 event types are emitted, only 11 have handlers. No handler for `appointment_slot_claimed` (while payment is bypassed this is the only event on claim, so the patient gets no confirmation), `doctor_allocated`/`doctor_auto_allocated`, `consent_*`, `eeg_*`, `device_session.*`, `treatment_protocol.*`, all store/inventory/admin/staff events, `payment_created/waived/status_changed`.
  - `_handle_appointment_cancelled` builds recipient `str(None)` for a protocol row with no doctor, which raises a UUID error and the notification is lost.
  - Redis publish happens inside the DB transaction with no try/except. A Redis error rolls back the notification insert, then the failure path marks the event published: notification dropped permanently. `redis_url` defaults to localhost.
  - `run_forever` has no top-level try/except (sweepers do). One DB blip kills the relay.
  - Drain SELECT has no `FOR UPDATE SKIP LOCKED`: N API instances would send every notification N times.
  - The 1,284 historical rows would replay as stale notifications (old SOS and cancellation notices) to real users.
  - `notifications.sender_id / clinic_id / expires_at` are never set. `clinic_id` drives the clinic_admin SELECT policy.
- **Fix:** see batch B1-a.

#### DC-02 RLS silently drops patient-session payment writes  [LIVE]
- **Evidence:** `rls_payments_update` USING allows only `super_admin, clinic_admin, system`. A patient-triggered `UPDATE payments` matches 0 rows and returns no error. In `compliance.audit_logs`, **zero** payment UPDATEs have ever changed `appointment_id`.
- **Proof on data:** all 5 reschedule chains (rescheduled_from → new row): the new row has `pay_on_new = 0`; 4 of 5 old rows still hold the payment. The new rows are then forced to status `paid` by code, so they show paid with no payment attached. This is why 3 `initial` appointments (`f3e32954`, `4916ea33`, `634dc87a`) are completed/no_show with no payment.
- **Refund consequence:** new row `2d9f887c` was cancelled by the patient, but its payment sits on the old row, so `record_cancellation_refund` finds nothing. `payments.cancellation_refund_percent/amount` are NULL on all 35 payments. No refund has ever been recorded.
- **Also suspect [CODE]:** `record_cancellation_refund` and any other payments UPDATE run under the patient session have the same trap.
- **Fix:** batch B1-b. Data repair: relink the 4 stranded payments to the latest row in each chain; decide the refund for `2d9f887c`.

#### DC-03 Audit trigger disabled on anamnesis_assessments  [LIVE]
- **Evidence:** `pg_trigger.tgenabled = 'D'` for `trg_audit_anamnesis_assessments`, all other audit triggers `'O'`. Table has 35 rows, audit has 3 inserts. Nothing in the repo disables it (grep of SQL, alembic, app for `DISABLE TRIGGER` is empty), so it was done by hand and never re-enabled.
- **Fix:** batch B2. Also find out who/why from RDS logs.

#### DC-04 Audit-log attribution columns are dead  [LIVE][CODE]
- **Evidence:** `compliance.audit_logs` 3,534 rows. `request_id`, `ip_address`, `clinic_id` NULL on 100%. Cause: `ops.fn_audit_trigger` INSERTs only `(table_name, operation, record_id, old_data, new_data, changed_by)` and never touches the three columns. There is no actor-role column at all.
- `changed_by` is NULL on a large share: appointments 568/1783 NULL, payments 175/233, profiles 116/209, patients 99/200, consent_records 44/101, anamnesis 6/6. Some are legitimate (sweepers, webhook) but with no actor-role column you cannot tell system from a lost user context.
- `compliance.activity_logs` (auth/activity events) has 0 rows: no writer exists.
- **Fix:** batch B2.

#### DC-05 Rows removed without a DELETE audit  [LIVE]
- Net inserts minus deletes in `audit_logs` vs live rows: appointments −67, patient_scale_assignments −126, prs_assessment_instances −52, consent_records −17, doctor_patient_assignments −14, payments −14, patients −12, protocol_diagnoses −3, device_sessions −2, protocol_plan −1. Row triggers fire even for FK cascades, so these went out via TRUNCATE or trigger-bypass (dev cleanup). `profiles` has 6 rows with no INSERT audit (loaded while triggers were off).
- Fine for pre-launch test data. Not fine to leave possible: **`ops.schema_migrations` is empty and hand-applied SQL is the norm**, so the same access can wipe audited clinical data untraced.
- **Fix:** batch B2 (TRUNCATE guard + baseline snapshot).

#### DC-06 System/bulk status changes skip `appointment_audit_logs`  [LIVE][CODE]
- `treatment_protocols/repository.py::cancel_planned` bulk `UPDATE ... status='cancelled'` writes no audit row: 255 cancelled protocol-born appointments (211 device_session, 44 protocol_followup) have zero rows. Called by protocol cancel and by amend/supersede.
- Migration 87's backfill `UPDATE` (12 rows, all stamped 2026-09-17 12:56:50) wrote no audit row. My omission. The 4 `missed` rows swept later have their audit row.
- `hold_sweeper` selected→planned revert (protocol-born) writes no audit row and no event [AGENT].
- Both sweepers do the audit INSERT and the UPDATE as two statements with the same predicate: a concurrent check-in/pay between them leaves an audit row that lies [AGENT].
- `cancelled_by` NULL on 255 of 260 cancelled rows.
- **Fix:** batch B1-c.

#### DC-07 Completed clinical answers are rewritable and unaudited  [CODE][AGENT]
- `prs_responses`, `anamnesis_responses`, `prs_scale_results`, `prs_final_results`, `protocol_instances`, `protocol_scales`, `protocol_conditions`, `protocol_device_sessions`, `protocol_followup`, `device_session_scales`, and all money reference config (`billable_items`, `platform_fee_config`, `cancellation_policy_tiers`, `clinic_weekly_hours`) have **no audit trigger**.
- `submit_responses` in prs and anamnesis has no status guard, so a completed instance/version can be edited and re-completed (overwrites `completed_at`, re-emits the event). Answers are not validated against the instance's scales/options; an unknown option stores `response_value` NULL silently.
- **Fix:** batch B1-d and B2.

---

### P1 — data captured but not mapped/linked correctly

#### DC-08 `protocol_instances` never leave `draft`  [LIVE][CODE]
3 of 3 instances are `draft` while their protocols are `active` (3), `completed` (1), `superseded` (9). `ProtocolService.activate` (`treatment_protocols/service.py:927`) only calls `repo.set_status` on `protocol_plan`; nothing advances the instance except a manual PATCH route or patient exit. The episode-of-care state is wrong for every patient.

#### DC-09 PRS/anamnesis not linked to the visit  [LIVE][AGENT]
Completed `main_clinical` PRS: 12 of 21 have `appointment_id` NULL, and unlinked ones continue through 2026-09-16, so it is not just old data. Anamnesis `main`: 2 of 17 unlinked. Cause: resuming an in-progress instance returns the existing row and ignores the new `appointment_id` (`prs/service.py:305-308`). The visit bundle then shows the record as "inherited".

#### DC-10 Device-session scale ↔ PRS link is inconsistent  [LIVE]
- 2 completed sessions (#6, #8) have a scale `completed` (with `prs_instance_id`) but **no** `device_session_prs_responses` row: two link mechanisms that can disagree.
- 2 completed sessions (#2, #2) have zero `device_session_scales` rows at all.
- Pending scales on completed sessions never expire (only `frozen` on protocol supersede): 4 pending, 4 frozen.
- `followup_prs_responses` is empty and the only completed `protocol_followup` has no PRS row. Needs a product rule: is follow-up PRS mandatory.

#### DC-11 Linking columns that are never written  [LIVE][CODE]
- `appointments.instance_id` NULL on 360/360. Appointments reach their episode only through `protocol_id`. `initial`/`follow_up` appointments have no episode link at all.
- `protocol_plan.authored_in_appointment_id` NULL on 13/13 (only set if the client sends it; it never does). Cannot answer "which consult produced this protocol".
- `prs_scale_results.scoring_version_id` NULL on 106/106 although `scoring_logic_versions` has 55 rows: results cannot be tied to the scoring logic that produced them (reproducibility).
- `prs_scale_results.percentage` 0/106, `prs_final_results.percentage/composite_summary` 0 populated (dead; `direction_corrected_percentage` is the live one, 68/106).
- `protocol_scales.scale_id` NULL 22/22 and `window_days` 22/22 (live column is `prs_scale_id`); `neuromod_scales.prs_scale_id` NULL 13/13.

#### DC-12 Duplicate active scale assignments  [LIVE]
`patient_scale_assignments`: 101 active rows, 78 distinct (patient, scale, stage): 23 duplicates. Same patient+scale inserted 2–4× on the same day. Only the primary key is unique (no `(patient_id, scale_id, stage)` index) and none has ever been deactivated. Audit shows 229 inserts for 101 live rows.

#### DC-13 Consent capture integrity  [LIVE][AGENT✓]
- `ip_address` NULL on 19/19 signed consents. It is read from the request body, not the connection.
- `witness_id` NULL on 19/19 (rule dropped, see old report #5). SQL comment still says patient_onboarding needs a witness.
- `router.py:76` defaults `signature_data=""`; none blank today (0 of 19) but nothing prevents it.
- `revoke_reason` accepted and dropped: no column exists.
- `sign()` sets `profiles.is_active=TRUE` on `record["staff_id"]` regardless of who signed, and `assert_owns_profile` is a no-op for staff: any staff role can activate another staff account. [AGENT✓ read `consent/service.py:159-160`]
- Patient transfer/exit `complete()` accepts any signed consent without checking it belongs to that patient or is the right type. `to_doctor_id` and exit `reason` are dropped.

#### DC-14 Retention / erasure pipeline never runs  [LIVE][AGENT]
`retention_purge.run_once` is not scheduled (only partition maintenance is). `patients.last_clinical_contact_at` and `retention_basis_cleared_at` are NULL on 13/13 because only `run_once` writes them. No route creates `erasure_requests`. Anonymisation leaves dob, city, state, pincode, government_id, guardian_*, emergency_contact_*.

#### DC-15 Historical `device_sessions.performed_by_*` NULL  [LIVE]
9 sessions (2026-08-28 to 2026-09-09) predate the columns. Fully recoverable: `device_session_events` has `actor_id/actor_role` on the `started` event for all 9.

#### DC-16 Payment ↔ appointment integrity  [LIVE]
- Duplicate order race: appointment `fbf2187b` (device session) has two payments created 1.6 s apart (`pending` at 11:23:11.8, `paid` at 11:23:13.5). `create_order` is meant to resume an in-progress order, but two concurrent calls both created one, and the abandoned `pending` row is never closed (1 stale pending payment on the DB). No unique guard on one open payment per appointment. Not a missing-payment problem: the appointment is correctly paid.
- The 3 `initial` appointments with no payment are all DC-02 casualties.

---

### P2 — code paths that let wrong data in (all [AGENT] unless marked)

#### DC-17 Payments (money path)
- **HIGH** Late Razorpay capture is dropped: `mark_paid` raises `NOT_AWAITING_PAYMENT` when the appointment is no longer `selected` (hold sweeper cancelled it, or staff cancelled). Webhook returns 4xx, Razorpay retries forever, money taken but payment stays `failed`/`pending` with no log row and no refund flag.
- Webhook never compares `entity.amount` to `payments.amount`.
- `set_status` sets `payment_method / waived_by / waived_reason` unconditionally: a PATCH without them nulls them (a `refunded` PATCH wipes the waiver trail). `waived_reason` optional even when waiving.
- No payment state machine: paid→failed→paid, refunded→paid all accepted; `refunded`/`failed` have no downstream effect.
- Cancellation refund records percent/amount only: `payments.status` stays `paid`, revenue reports keep counting it, no `payment_logs` row, no event. Refund tier ignores who cancelled (clinic/doctor cancel penalises patient).
- Minor: regional_admin can list any clinic's payments; `get_receipt_pdf` labels device sessions with the appointment price row.

#### DC-18 Store
`instance_id` never checked against `patient_id` (store-orders + device-assignments, still open from old report #3). **HIGH** `PATCH /store-orders/{id}/status` has no clinic scope and no role gate on `doctor_approved` [AGENT✓]. `quantity` has no `ge=1`; empty items allowed; inactive products accepted. Order and payment decoupled (dispatch with no paid payment); collected orders never decrement inventory; cancelling a device order leaves `purchase_status`. Dead: `device_assignments.order_id / purchased_at / returned_*`. Module is unused in the DB today (0 orders).

#### DC-19 Inventory
**HIGH** Stock created from nothing: `adjust()` clamps with `GREATEST(...,0)` and no sufficiency check, so receiving 10 from a source holding 3 adds 10 and zeroes the source [AGENT✓]. Double-receive race (no `WHERE status = old`). No scope on any route; any clinic_admin can dispatch/receive any transfer; `from == to` allowed; `main_branch` and `order_id` unverified. Old report called this "cleanest module": wrong.

#### DC-20 Admin / staff
`PATCH /clinics/{id}` still a bare SET exposing `clinic_admin_id` and `is_main_branch` (no role check, no `admins` sync, `false` unsets main branch, unhandled IntegrityError → 500). **HIGH** regional_admin not region-scoped on `create_clinic`, `assign_admin`, `change_status`, `delete`. Staff-assignment routes have no scope; `remove` leaves `doctors.clinic_id` and `clinics.clinic_admin_id` untouched. Clinic closure guard is `pass`. Approving a clinic request has no downstream effect. `POST /ca-doctor-assignments` still has no scope or ca/doctor/clinic agreement check; no unassign endpoint (`removed_at` never written; UNIQUE blocks re-add).

#### DC-21 Files / reception
No `assert_clinic_scope` on any files staff route: any doctor/CA/receptionist can read, upload, review any patient's file. `confirm` trusts a caller `s3_key` (only checks it exists; does not check the `/patients/{id}/` segment) and `clinic_id` from the body [AGENT✓ read `files/service.py`]. EEG review overwrites `clinical_findings/is_abnormal` with NULL when omitted. `mime_type` and `recording_notes` never captured; no DELETE route for medical-history files. Reception: receptionist can register into any clinic; mobile path never sets `phone_verified`; `consent_status:"signed"` returned while the row is pending; reject never stores `rejection_reason` (notification body null); Cognito user orphaned if DB write fails; `list_*` falls open to all clinics when `ctx.clinic_id` is falsy.

#### DC-22 Patients / PRS / anamnesis
Transfer/exit: `to_clinic_id` never validated; exit persists no patient state (does not end assignment or deactivate profile); no scope on transfer/exit routes. Staff `POST /patients` cannot register a minor (schema lacks `guardian_*`, service then raises `GUARDIAN_REQUIRED`). `taken_by` is a free string written to an enum (bad value = 500; a patient can claim `doctor_on_behalf`; doctor omitting it is recorded as patient). Anamnesis stage vocabulary differs between API (`registration/main`) and DB default (`general_registration/main_clinical`); `is_required` not enforced on complete. `assign_scale` does not check scale belongs to disease. Patient self-update can change email/phone with no Cognito sync and no reset of `*_verified`. `assert_patient_self` is a no-op for staff, so the app layer does not stop a staff member reading another clinic's patient. `anava_app` (`rolbypassrls = false`) **is** subject to RLS, so RLS is the backstop for app traffic (which is also exactly why DC-02 happens: the same RLS silently rejects the patient-session payment write). The migration role `postgres` is not subject to it (write test on `ops.outbox_events` / `core.notifications` succeeded with no role set). Keep the app-layer scope checks anyway as defence in depth, and never run app-tier code on the migration URL.

#### DC-23 Auth
Emergency contact never collected at signup (all 3 wizards). `verify-channel/confirm` writes `body.value` and sets `*_verified = TRUE` without tying it to the value the code was sent for. `PATCH /me` (reception, clinical) bypasses the staff email-domain check.

---

### P3 — empty tables, dead columns, catalog gaps  [LIVE]

30 of 103 tables never written: `notifications`, `store_orders`, `order_items`, `inventory`, `stock_transfers`, `device_assignments`, `ca_doctor_assignments`, `clinic_requests`, `patient_clinic_transfers`, `patient_eeg_files`, `patient_medical_history_files`, `device_session_media`, `device_session_sos_events`, `doctor_schedule_overrides`, `clinic_device_schedule_overrides`, `followup_prs_responses`, `compliance.activity_logs`, `reference.products`, plus retired `sessions` and `doctor_session_notes` (drop candidates), `ops.schema_migrations`, and 10 reference tables.

- **DC-24** Reference catalogs empty for 5 of 6 modalities: `hd_tdcs`, `tavns`, `tps`, `rtms`, `other` placements/dosing. Only tDCS is seedable today.
- Store, inventory, transfers, EEG/medical-history upload, SOS, media have never been exercised on this DB, so their findings above are latent, not observed.
- 100% NULL columns worth a decision (drop or wire): `appointments.reason / patient_complaint / notes`, `patients.referred_by`, `profiles.profile_photo_s3_key`, `doctor_weekly_schedules.max_appointments`, `notifications.*` (DC-01), `payments.session_id`, `prs_assessment_instances.session_id`.
- `prs_assessment_instances.session_id` still accepted by the API; its FK targets retired `core.sessions`, so any non-null client value is an FK error (500).

---

## 3. Confirmed clean  [LIVE]

| Check | Result |
|---|---|
| Protocol-born appointment patient = protocol_instance patient | 0 mismatches |
| `protocol_device_sessions` / `protocol_followup` ↔ appointments (protocol_id, session_number) | 0 mismatches, 278/278 and 58/58 |
| `device_sessions.protocol_id` = appointment.protocol_id | 0 mismatches |
| `device_session_prs_responses` patient/protocol/session = appointment | 0 mismatches |
| Completed/in-progress device sessions with no header row | 0 |
| Completed sessions with no events | 0 |
| `payments.amount = base_fee + platform_fee` | 0 mismatches |
| Appointments with >1 paid payment | 0 |
| `paid` payments with no `paid_at` | 0 |
| Profile role ↔ role-table row missing | 0 |
| appointment.clinic_id = patient primary clinic; doctor clinic = appointment clinic | 0 mismatches |
| `registration_complete` patients lacking signed consent / anamnesis / PRS | 0 |
| Completed PRS instances lacking `prs_final_results` | 0 |
| Multiple active protocols per instance; broken supersede chains | 0 |
| Duplicate MRN, duplicate email, patients with >1 active doctor | 0 |
| Doctor↔patient assignments | consistent (both sides use profile ids; the id spaces match) |
| Signed consents with blank signature | 0; content hash present 19/19 |
| Pause/resume timestamps | 1 pause event, no resume: consistent |
| Sweepers | hold + no-show + missed branch deployed and running (missed 12→16 with audit rows on the live-swept ones) |
| Migration role write test | outbox UPDATE and notifications INSERT succeed |

---

## 4. Fix plan (nothing applied yet)

### B1 — code only, no migration
- **B1-a Relay:** add `event_relay_enabled` setting and start `run_forever()` in `main.py` lifespan behind it. In `event_relay.py`: wrap `drain_outbox` in a never-die try/except; `SELECT ... FOR UPDATE SKIP LOCKED` (or advisory lock like the sweepers); commit the notification row **before** the Redis publish and wrap publish in try/except; fix `str(None)` recipient in `_handle_appointment_cancelled`; set `notifications.clinic_id`, `sender_id`, `expires_at`; add handlers for `appointment_slot_claimed`, `doctor_allocated`, `doctor_auto_allocated`, `consent_signed`, `device_session.completed`, `treatment_protocol.activated`, `payment_waived`, `eeg_reviewed`, and doctor-side notices for reschedule/cancel/no_show/missed. **Before enabling:** bulk-mark the 1,284 old rows published (or keep only the last N days; your call) so real users are not sent stale notices. Fix SSE `aclose()` leak.
- **B1-b Payment writes under RLS:** helper `run_as_system(session)` that saves `app.current_user_role`, sets `system`, runs, restores. Use it in `PaymentRepository.relink_appointment` and `record_cancellation_refund`. Make `relink_appointment` raise when it returns None (fail loud). Grep every patient-session `UPDATE payments`. Add the `refund` write: set payments status, `payment_logs` row, event. One-off data repair script for the 4 stranded payments and `2d9f887c`.
- **B1-c Audit completeness:** rewrite `cancel_planned` as `WITH upd AS (UPDATE ... RETURNING) INSERT INTO appointment_audit_logs ... SELECT FROM upd` (system actor, reason). Same shape in `hold_sweeper` revert path and in both sweepers to remove the race. Set `cancelled_by` NULL + `changed_by_role='system'` explicitly. Backfill audit rows for the 12 migration-87 rows.
- **B1-d Response guards:** reject `submit_responses` when the instance/version is `completed`; validate `question_id` ∈ instance scales and `given_response` ∈ options; on PRS/anamnesis resume set `appointment_id` when NULL; derive `taken_by` from `ctx.role`; require `appointment_id` (or auto-resolve today's active visit) for `main` stage.
- **B1-e Episode state:** `ProtocolService.activate` also sets the `protocol_instance` `active` when `draft`; `complete`/exit path completes it. Decide rule for instance close.
- **B1-f Shared scope helper:** one `assert_same_scope(patient=, clinic=, instance=, ...)` used by store, scheduling, consent, transfer/exit, staff, files, so the "caller-supplied id not checked against its parent" class is fixed once (this is the 5-then-8 place recurring bug).

### B2 — migration 88, audit hardening
- `ALTER TABLE core.anamnesis_assessments ENABLE TRIGGER trg_audit_anamnesis_assessments;` plus a baseline snapshot row per existing record so history starts from a known state.
- `ops.fn_audit_trigger`: also write `ip_address` (`app.client_ip`), `request_id` (`app.request_id`), `clinic_id` (`app.current_clinic_id`, already set in `db.py:88`), and add an `actor_role` column from `app.current_user_role`. Set `app.request_id` and `app.client_ip` in `db.py`/middleware. Set `system` in workers so NULL `changed_by` is explainable.
- New triggers: `AFTER UPDATE OR DELETE` on `anamnesis_responses`, `prs_responses` (answers are their own INSERT record, history is what matters); full audit on `prs_scale_results`, `prs_final_results`, `protocol_instances`, `protocol_scales`, `protocol_conditions`, `protocol_device_sessions`, `protocol_followup`, `device_session_scales`, `billable_items`, `platform_fee_config`, `cancellation_policy_tiers`, `clinic_weekly_hours`, `scoring_logic_versions`.
- Statement-level `BEFORE TRUNCATE` guard on audited clinical/money tables (raise unless a flag is set), and confirm `anava_app` has no TRUNCATE/DELETE beyond what code needs. Backfill `device_sessions.performed_by_*` from `device_session_events`.

### B3 — migration 89, integrity constraints
- `UNIQUE (patient_id, scale_id, assessment_stage) WHERE is_active` on `patient_scale_assignments`, after a dedupe keeping the earliest; `ON CONFLICT DO NOTHING` in the assigner.
- `compliance.consent_records.revoke_reason`; server-side `ip_address`; CHECK non-empty `signature_data` on signed rows.
- Stamp `prs_scale_results.scoring_version_id` at scoring time (code) and backfill the 106 by the version effective at `time_stamp`.
- Drop or wire: `authored_in_appointment_id` (auto-set from the authoring appointment), `appointments.instance_id` (auto-set for protocol-born rows from `protocol_plan.instance_id`).
- Reference seeds for the five other modalities, or hide those devices in the wizard until seeded.

### B4 — scope and validation batch (the P2 list)
Order: payments late-capture and amount check (money) → store/inventory sufficiency + scope → admin/staff scope → files/reception scope + `s3_key` prefix check → consent binding → patients transfer/exit → auth verify-channel.

### B5 — retention
Schedule `retention_purge.run_once` or mark the DPDP feature not live; add erasure intake route; complete anonymisation field list; write audit rows for purge actions.

---

## 5. Test cases

Types: **U** unit (no DB), **I** integration (DB), **SQL** invariant in `SQL/v1/_data_integrity_checks.sql` (expects 0 rows), **E2E** manual/API.

| ID | Type | Scenario | Expected |
|---|---|---|---|
| **DC-01** | | | |
| T01 | I | Insert an `appointment_booked` outbox row, run one drain pass | notification row exists, outbox `published_at` set |
| T02 | I | Two drains run concurrently on the same rows | each event produces exactly one notification |
| T03 | U | Handler raises for one event in a batch of 3 | other 2 delivered; failing one logged and marked failed with `publish_attempts` incremented, not silently dropped |
| T04 | I | Redis unavailable during publish | notification row still committed; publish failure logged |
| T05 | U | `appointment_cancelled` for protocol row with `doctor_id` NULL | no exception; patient notified |
| T06 | U | Every event type emitted in `app/` has a handler or is on an explicit allow-list | test fails when a new `emit_event` type is added without one |
| T07 | E2E | Patient claims a device-session slot with payment bypassed | patient gets a confirmation notification |
| T08 | E2E | Start app, no manual relay process | outbox drains within 10 s |
| **DC-02** | | | |
| T09 | I | Patient reschedules a `paid` appointment | payment row's `appointment_id` = new appointment; `pay_on_old = 0` |
| T10 | I | Patient reschedules a `no_show` appointment | payment moves to the new row |
| T11 | I | `relink_appointment` when RLS denies | raises, transaction rolls back, old row stays `paid` (not left `rescheduled`) |
| T12 | I | Patient cancels a paid appointment inside each refund tier | `cancellation_refund_percent/amount` set, `payment_logs` row written, event emitted |
| T13 | I | Doctor/clinic cancels a paid appointment | refund is 100%, not the time-tier % |
| T14 | SQL | No non-`rescheduled` appointment in `paid/checked_in/in_progress/completed/no_show` lacks a `paid`/`waived` payment | 0 rows |
| T15 | SQL | No appointment has a payment whose own appointment is `rescheduled` | 0 rows |
| **DC-03/04/05** | | | |
| T16 | SQL | Every audit trigger has `tgenabled='O'` | 0 disabled |
| T17 | I | Update a patient via API | audit row has `changed_by`, `actor_role`, non-null `ip_address`, `request_id`, `clinic_id` |
| T18 | I | Worker (sweeper) update | audit row `actor_role='system'` |
| T19 | SQL | For each audited table: live rows = audit INSERT − DELETE (from baseline) | 0 mismatches |
| T20 | I | `TRUNCATE core.appointments` as app role and as migration role | rejected by guard |
| T21 | I | Insert into an audited table with triggers enabled, then `SET session_replication_role=replica` attempt | blocked/alerted |
| **DC-06/07** | | | |
| T22 | I | `cancel_planned` on a protocol with N planned rows | N audit rows, `changed_by_role='system'`, `cancelled_by` NULL, reason present |
| T23 | I | Hold expiry on a protocol-born `selected` row | audit row + event, patient notified |
| T24 | I | Concurrent `mark_paid` during no-show sweep | audit rows equal actual status changes (no phantom row) |
| T25 | SQL | Every appointment not `planned` has ≥1 `appointment_audit_logs` row | 0 rows |
| T26 | I | Submit responses to a `completed` PRS instance / anamnesis version | 409, no rows changed |
| T27 | I | Submit a `question_id` not in the instance's scales / a `given_response` not an option | 422, nothing stored |
| T28 | I | UPDATE a `prs_responses` row directly | audit row with old/new data |
| T29 | I | Change a `billable_items` price | audit row |
| **DC-08..12** | | | |
| T30 | I | Activate a protocol whose instance is `draft` | instance becomes `active` |
| T31 | I | Complete/exit | instance `completed` |
| T32 | SQL | No `active`/`completed` protocol whose instance is `draft` | 0 rows |
| T33 | I | Start PRS for an appointment, leave, resume with the appointment id | instance `appointment_id` set |
| T34 | SQL | Completed `main_clinical` PRS and `main` anamnesis with `appointment_id` NULL after cutoff date | 0 rows |
| T35 | SQL | Every `completed` device-session scale has a matching `device_session_prs_responses` row (or the link is derived) | 0 rows |
| T36 | I | Complete a session with pending scales | scales moved to a terminal state per product rule |
| T37 | I | Assign the same scale twice to a patient/stage | second is a no-op, still 1 active row |
| T38 | SQL | No duplicate active `(patient_id, scale_id, assessment_stage)` | 0 rows |
| T39 | I | Score a PRS scale | `scoring_version_id` set to the effective version |
| T40 | I | Create a protocol with `authored_in_appointment_id` omitted from an in-consult call | derived from the doctor's active appointment |
| **DC-13** | | | |
| T41 | I | Sign consent | `ip_address` = connection IP, ignoring any body value |
| T42 | I | Sign with empty `signature_data` | 422 |
| T43 | I | Staff A signs staff_onboarding for staff B | 403 |
| T44 | I | Revoke with reason | `revoke_reason` persisted |
| T45 | I | Complete a transfer with another patient's signed consent | 403/422 |
| T46 | I | Transfer with `to_doctor_id` | persisted and used |
| **DC-14/15/16** | | | |
| T47 | I | Run `retention_purge.run_once` on a seeded patient | `last_clinical_contact_at` set; purge actions audited |
| T48 | I | Erasure request intake route | row created, status flow works |
| T49 | SQL | Every started device session has `performed_by_id/role` | 0 rows |
| T50 | I | Two concurrent `create_order` calls for one appointment | one open payment; second call resumes the first |
| T50b | SQL | No `pending` payment on an appointment that already has a `paid` one | 0 rows |
| **DC-17 payments** | | | |
| T51 | I | Webhook `payment.captured` for an appointment already cancelled by hold sweeper | payment `paid`, log row, refund flag; 200 to Razorpay |
| T52 | U | Webhook amount differs from `payments.amount` | not marked paid, mismatch logged |
| T53 | I | PATCH `refunded` on a waived payment | `waived_by/reason` preserved |
| T54 | I | Waive without reason | 422 |
| T55 | U | Illegal transitions (refunded→paid, failed→paid) | rejected |
| T56 | I | Cancel + refund | `payments.status` reflects refund; revenue summary excludes it |
| T57 | I | Every role × PATCH `/payments/{id}/status` | roles limited to those the RLS policy also allows; no silent 0-row |
| **DC-18..23 scope/validation** | | | |
| T58 | I | Clinic A staff PATCH clinic B store order status | 403 |
| T59 | I | Receptionist sends `doctor_approved` | 403 |
| T60 | I | Order with `quantity <= 0`, empty items, inactive product | 422 |
| T61 | I | `instance_id` of another patient on store-order / device-assignment / appointment / PRS link / ca-doctor | 422 in all five |
| T62 | I | Stock transfer receive when source stock < quantity | 409, no inventory change |
| T63 | I | Two concurrent `received` on one transfer | inventory incremented once |
| T64 | I | Clinic A admin dispatches clinic B's transfer | 403 |
| T65 | I | `PATCH /clinics/{id}` with `clinic_admin_id` / `is_main_branch` | rejected or routed through checks; collision → 409 not 500 |
| T66 | I | regional_admin creates/closes clinic in another region | 403 |
| T67 | I | `POST /ca-doctor-assignments` with mismatched clinics | 422 |
| T68 | I | Doctor in clinic A reads/reviews clinic B patient's file | 403 |
| T69 | I | Confirm upload with `s3_key` under another patient's prefix | 422 |
| T70 | I | EEG review PATCH with status only | `clinical_findings` preserved |
| T71 | I | Receptionist registers patient into another clinic | 403 |
| T72 | I | Reject registration with reason | stored and shown in notification |
| T73 | I | Staff registers a minor with guardian fields | succeeds |
| T74 | I | Anamnesis with `taken_by='foo'` | 422, not 500; doctor role ⇒ `doctor_on_behalf` |
| T75 | I | Patient changes email via self-update | verified flags reset and Cognito synced |
| T76 | I | `verify-channel/confirm` with a value different from the one the code was sent for | rejected |
| T77 | E2E | Signup wizards | emergency contact collected (or product decision recorded) |
| **Regression / global** | | | |
| T78 | SQL | Whole `_data_integrity_checks.sql` suite | every check returns 0 rows |
| T79 | I | Reschedule `missed`/`no_show` protocol-born row (staff) | same row moved, protocol_id/session_number kept, status per payment rule, audit + event written |
| T80 | E2E | Per-role smoke: patient, doctor, CA, receptionist, clinic_admin, regional_admin, super_admin each perform their core write | no silent 0-row updates (assert `rowcount`/returned row is not None on every critical UPDATE) |

Generic guard behind T11/T57/T80: critical UPDATEs in repositories return `fetch_optional(...)`; any caller that ignores a `None` is a silent-failure site. Add a `require_row()` wrapper and grep for callers that discard the result.

---

## 6. Master checklist

`[x]` verified good, `[ ]` open, `[~]` partial.

### Infrastructure
- [ ] Event relay running (DC-01)
- [ ] Relay hardened: poison, Redis, SKIP LOCKED, backlog
- [x] Hold sweeper running  [x] No-show + missed sweeper running
- [ ] Retention purge scheduled (DC-14)
- [ ] `ops.schema_migrations` / alembic reflects applied SQL (files 42-53, 87 hand-applied)
- [ ] All audit triggers enabled (DC-03)
- [ ] Audit rows carry actor role, IP, request id, clinic (DC-04)
- [ ] TRUNCATE/replica-role guard on audited tables (DC-05)
- [ ] `compliance.activity_logs` has a writer

### Data capture by module
| Module | Writes correct | Links correct | Audited | Scoped | Open items |
|---|---|---|---|---|---|
| auth | [~] | [x] | [x] | n/a | DC-23 |
| consent | [~] | [~] | [x] | [ ] | DC-13 |
| anamnesis | [~] | [~] | [~] trigger disabled, responses unaudited | [ ] | DC-03, 07, 09, 22 |
| prs | [~] | [~] | [~] | [ ] | DC-07, 09, 11, 12 |
| patients | [~] | [x] | [x] | [ ] | DC-14, 22 |
| treatment_protocols | [x] | [~] | [~] | [x] | DC-08, 11 |
| scheduling | [x] | [~] | [~] | [x] | DC-02, 06, 11 |
| device_sessions | [x] | [~] | [x] | [x] | DC-10, 15 |
| payments | [~] | [~] | [~] | [x] | DC-02, 16, 17 |
| store | [~] | [ ] | [x] | [ ] | DC-18 (unused today) |
| inventory | [ ] | [ ] | [ ] | [ ] | DC-19 (unused today) |
| admin | [~] | [ ] | [x] | [ ] | DC-20 |
| staff | [~] | [ ] | [x] | [~] | DC-20 |
| files | [~] | [ ] | [x] | [ ] | DC-21 (unused today) |
| notifications | [ ] | n/a | [ ] | [x] | DC-01 |
| reception | [~] | [x] | [x] | [ ] | DC-21 |
| clinical | [x] | n/a | [x] | [x] | none |
| workers | [~] | n/a | [~] | n/a | DC-01, 06, 14 |

### Cross-entity invariants (all in `_data_integrity_checks.sql`)
- [x] protocol-born patient = instance patient
- [x] mirror tables ↔ appointments
- [x] device_session header/protocol/PRS-link consistency
- [x] payment amount = base + fee; ≤1 paid payment per appointment
- [x] role ↔ role-table rows; clinic consistency; MRN/email unique
- [ ] payment ↔ appointment presence (DC-02)
- [ ] one open payment per appointment (DC-16)
- [ ] audit trigger enabled + audit rows for every non-planned appointment (DC-03, 06)
- [ ] episode state consistent (DC-08)
- [ ] visit linkage of PRS/anamnesis (DC-09)
- [ ] no duplicate active scale assignments (DC-12)
- [ ] scale-completed ⇒ PRS link row (DC-10)
- [ ] every started device session has performer (DC-15)

Re-run any time: `python scripts` equivalent is the SQL file; every check returns rows only when violated.
