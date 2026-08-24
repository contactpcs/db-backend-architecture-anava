# Data Capture Gap Report

Companion to `Documents/Data_Capture_Verification_Plan_v1.md`. **Full coverage: all 17 wired modules verified (auth, consent, anamnesis, prs, patients, treatment_protocols audited directly; scheduling, device_sessions, clinical, payments, store, inventory, files, notifications, reception, admin, staff audited by 3 background agents, every confirmed finding spot-verified against the actual code before being logged here).** `reports`/`audit` remain unbuilt stubs (`__init__.py` only) — confirmed, out of scope for this audit since there's no code to check.

Audit date: 2026-08-24.

## Executive summary

**11 real, confirmed findings.** Ranked by severity. All were verified by reading the actual code (router → service → repository → DB schema), not inferred.

### Money-path (highest priority — fix first)

1. **Spoofable payment amount + no clinic scope** — `POST /payments` (generic create). Any staff role can submit an arbitrary `order_id` + arbitrary `amount`; nothing checks the order belongs to their clinic or that the amount matches the real order total. `GET /payments/{id}`, `GET /payments?clinic_id=`, `PATCH /payments/{id}/status` have the same missing-clinic-scope problem — a clinic_admin/receptionist can view or mark-paid/waive/refund another clinic's payment. → `payments` section.
2. **Waived payments never unblock the appointment** — `PATCH /payments/{id}/status` with `status='waived'` only has a branch for `status='paid'`. A waived visit stays stuck at `selected` forever, can't reach checked-in/completed. → `payments` section.

### Recurring pattern — same bug, 5 separate places (fix once, not five times)

3. A caller-supplied ID that should be scoped to the same patient/clinic as its parent resource is accepted with no cross-check, in 5 places found independently across 3 separate audits:
   - `treatment_protocols`: `POST .../prs/device-session` and `/prs/follow-up` — linked PRS `instance_id` never checked against the appointment's patient.
   - `scheduling`: `POST /appointments` — `instance_id` never checked against `patient_id`.
   - `store`: `POST /store-orders` and `POST /device-assignments` — `instance_id` never checked against `patient_id`.
   - `staff`: `POST /ca-doctor-assignments` — `ca_id`/`doctor_id`/`clinic_id` never checked against each other for clinic agreement.
   - `admin`: `PATCH /clinics/{id}` — `clinic_admin_id` accepted with no role check, no "already assigned elsewhere" check, no sync to the `admins` table; `is_main_branch` can be flipped outside the one-per-region rule and crashes as a raw 500 (unhandled `IntegrityError`) instead of a clean 409 when it collides.
   - Recommend a shared fix (e.g. one reusable "assert same patient/clinic" helper called from all 5 sites) rather than five one-off patches — same root cause every time.

### Design decisions needed from you (not bugs — need a call)

4. **Emergency contact never collected at patient signup** — the 3 self/receptionist-signup wizards (`auth` module) don't ask for `emergency_contact_name/phone`, even though the column and full write path exist (used only by the direct staff-create route and later PATCH edits). Your call: add to the wizard, or intentionally collect later at the desk?
5. **Consent witness requirement appears to have been fully dropped** — `witness_id` used to be enforced for certain consent signings (per older project notes); current code has no enforcement anywhere, for any consent type. Your call: intentional (most patients self-register now) or a lost safeguard?

### Confirmed functional gaps (real, lower severity than money-path)

6. **PRS scale-to-response link never wired up** — `device_session_scales.prs_instance_id` is meant to link a due PRS scale to the patient's actual answered assessment (per the column's own DB comment) but is never written by any code path. `status` never advances past `'pending'`. The scale-delivery tracking this table exists for stops at "seeded, mode chosen."
7. **`patient_medical_history_files` has no delete endpoint** — `MedicalHistoryFileRepository.soft_delete` is fully written but nothing calls it; no DELETE route exists in `files/router.py`.
8. **`patient_medical_history_files.mime_type` never captured** — collected at the presign step (`content_type`) but never threaded through to the confirm step that actually writes the row.

### Vestigial / dead columns (cleanup-list candidates, not bugs — none of these lose data that was ever supposed to be there)

- `prs_assessment_instances.session_id`, `payments.session_id` — leftover from the retired `core.sessions` table (commit `b341817`, 2026-08-20), confirmed 0 real usage.
- `patient_eeg_files.report_s3_key/report_file_name/report_file_size/report_checksum/report_checksum_algorithm/superseded_by` — unbuilt feature surface (no radiology-report/versioning endpoint exists at all).
- `patient_eeg_files.cycle_id/session_id` — pre-appointments-spine leftovers, same root cause as above.
- `notifications.sender_id/clinic_id/expires_at` — the only writer (`event_relay.py`) never sets these; `expires_at` looks intended for a TTL/cleanup job that was never built.
- `device_assignments.order_id` — meant to link a device-purchase prompt back to its fulfilling order, never written.
- `device_assignments.purchase_status='purchased'` value + its `purchased_at` — unreachable; the only real transition skips straight from `purchase_prompted` to `collected`.
- `device_assignments.returned_at/returned_by/return_reason` — no return/exchange flow exists anywhere in the app.
- `doctor_weekly_schedules.max_appointments` — captured on write, never read anywhere (slots are exclusive, not counted) — confirm intentional or drop.

### Confirmed clean (no gaps found)

`anamnesis`, `patients`, `clinical`, `inventory`, `notifications` (aside from the 3 dead columns above), `reception` (inherits #4 only, not a new gap). `treatment_protocols` and `scheduling` are otherwise well-guarded — the instance_id gaps above are their one real issue each.

### Old stale-memory items re-checked and corrected here

- `PatientRead` missing name/email/phone — **fixed since**, no longer true (was accurate 48 days ago, isn't now).
- Doctor API gap audit (48 endpoints) — not re-verified in this pass (different audit scope), flagged separately in existing memory.

---

## Module-by-module detail

## auth (`app/modules/auth/`)

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| GET /config, GET /clinics, POST /login, /login/new-password, /forgot-password/* | — (Cognito only / read-only) | N/A | `auth/router.py` | No DB write expected, none found. Correct. |
| POST /register, POST /patients/signup/complete, POST /patients/receptionist-signup/complete | profiles + patients (name/dob/gender/address/city/state/country/pincode/primary_clinic_id/self_registered/approval_status/cognito_sub/registered_by/guardian_*) | Y | `auth/router.py:82-227,309-326` → `patients/service.py:97-120` → `patients/repository.py` | Confirmed full write path. |
| POST /register, /signup/complete, /receptionist-signup/complete | patients.emergency_contact_name / emergency_contact_phone | **PARTIAL — OPEN QUESTION** | `patients/repository.py:26-27,92,100-101` supports the write; `auth/schemas.py` `PublicPatientRegister`/`PatientSignupComplete` never collect these two fields | Column is real, write path exists and works (confirmed via direct staff `POST /patients` and PATCH). But none of the 3 signup wizards in `auth` module ask for it — always NULL at signup time. Need user call: collected later at intake by design, or should the wizard ask? |
| POST /patients/verify-channel/confirm | profiles.email_verified / phone_verified / email / phone | Y | `auth/router.py:363-373` | Direct write, correct. |
| GET /me | — (read-only) | N/A | `auth/router.py:409-476` | Correct, no write expected. |

## payments (audited by background agent, spot-verified) — highest-severity findings in this pass

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| POST /appointments/{id}/payments/order, /verify, GET /receipt, POST /webhooks/razorpay | payments (full field set), appointments.status via mark_paid | Y | `payments/service.py:56-311`, `scheduling/service.py:1216-1247` | Correct, cleanly retargeted off retired `treatment_sessions` per commit b341817, idempotent, webhook correctly filters to `payment.captured`/`order.paid` only. |
| **PATCH /payments/{id}/status, status='waived'** | appointments.status | **GAP-CONFIRMED (spot-verified)** | `payments/service.py:140` only branches on `status == "paid"` — no `waived` case calls `mark_paid` | Waiving a payment never unblocks the appointment. It stays stuck at `selected` forever and can never reach checked_in/in_progress/completed, even though the receipt PDF and this method's own docstring both treat `waived` as payment-complete. A waived visit is unbookable downstream. Fix: treat `waived` the same as `paid` in this branch. |
| **POST /payments (generic create_payment)** | payments.order_id/amount/currency | **GAP-CONFIRMED (spot-verified) — spoofable amount, no clinic scope** | `payments/router.py:20-22` — no `assert_clinic_scope`, only `require_role`; `service.py:31-54` takes `amount` verbatim from the client with no server-side recompute (contrast `create_order`'s `_resolve_amount`) | Any staff role can POST an arbitrary `order_id` + arbitrary `amount` with nothing verifying the order belongs to their clinic or that the amount matches `store_orders.total_amount`. Neither app code nor RLS (`rls_payments_insert` permits any of the 6 staff roles regardless of clinic) narrows this. Real money-path gap. |
| **GET /payments/{id}, GET /payments?clinic_id=, PATCH /payments/{id}/status** | — (scope) | **GAP-CONFIRMED — missing clinic scope on the older generic endpoints** | `router.py` never imports `assert_clinic_scope` for these; contrast the newer `_get_appointment_for_pay` path which does call it | A clinic_admin/receptionist can view or mark-paid/waive/refund a payment belonging to a DIFFERENT clinic's order. `list_payments` only force-scopes when `ctx.role == "clinic_admin"` — doctor/CA/receptionist can pass any `clinic_id`. |
| `PaymentCreate.session_id` | payments.session_id | Vestigial, not a new bug | Same root cause as `prs_assessment_instances.session_id` (already logged) | `core.sessions` retired code-side, nothing populates a real session; field carried by the generic POST but never exercised by the real appointment-payment flow. |
| `get_receipt_pdf` item-name lookup | (read-path, no DB write) | Cosmetic, not a capture gap | Always queries `category="appointment"` even for device_session appointments, silently falls back to a title-cased type string instead of the real catalog price row | Display-only miss, flagged for awareness. |

## store (audited by background agent, spot-verified)

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| POST /products, /store-orders | products.*, store_orders + order_items full field sets | Y | `store/service.py:31-88` | Correct, server computes total_amount from catalog price (not client-spoofable), clinic scope IS checked here (unlike payments above). |
| **POST /store-orders, /device-assignments — `instance_id` field** | store_orders.instance_id / device_assignments.instance_id | **GAP-CONFIRMED — same cross-patient class as treatment_protocols/scheduling/staff findings above** | `store/service.py:48-152` | `instance_id` caller-supplied, never checked against the resolved `patient_id`. FK only proves the protocol_instances row exists, not that it's this patient's. Fourth occurrence of this exact bug pattern across the codebase (treatment_protocols PRS-link, scheduling appointments.instance_id, staff ca_doctor_assignments, now here) — **strongly suggests a shared root cause worth fixing once, not four separate patches.** |
| PATCH /store-orders/{id}/status | store_orders.status/approved_by/cancelled_by/cancelled_at, device_assignments.purchase_status | Y | `service.py:99-126` | Correct FSM, device-approval-gate vs accessory-skip confirmed still accurate. |
| — | device_assignments.order_id | **GAP-CONFIRMED (dead column)** | Column exists to link a device-purchase prompt back to its fulfilling order (FK'd), never written anywhere — always NULL. | |
| — | device_assignments.purchased_at, purchase_status='purchased' | **GAP-CONFIRMED (dead status value)** | Only transition ever driven from code is `purchase_prompted → collected` directly — the intermediate 'purchased' state is unreachable. | |
| — | device_assignments.returned_at/returned_by/return_reason | **GAP-CONFIRMED (dead columns)** | No return/exchange flow exists anywhere in the app. | |

## inventory (audited by background agent, spot-verified)

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| GET /inventory, POST /stock-transfers, PATCH .../status | stock_transfers full field set, inventory.quantity adjustment | Y | `inventory/service.py:29-85` | Correct, 3-tier hierarchy logic verified against table shape, `from_type` cross-field validation enforced. |

**Inventory: no gaps, cleanest module in this batch.**

## files (audited by background agent, spot-verified)

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| POST /patients/{id}/files (eeg, medical_history confirm steps) | patient_eeg_files / patient_medical_history_files, full field sets each schema collects | Y | `files/service.py:35-62` | Correct for what's collected. |
| POST /patients/{id}/files | patient_medical_history_files.mime_type | **GAP-CONFIRMED (dead column)** | `FileConfirmCreate` schema never carries `content_type`/`mime_type` even though the earlier presign step collects it (`PresignUploadRequest.content_type`) — never threaded through. Real column, `05_tables_core.sql:569`, always NULL. | |
| — | patient_eeg_files.report_s3_key/report_file_name/report_file_size/report_checksum/report_checksum_algorithm/superseded_by/cycle_id/session_id | **GAP-CONFIRMED (dead columns)** | Zero references anywhere in app code | `cycle_id`/`session_id` are pre-appointments-spine leftovers (same vestigial pattern as `prs_assessment_instances.session_id`, already logged). The 5 `report_*`+`superseded_by` columns are unbuilt feature surface (no radiology-report/versioning endpoint exists at all), not a capture bug. |
| — (no route exists) | patient_medical_history_files.is_deleted/deleted_by/deleted_at | **GAP-CONFIRMED (unreachable code)** | `MedicalHistoryFileRepository.soft_delete` exists (`repository.py:147-155`) but no DELETE route wires it up | Dead method, dead columns in practice. |
| PATCH /eeg-files/{id}/review, GET endpoints | patient_eeg_files.reviewed_by/clinical_findings/is_abnormal/status/reviewed_at | Y / N/A | `service.py:104-121` | Correct. |

## notifications (audited by background agent, spot-verified)

Read + mark-read only by design — the actual writer is `app/workers/event_relay.py`, not this module (architecturally correct, not a gap).

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| GET /notifications, /unread-count, PATCH /read, GET /events/stream | notifications.is_read/read_at (write); rest read/SSE | Y / N/A | `router.py:19-85` | Correct. |
| (writer: event_relay.py, not this module) | notifications.sender_id/clinic_id/expires_at | **GAP-CONFIRMED (dead columns)** | `event_relay.py:85-118` only ever sets recipient_id/type/title/body/entity_type/entity_id | Real columns, always NULL/default. `expires_at` looks intended for TTL/cleanup that was never built. |

## reception (audited by background agent, spot-verified)

Thin adapter over patients/staff/notifications services (already audited). No new gaps of its own beyond inheriting the auth-module emergency-contact gap (same root cause, not counted twice).

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| POST /patients, PATCH /me, /registrations/*, /notifications/*, GET /doctors, /enums | delegates to already-audited services; profiles.first_name/last_name/email/phone on /me | Y | `router.py` | Correct. GET /patients/{id} has no app-level clinic-scope check but RLS (`rls_patients_select`) is a confirmed real backstop here (app role does NOT bypass RLS per earlier RDS cutover fix), not a gap. |

## admin (audited by background agent, spot-verified)

Memory notes reconfirmed still true (2-step clinic creation, main-branch auto-set, regional_admin-must-be-main-branch).

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| POST/PATCH /billable-items, /regions(+assign-admin), /clinics(+assign-admin/status/delete), /clinic-requests(+decision), /staff-assignments, /admins | full field sets across all these | Y | `admin/service.py` | Correct, matches Master Doc ordering, all invariants enforced. |
| **PATCH /clinics/{id}** | clinics.clinic_admin_id, clinics.is_main_branch | **GAP-CONFIRMED (spot-verified) — cross-entity consistency bug** | `admin/service.py:292-296` `ClinicService.update` is a bare dynamic-SET, zero business-rule checks | A caller can PATCH `clinic_admin_id` to ANY existing profile id — no role check (need not be role='clinic_admin'), no "already someone else's admin" check, no `admins` table sync (leaves `clinics.clinic_admin_id` and `admins.clinic_id` out of sync with each other), bypasses every safeguard `assign_admin` has. Also lets `is_main_branch` be flipped `true` outside the "first clinic in region" rule — DB's unique partial index will reject a second `true` per region, but this path doesn't catch `IntegrityError` (every other write path in the file does), so it surfaces as a raw 500 instead of a clean 409. Recommend: either exclude `clinic_admin_id`/`is_main_branch` from the generic `ClinicUpdate` schema (force those through their dedicated endpoints), or add the same checks `assign_admin` already has. |

## staff (audited by background agent, spot-verified)

Memory notes reconfirmed still true (create/delete restricted to super_admin/regional_admin, staff email-domain allowlist enforced at create AND on email-change, every PATCH emits outbox_events).

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| POST/PATCH/DELETE /doctors, /clinical-assistants, /receptionists | profiles + role table + clinic_staff_assignments + onboarding consent + outbox_events, full field sets | Y | `staff/service.py:154-417` | Correct, 3-way soft-delete kept consistent. |
| **POST /ca-doctor-assignments** | ca_doctor_assignments.ca_id/doctor_id/clinic_id | **GAP-CONFIRMED (spot-verified) — cross-entity consistency bug** | `staff/router.py:164-170` has no `assert_clinic_scope` call (every other write route in this file has one); `CaDoctorAssignmentService.create` (`service.py:425-429`) passes all 3 caller-supplied ids straight to insert with no cross-check | Same bug class as the treatment_protocols/scheduling findings above. Three independent caller-supplied UUIDs (ca_id, doctor_id, clinic_id) — nothing checks the CA's own clinic, the doctor's own clinic, and the supplied clinic_id all agree. DB only enforces FK existence + `UNIQUE(ca_id, doctor_id)`. A clinic_admin (in scope for this route) could link a CA at clinic A to a doctor at clinic B. |
| GET /ca-doctor-assignments, POST /staff-requests(+decision) | — (read) / staff_requests full field set | N/A / Y | `router.py`, `service.py:440-488` | Correct. |

## reports / audit — confirm-only

Both still stub-only, unchanged since 2026-08-19 memory: `__init__.py` only, no router/service/repository in either directory, no registration in `app/main.py`. Confirmed nothing built since.

## consent

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| POST /consent-records | consent_records (consent_type/template_id/patient_id/staff_id/clinic_id/region_id) | Y | `consent/service.py:74-81` → `repository.py` | Correct. `patient_id` resolved profiles.id not patients.patient_id, matches documented convention. |
| PATCH /consent-records/{id}/status (sign) | consent_records.status/signed_by/witness_id/signature_data/ip_address/content_hash_at_signing; profiles.consent_signed; profiles.is_active (staff only, immediate); patients.registration_status (patient, via advance_registration_status) | Y | `consent/service.py:105-171` | Correct, matches documented state machine (patient activates later at registration_complete/approval, not at sign). |
| PATCH .../status (sign), witness_id | consent_records.witness_id | **PARTIAL — OPEN QUESTION** | `consent/schemas.py:34,55` `witness_id: UUID \| None = None`, no enforcement anywhere in `service.py::sign` or DB (`SQL/v1/07_tables_compliance.sql:199` nullable, no CHECK) | Old memory says non-self-registered patient consent required a witness at signing (`_WITNESS_REQUIRED_TYPES`). That constant/check no longer exists in code — witness_id is now fully optional for every consent type. Column not dead (FK'd, still written when provided), but nothing forces it. Confirm with user: rule deliberately dropped, or regression? |
| PATCH .../status (revoke) | consent_records.status/revoked_by | Y | `service.py:174-186` | Correct. |
| GET /consent-templates, GET /consent-records* | — (read) | N/A | `router.py` | Correct. |

## anamnesis

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| POST /patients/{id}/anamnesis | anamnesis_assessments (patient_id/submitted_by/taken_by/version/assessment_stage) | Y | `anamnesis/service.py:31-48` | Correct. assessment_stage correctly distinguishes general_registration (self-service) vs main_clinical (doctor in-person redo), matches flow-pivot doc rule ("Doctor always personally redoes anamnesis"). |
| PATCH /anamnesis/{id} | anamnesis_responses (question_id/response_value/response_values), anamnesis_assessments.status/completed | Y | `anamnesis/service.py:69-100` | Correct, upsert per question, mark_complete drives patient registration_status advance. |
| GET /anamnesis-catalog, GET .../anamnesis, GET .../responses | — (read) | N/A | `router.py` | Correct. |

No gaps found in this module.

## patients

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| POST /patients (staff-direct) | profiles+patients, same full field set as auth module's wizards, PLUS emergency_contact_name/phone (this route's own schema does collect them) | Y | `patients/router.py:23-44` → `service.py:56-143` | Correct, full capture including emergency contact (unlike the OTP wizards — see auth section). |
| PATCH /patients/{id} | profiles (name/email/phone/gender/dob/address/is_active), patients (emergency_contact_name/phone) | Y | `service.py:154-165` | Correct, this is the "fill in later" path for emergency contact. |
| PATCH .../approval | patients.approval_status/approved_by/rejection_reason; profiles.is_active (on approve) | Y | `service.py:167-193` | Correct, gated on registration_complete + pending, matches doc. |
| PATCH .../allocate-doctor | doctor_patient_assignments (end old + create new), patients.primary_doctor_id | Y | `service.py:195-225` | Correct, ends prior active assignment first (no dupes). |
| POST .../disease-selection | disease_selection (patient_profile_id/disease_id/disease_unknown/is_primary), triggers PRS auto-assign + registration_status advance | Y | `service.py:238-272` | Correct. |
| advance_registration_status (internal, called from consent/anamnesis/prs completion) | patients.registration_status, doctor_patient_assignments (on auto-complete), profiles.is_active (staff-registered only) | Y | `service.py:280-370` | Correct, self-healing re-derivation, matches Master Doc step 6. Doctor auto-allocation via least-loaded view, not a counter. |
| POST .../followup-cycles | delegates to treatment_protocols.ProtocolInstanceService.create (instance_type='followup') | Y | `service.py:383-419` | Correct, see treatment_protocols section for the actual write. |
| POST .../transfers, PATCH /transfers/{id}/complete | patient_clinic_transfers (full field set), patients.primary_clinic_id/primary_doctor_id (on complete), protocol_instances.clinic_id/doctor_id (on complete, if active episode) | Y | `service.py:443-524` | Correct, matches doc ("NO RESTART", active episode repointed not recreated). |
| POST .../exit | protocol_instances.status='completed' (if active); no dedicated patients "exited" column (by design — represented via signed consent + closed episode) | Y | `service.py:538-567` | Correct, matches doc note that there's no separate exit-status column. |
| GET endpoints | — (read) | N/A | `router.py` | Correct. |
| **Old memory gap re-checked: PatientRead missing name/email/phone** | — | **RESOLVED since memory was written** | `patients/schemas.py:60-64` | `PatientRead` now has `first_name`/`last_name`/`email` explicitly joined from profiles, plus `doctor_first_name`/`doctor_last_name`. No longer a gap — memory was stale, corrected here. |

No new gaps. Emergency-contact-at-signup remains the one open item, already logged under auth (root cause is the OTP wizard schemas, not this module).

## treatment_protocols — the "training protocol route" named in your ask

30 routes, 8-step wizard. Best-instrumented module found so far — extensive pre-flight validation (readable 422s ahead of DB triggers), same-transaction writes, real business-rule enforcement (device-at-clinic, montage ownership, prescription-completeness gate before activation).

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| Steps 1-6 GET catalogue routes (devices, conditions, diagnoses, placements, dosing, scales) + resolve/validate | — (read, or pure computation for validate/resolve) | N/A | `service.py:119-289` | Correct, no write expected. |
| POST /neuromod/custom-montages | protocol_custom_montages (created_by/clinic_id/device_id/condition_id/montage_name/anode_sites/cathode_sites/description/clinical_reasoning) | Y | `service.py:988-1044` | Correct, electrode-shape re-validated app-side ahead of the DB CHECK. |
| POST /protocol-instances | protocol_instances (patient_id/doctor_id/ca_id/clinic_id/instance_type/created_by/instance_number/status/notes) | Y | `service.py:895-940` | Correct. One-active-episode enforced both app-side (pre-check) and DB-side (unique constraint), matches Master Doc. |
| POST /treatment-protocols (Step 8, the actual protocol write) | treatment_protocols (instance_id/device_id/set_by/placement+dosing OR custom_montage_id/session_count/follow_up_every_n/status/device_settings/notes/authored_in_appointment_id/prescribed_current_ma/prescribed_duration_min/ramp_seconds/sessions_per_week) + protocol_conditions/protocol_diagnoses/protocol_scales (child tables) + appointments (one row per generated session/follow-up) + protocol_device_sessions/protocol_followup (mirror rows) | Y | `service.py:445-606,660-771` | Fully verified, all in one transaction. Exactly-one-of-placement/custom_montage enforced both by schema validator and DB CHECK (54). Every generated appointment correctly carries clinic_device_id (device sessions only) vs doctor_id (both types), booked_by left NULL by design (nothing booked yet). |
| PATCH /treatment-protocols/{id}, /activate, /cancel, /complete | treatment_protocols.status + related fields; appointments (cancel_planned on cancel) | Y | `service.py:775-874` | Correct. Activation gated on prescription-completeness (current_ma/duration/sessions_per_week all set) — readable 422 ahead of the DB trigger. |
| **POST .../prs/device-session, POST .../prs/follow-up** | device_session_prs_responses / followup_prs_responses (appointment_id/protocol_id/session_number/**instance_id**/patient_id) | **GAP — CONFIRMED** | `service.py:1095-1144`, DB: `SQL/v1/32_treatment_protocol.sql:528-556,1089-1117` | These are **link rows**, not data-entry — the real PRS answers/scores live in `prs_assessment_instances`/`prs_responses` (verified correct under the `prs` module above). `instance_id` here is caller-supplied (`DeviceSessionPrsCreate.instance_id: str`) and points at any `prs_assessment_instances.instance_id`. The only server-side check (`fn_check_prs_appointment_type` DB trigger) verifies the **appointment's type** matches ('device_session' or 'protocol_followup') — it does NOT verify the referenced PRS instance actually belongs to the same patient as the appointment. Neither the trigger nor `ProtocolPrsService.record_device_session/record_follow_up` (`service.py:1095-1144`) cross-checks `instance.patient_id == appt.patient_id`. A caller (including a "patient" role, which is in `_PRS_WRITERS`) could link one patient's completed PRS assessment to a different patient's device-session appointment — DB accepts it (FK only requires the instance to exist, not to match). Recommend adding the patient-match check either as a DB trigger addition (cheap: extend `fn_check_prs_appointment_type` to also compare `prs_assessment_instances.patient_id`) or an app-side check in both `record_device_session`/`record_follow_up` before insert. |
| GET /treatment-protocols/{id}/prs | — (read) | N/A | `service.py:1146-1151` | Correct. |
| GET /protocol-instances, /treatment-protocols, /{id}, /{id}/sessions | — (read) | N/A | `router.py` | Correct, clinic-scoped, patient self-scoped. |

**One real, actionable gap found in this module** — cross-patient PRS-link mismatch, flagged above. Everything else verified correct and unusually well-guarded.

## scheduling (audited by background agent, spot-verified)

38 routes — appointments spine, doctor/device schedules, patient self-booking.

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| POST /doctors/{id}/weekly-schedules, PUT /schedule/my | doctor_weekly_schedules.max_appointments | PARTIAL (dead-column candidate) | `scheduling/schemas.py:15,45`, `repository.py:59-61,76-86` | Written when supplied, but never read back anywhere (`WeeklyScheduleRead` omits it, availability computation never references it — slots are exclusive, not counted). Not a true capture gap, candidate for the cleanup list. Confirm with user: intentional unused field, or drop it. |
| **POST /appointments (staff)** | appointments.instance_id vs appointments.patient_id | **GAP — CONFIRMED (spot-verified)** | `scheduling/service.py:498` writes `data["instance_id"]` straight into the INSERT with no check against `patient_id`; only DB triggers on `appointments` are audit/updated_at (`SQL/v1/15_triggers.sql:9-10`); `fk_appointments_instance_id` only requires the instance to exist, not belong to the same patient | Same bug class as the treatment_protocols PRS-link finding above. A staff caller can create a `protocol_followup` appointment for patient A while pointing `instance_id` at patient B's episode of care — nothing rejects it. (`device_session` type is accidentally protected, since this endpoint has no `plan_id` and a CHECK requires one for that type — `protocol_followup` has no equivalent guard.) Recommend an app-side check or a DB trigger extension, same fix shape as the treatment_protocols finding. |
| POST /appointments, ca_id field | appointments.ca_id | Y, unscoped | `scheduling/schemas.py:115`, `service.py:497` | Minor, unconfirmed severity: no check the supplied `ca_id` is actually a CA at the appointment's clinic (FK only targets `profiles(id)`). Elsewhere in the same module `ca_id` is treated as late-bound (set only when a CA starts a device session) — pre-setting it at creation is off-pattern but not confirmed exploitable. Flagged for a second look, not a confirmed gap. |
| PATCH /appointments/{id}, /reschedule, /status | appointments.notes/patient_complaint/status/cancellation_reason/cancelled_by/checked_in_at/started_at/completed_at/ca_id | Y | `service.py:672-793` | Correct, FSM-gated, advisory-lock-guarded device capacity, audit log written. |
| Weekly schedule / override / clinic device schedule CRUD | doctor_weekly_schedules, doctor_schedule_overrides, clinic_device_schedules(+overrides), clinic_devices, appointment_audit_logs | Y | `service.py:309-360,1295-1478` | Correct, full field coverage confirmed against `05/36/37/41_*.sql`. |
| GET endpoints | — (read) | N/A | `router.py` | Correct. |

## device_sessions (audited by background agent, spot-verified)

27 routes — TDCS session execution (checklist through completion).

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| POST /checklist, /start-/pause/resume/stop/complete, /device-fit, /symptoms, /adverse-events, /notes, /activities, /feedback, /media(+consent), /next-session, /sos(+ack) | device_sessions + 8 child tables, full field sets | Y | `device_sessions/service.py` throughout, cross-checked `56_device_session_records.sql` | Correct across the board — consistently scope-checked (clinic/patient) on every write, better-guarded than scheduling's appointment creation. |
| **GET /scales, PATCH /scales/{id}** | device_session_scales.prs_instance_id | **GAP — CONFIRMED (spot-verified: zero writes anywhere in codebase)** | `device_sessions/service.py:398-431`, column comment `56_device_session_records.sql:382` says it should link a due scale to the actual answered `prs_assessment_instances` row | `prs_instance_id` is never written by any code path — confirmed by repo-wide grep, only appears as a read-schema field (`schemas.py:260`). `status` similarly never advances past `'pending'` (only ever set once at seed, or rewritten unchanged by `set_scale_delivery`). The scale-delivery tracking this table exists for stops at "seeded, delivery mode chosen" — the intended link to the patient's actual PRS answers was never wired up. Real capture gap, not vestigial (the column is meant to be live, per its own DB comment). |

## clinical (audited by background agent, spot-verified)

Down to 4 routes since commit `b341817` gutted Session/TreatmentSession/doctor-notes code — now just `assessment_protocol_requests` CRUD.

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| POST /assessment-protocol-requests, PATCH .../decision | assessment_protocol_requests (full field set), + patient_scale_assignments on approval (via prs module) | Y | `clinical/service.py:30-108` | Correct, both patient/doctor ids resolved via `core/resolve.py` convention. |
| GET endpoints | — (read) | N/A | `router.py` | Correct. |

No gaps. Confirmed clean — legacy `cycle_id` column was properly dropped (`58_protocol_instances_absorb_cycle.sql:229-230`), no dangling references, no writes to retired `core.sessions`/`core.treatment_sessions` anywhere in this module.

## prs

| Endpoint | Table.column | Captured? | Evidence | Note |
|---|---|---|---|---|
| POST /patient-scale-assignments (+ auto_assign_for_disease, called from patients/service.py::select_disease) | patient_scale_assignments (patient_id/scale_id/disease_id/assessment_stage/assigned_by/assignment_reason) | Y | `prs/service.py:70-130` | Correct. general_registration stage deliberately narrowed to EQ-5D-5L only (documented product decision, not a bug). |
| POST /prs-assessment-instances (start) | prs_assessment_instances (disease_id/patient_id/initiated_by/administered_by/assessment_stage/language_code) | Y | `prs/service.py:243-289` | Correct, resumes in-progress instead of duplicating. |
| POST .../responses (submit) | prs_responses (question_id/given_response/response_value/language_code), prs_scale_results (via _finalize_scale) | Y | `prs/service.py:297-364` | Correct, server-authoritative scoring, skip-logic-aware denominator. Drives registration_status advance on completion. |
| PATCH .../language | prs_assessment_instances.language_code | Y | `service.py:233-241` | Correct. |
| **session_id field** — `AssessmentInstanceCreate.session_id`, `prs_assessment_instances.session_id` | **VESTIGIAL, not a capture gap** | N/A | `prs/schemas.py:62`, `repository.py:293-313`, FK → `core.sessions.session_id` (`erd.dbml:1581`) | `core.sessions` was retired code-side in commit `b341817` (2026-08-20) — verified 0 rows in `prs_assessment_instances.session_id` at that time, table itself not yet dropped (deliberate phased cutover). This field is still accepted by the API but nothing ever populates it meaningfully now. **Candidate for the cleanup-proposal deliverable** once `core.sessions` itself is dropped — don't fix now, tracked as known/intentional. |
| GET endpoints (catalog, results, responses, responses-by-scale) | — (read) | N/A | `router.py` | Correct. |

No new gaps — one pre-known vestigial field, tracked for later cleanup, not a bug.
