# Anava Clinic — API Endpoint Catalog v1.0

Companion document to `Anava_Backend_Architecture_v1.md` (Section 6/8). Lists every projected endpoint, per module, in two versions:
- **Original (per-action)** — one endpoint per action/filter/view, the naive projection.
- **Consolidated (recommended)** — after applying the 8 reduction techniques agreed in review (generic status/decision endpoints, query-param filtering, field-expansion, RLS-scoped single endpoints, generic presign pattern, type-parameterized endpoints, bulk-capable endpoints, generic catalog/reference-data endpoints). No business rule, permission check, or state transition is removed — only the URL surface is reduced.

These are itemized projections for planning, not measured from running code (contrast with the old NeuroWellness system, where the 119-endpoint figure was `grep`-measured from `D:\PCS\Backend_v1\neurowellness\backend`). Re-measure once routers are actually built.

---

## Summary

| Module | Original endpoints | Consolidated endpoints | Reduction |
|---|---|---|---|
| auth | 4 | 4 | — |
| admin | 21 | 16 | 24% |
| staff | 19 | 16 | 16% |
| patients | 10 | 7 | 30% |
| clinical | 33 | 22 | 33% |
| scheduling | 29 | 17 | 41% |
| prs | 22 | 12 | 45% |
| anamnesis | 8 | 6 | 25% |
| files | 14 | 7 | 50% |
| consent | 10 | 6 | 40% |
| store | 14 | 8 | 43% |
| inventory | 8 | 6 | 25% |
| payments | 6 | 5 | 17% |
| notifications | 6 | 5 | 17% |
| audit | 6 | 5 | 17% |
| reports | 8 | 6 | 25% |
| **TOTAL** | **218** | **148** | **32%** |

**16 modules ("APIs") in both versions — module boundaries don't change, only endpoint count per module.** This refines the earlier rough estimate (~220 / ~156) with an actual itemized count: **218 original → 148 consolidated**, a 32% reduction, still above the old system's measured 119 (genuine new functionality: store, inventory, payments, deeper consent/clinical sequencing did not exist before).

---

## 1. auth

No consolidation opportunity — already minimal.

| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | POST | `/auth/login` | Cognito login, returns tokens |
| 2 | POST | `/auth/refresh` | Refresh access token |
| 3 | POST | `/auth/logout` | Client-side + optional Cognito global sign-out |
| 4 | POST | `/auth/mfa/verify` | MFA challenge verification |

---

## 2. admin (regions, clinics, admins, clinic_staff_assignments, clinic_requests)

### Original (per-action) — 21
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | POST | `/regions` | Create region |
| 2 | GET | `/regions` | List regions |
| 3 | GET | `/regions/{id}` | Region detail |
| 4 | PATCH | `/regions/{id}` | Update region |
| 5 | PATCH | `/regions/{id}/assign-admin` | Assign Regional Admin |
| 6 | POST | `/clinics` | Create clinic |
| 7 | GET | `/clinics` | List clinics |
| 8 | GET | `/clinics/{id}` | Clinic detail |
| 9 | PATCH | `/clinics/{id}` | Update clinic |
| 10 | PATCH | `/clinics/{id}/activate` | setup → active transition |
| 11 | PATCH | `/clinics/{id}/close` | active → pending_closure transition |
| 12 | PATCH | `/clinics/{id}/change-admin` | Change Clinic Admin |
| 13 | PATCH | `/clinics/{id}/change-main-branch` | Toggle is_main_branch |
| 14 | POST | `/clinic-requests` | Submit clinic request |
| 15 | GET | `/clinic-requests` | List clinic requests |
| 16 | GET | `/clinic-requests/{id}` | Request detail |
| 17 | PATCH | `/clinic-requests/{id}/approve` | Approve request |
| 18 | PATCH | `/clinic-requests/{id}/reject` | Reject request |
| 19 | PATCH | `/clinic-requests/{id}/withdraw` | Withdraw request |
| 20 | GET | `/clinics/{id}/staff-assignments` | List clinic staff |
| 21 | POST | `/clinics/{id}/staff-assignments` | Add staff assignment |

### Consolidated (recommended) — 16
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | POST | `/regions` | Create region |
| 2 | GET | `/regions` | List regions |
| 3 | GET | `/regions/{id}` | Region detail |
| 4 | PATCH | `/regions/{id}` | Update region incl. admin assignment (merges #4,5) |
| 5 | POST | `/clinics` | Create clinic |
| 6 | GET | `/clinics` | List clinics |
| 7 | GET | `/clinics/{id}` | Clinic detail |
| 8 | PATCH | `/clinics/{id}` | Update clinic incl. change-admin/change-main-branch (merges #9,12,13) |
| 9 | PATCH | `/clinics/{id}/status` | activate/close decision endpoint (merges #10,11) |
| 10 | POST | `/clinic-requests` | Submit clinic request |
| 11 | GET | `/clinic-requests` | List clinic requests |
| 12 | GET | `/clinic-requests/{id}` | Request detail |
| 13 | PATCH | `/clinic-requests/{id}/decision` | approve/reject/withdraw merged (merges #17,18,19) |
| 14 | GET | `/clinics/{id}/staff-assignments` | List clinic staff |
| 15 | POST | `/clinics/{id}/staff-assignments` | Add staff assignment |
| 16 | PATCH | `/staff-assignments/{id}` | Update/remove assignment (is_active toggle) |

---

## 3. staff (doctors, clinical_assistants, ca_doctor_assignments, receptionists, staff_requests)

### Original (per-action) — 19
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | POST | `/doctors` | Create doctor profile |
| 2 | GET | `/doctors` | List doctors |
| 3 | GET | `/doctors/{id}` | Doctor detail |
| 4 | PATCH | `/doctors/{id}` | Update doctor |
| 5 | PATCH | `/doctors/{id}/availability` | Update availability_status |
| 6 | POST | `/clinical-assistants` | Create CA profile |
| 7 | GET | `/clinical-assistants` | List CAs |
| 8 | GET | `/clinical-assistants/{id}` | CA detail |
| 9 | PATCH | `/clinical-assistants/{id}` | Update CA |
| 10 | POST | `/ca-doctor-assignments` | Assign CA to doctor |
| 11 | GET | `/ca-doctor-assignments` | List assignments |
| 12 | PATCH | `/ca-doctor-assignments/{id}/remove` | End assignment |
| 13 | POST | `/receptionists` | Create receptionist profile |
| 14 | GET | `/receptionists` | List receptionists |
| 15 | PATCH | `/receptionists/{id}` | Update receptionist |
| 16 | POST | `/staff-requests` | Submit staff request |
| 17 | GET | `/staff-requests` | List staff requests |
| 18 | PATCH | `/staff-requests/{id}/approve` | Approve staff request |
| 19 | PATCH | `/staff-requests/{id}/reject` | Reject staff request |

### Consolidated (recommended) — 16
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | POST | `/doctors` | Create doctor profile |
| 2 | GET | `/doctors` | List doctors |
| 3 | GET | `/doctors/{id}` | Doctor detail |
| 4 | PATCH | `/doctors/{id}` | Update doctor incl. availability (merges #5) |
| 5 | POST | `/clinical-assistants` | Create CA profile |
| 6 | GET | `/clinical-assistants` | List CAs |
| 7 | PATCH | `/clinical-assistants/{id}` | Update CA |
| 8 | POST | `/ca-doctor-assignments` | Assign CA to doctor |
| 9 | GET | `/ca-doctor-assignments` | List assignments |
| 10 | PATCH | `/ca-doctor-assignments/{id}` | Update/end assignment (removed_at toggle) |
| 11 | POST | `/receptionists` | Create receptionist profile |
| 12 | GET | `/receptionists` | List receptionists |
| 13 | PATCH | `/receptionists/{id}` | Update receptionist |
| 14 | POST | `/staff-requests` | Submit staff request |
| 15 | GET | `/staff-requests` | List staff requests |
| 16 | PATCH | `/staff-requests/{id}/decision` | approve/reject merged (merges #18,19) |

---

## 4. patients (patients, patient_disease_selection)

### Original (per-action) — 10
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | POST | `/patients` | Registration Step 1 — demographics |
| 2 | GET | `/patients` | List patients |
| 3 | GET | `/patients/{id}` | Patient detail |
| 4 | PATCH | `/patients/{id}` | Update demographics |
| 5 | POST | `/patients/{id}/disease-selection` | Registration Step 2 |
| 6 | PATCH | `/patients/{id}/disease-selection/{pds_id}` | Update disease selection |
| 7 | GET | `/patients/{id}/registration-status` | Get status |
| 8 | PATCH | `/patients/{id}/registration-status` | Manual status override (admin) |
| 9 | GET | `/patients/incomplete-registrations` | Recovery worklist (Flow K) |
| 10 | PATCH | `/patients/{id}/deactivate` | Soft-delete |

### Consolidated (recommended) — 7
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | POST | `/patients` | Registration Step 1 |
| 2 | GET | `/patients` | List, `?registration_status=` filter covers recovery worklist (merges #7,9) |
| 3 | GET | `/patients/{id}` | Patient detail incl. registration_status (merges #7) |
| 4 | PATCH | `/patients/{id}` | Update demographics + status override (merges #4,8) |
| 5 | POST | `/patients/{id}/disease-selection` | Registration Step 2 |
| 6 | PATCH | `/patients/{id}/disease-selection/{pds_id}` | Update disease selection |
| 7 | PATCH | `/patients/{id}/deactivate` | Soft-delete |

---

## 5. clinical (doctor_patient_assignments, treatment_cycles, assessment_protocol_requests, sessions, treatment_plans, treatment_sessions, doctor_session_notes)

### Original (per-action) — 33
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/doctor-patient-assignments` | List assignments |
| 2 | POST | `/doctor-patient-assignments` | Manual override (auto-alloc is internal, Flow M) |
| 3 | PATCH | `/doctor-patient-assignments/{id}/transfer` | Mark transferred |
| 4 | PATCH | `/doctor-patient-assignments/{id}/complete` | Mark completed |
| 5 | POST | `/treatment-cycles` | Create cycle (system-triggered normally) |
| 6 | GET | `/treatment-cycles` | List cycles |
| 7 | GET | `/treatment-cycles/{id}` | Cycle detail |
| 8 | PATCH | `/treatment-cycles/{id}/complete` | Mark completed |
| 9 | PATCH | `/treatment-cycles/{id}/cancel` | Mark cancelled |
| 10 | POST | `/assessment-protocol-requests` | CA submits protocol |
| 11 | GET | `/assessment-protocol-requests` | List requests |
| 12 | GET | `/assessment-protocol-requests/{id}` | Request detail |
| 13 | PATCH | `/assessment-protocol-requests/{id}/approve` | Doctor authorizes |
| 14 | PATCH | `/assessment-protocol-requests/{id}/request-modification` | Doctor requests changes |
| 15 | PATCH | `/assessment-protocol-requests/{id}/reject` | Doctor rejects |
| 16 | GET | `/sessions` | List sessions |
| 17 | GET | `/sessions/{id}` | Session detail |
| 18 | PATCH | `/sessions/{id}/start` | Mark in_progress |
| 19 | PATCH | `/sessions/{id}/complete` | Mark completed |
| 20 | PATCH | `/sessions/{id}/cancel` | Mark cancelled |
| 21 | PATCH | `/sessions/{id}/mark-missed` | Mark missed (no-show) |
| 22 | POST | `/treatment-plans` | Doctor creates plan |
| 23 | GET | `/treatment-plans` | List plans |
| 24 | GET | `/treatment-plans/{id}` | Plan detail |
| 25 | PATCH | `/treatment-plans/{id}/extend-sessions` | Extend beyond standard 5 |
| 26 | PATCH | `/treatment-plans/{id}/supersede` | Mark superseded by follow-up plan |
| 27 | GET | `/treatment-sessions` | List treatment sessions |
| 28 | GET | `/treatment-sessions/{id}` | Detail |
| 29 | PATCH | `/treatment-sessions/{id}/start` | Mark in_progress |
| 30 | PATCH | `/treatment-sessions/{id}/complete` | Mark completed |
| 31 | POST | `/doctor-session-notes` | Create S2/S4 notes |
| 32 | GET | `/doctor-session-notes/{id}` | Detail |
| 33 | PATCH | `/doctor-session-notes/{id}` | Update notes |

### Consolidated (recommended) — 22
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/doctor-patient-assignments` | List assignments |
| 2 | POST | `/doctor-patient-assignments` | Manual override |
| 3 | PATCH | `/doctor-patient-assignments/{id}/status` | transfer/complete merged (merges #3,4) |
| 4 | POST | `/treatment-cycles` | Create cycle |
| 5 | GET | `/treatment-cycles` | List cycles |
| 6 | GET | `/treatment-cycles/{id}` | Cycle detail |
| 7 | PATCH | `/treatment-cycles/{id}/status` | complete/cancel merged (merges #8,9) |
| 8 | POST | `/assessment-protocol-requests` | CA submits protocol |
| 9 | GET | `/assessment-protocol-requests` | List requests |
| 10 | GET | `/assessment-protocol-requests/{id}` | Request detail |
| 11 | PATCH | `/assessment-protocol-requests/{id}/decision` | approve/reject/modify merged (merges #13,14,15) |
| 12 | GET | `/sessions` | List sessions |
| 13 | GET | `/sessions/{id}` | Session detail |
| 14 | PATCH | `/sessions/{id}/status` | start/complete/cancel/missed merged (merges #18-21) |
| 15 | POST | `/treatment-plans` | Doctor creates plan |
| 16 | GET | `/treatment-plans` | List plans |
| 17 | GET | `/treatment-plans/{id}` | Plan detail |
| 18 | PATCH | `/treatment-plans/{id}` | Extend/supersede via body field (merges #25,26) |
| 19 | GET | `/treatment-sessions` | List treatment sessions |
| 20 | GET | `/treatment-sessions/{id}` | Detail |
| 21 | PATCH | `/treatment-sessions/{id}/status` | start/complete merged (merges #29,30) |
| 22 | POST | `/doctor-session-notes` | Create; GET/PATCH folded into detail+update on same resource |

---

## 6. scheduling (doctor_weekly_schedules, doctor_schedule_overrides, appointment_requests, appointments, appointment_audit_logs)

### Original (per-action) — 29
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | POST | `/doctors/{id}/weekly-schedules` | Add recurring slot rule |
| 2 | GET | `/doctors/{id}/weekly-schedules` | List rules |
| 3 | PATCH | `/weekly-schedules/{id}` | Update rule |
| 4 | DELETE | `/weekly-schedules/{id}` | Remove rule |
| 5 | POST | `/doctors/{id}/schedule-overrides` | Add date exception |
| 6 | GET | `/doctors/{id}/schedule-overrides` | List overrides |
| 7 | PATCH | `/schedule-overrides/{id}` | Update override |
| 8 | DELETE | `/schedule-overrides/{id}` | Remove override |
| 9 | GET | `/doctors/{id}/availability` | Computed open slots |
| 10 | POST | `/appointment-requests` | Patient/staff submits request |
| 11 | GET | `/appointment-requests` | List requests |
| 12 | GET | `/appointment-requests/{id}` | Detail |
| 13 | PATCH | `/appointment-requests/{id}/approve` | Approve → creates appointment |
| 14 | PATCH | `/appointment-requests/{id}/reject` | Reject |
| 15 | PATCH | `/appointment-requests/{id}/cancel` | Patient cancels request |
| 16 | POST | `/appointments` | Direct staff booking |
| 17 | GET | `/appointments` | List appointments |
| 18 | GET | `/appointments/{id}` | Detail |
| 19 | PATCH | `/appointments/{id}/reschedule` | Reschedule |
| 20 | PATCH | `/appointments/{id}/cancel` | Cancel |
| 21 | PATCH | `/appointments/{id}/check-in` | Check-in |
| 22 | PATCH | `/appointments/{id}/start` | Mark in_progress |
| 23 | PATCH | `/appointments/{id}/complete` | Mark completed |
| 24 | PATCH | `/appointments/{id}/no-show` | Mark no-show |
| 25 | GET | `/appointments/{id}/audit-log` | Change history for one appointment |
| 26 | GET | `/appointment-audit-logs` | Global audit log query |
| 27 | GET | `/clinics/{id}/appointments` | Clinic-wide calendar view |
| 28 | GET | `/doctors/{id}/appointments` | Doctor calendar view |
| 29 | POST | `/doctors/{id}/weekly-schedules/bulk` | Bulk-create weekly rules |

### Consolidated (recommended) — 17
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | POST | `/doctors/{id}/weekly-schedules` | Add recurring slot rule (supports bulk array body, merges #29) |
| 2 | GET | `/doctors/{id}/weekly-schedules` | List rules |
| 3 | PATCH | `/weekly-schedules/{id}` | Update/deactivate via is_active (merges #4) |
| 4 | POST | `/doctors/{id}/schedule-overrides` | Add date exception |
| 5 | GET | `/doctors/{id}/schedule-overrides` | List overrides |
| 6 | PATCH | `/schedule-overrides/{id}` | Update/remove (merges #8) |
| 7 | GET | `/doctors/{id}/availability` | Computed open slots |
| 8 | POST | `/appointment-requests` | Submit request |
| 9 | GET | `/appointment-requests` | List requests |
| 10 | GET | `/appointment-requests/{id}` | Detail |
| 11 | PATCH | `/appointment-requests/{id}/decision` | approve/reject/cancel merged (merges #13,14,15) |
| 12 | POST | `/appointments` | Direct staff booking |
| 13 | GET | `/appointments` | List, `?clinic_id=`/`?doctor_id=` filters cover calendar views (merges #27,28) |
| 14 | GET | `/appointments/{id}` | Detail |
| 15 | PATCH | `/appointments/{id}/reschedule` | Reschedule (kept distinct — different semantics from a status change) |
| 16 | PATCH | `/appointments/{id}/status` | cancel/check-in/start/complete/no-show merged (merges #20-24) |
| 17 | GET | `/appointments/{id}/audit-log` | Change history (covers #26 via `?appointment_id=` filter too) |

---

## 7. prs (diseases, scales, disease_scale_map, questions, options, scale_question_map, disease_question_map, assessment_instances, responses, scale_results, final_results, patient_scale_assignments)

### Original (per-action) — 22
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/prs-diseases` | List diseases |
| 2 | POST | `/prs-diseases` | Admin create |
| 3 | PATCH | `/prs-diseases/{id}` | Admin update |
| 4 | GET | `/prs-scales` | List scales |
| 5 | POST | `/prs-scales` | Admin create |
| 6 | PATCH | `/prs-scales/{id}` | Admin update |
| 7 | GET | `/prs-disease-scale-map` | List mappings |
| 8 | POST | `/prs-disease-scale-map` | Admin create mapping |
| 9 | GET | `/prs-questions` | List questions |
| 10 | POST | `/prs-questions` | Admin create |
| 11 | PATCH | `/prs-questions/{id}` | Admin update |
| 12 | GET | `/prs-options` | List options |
| 13 | POST | `/prs-options` | Admin create |
| 14 | POST | `/patient-scale-assignments` | Assign scale to patient |
| 15 | GET | `/patient-scale-assignments` | List assignments |
| 16 | PATCH | `/patient-scale-assignments/{id}/deactivate` | Deactivate |
| 17 | POST | `/prs-assessment-instances` | Start assessment |
| 18 | GET | `/prs-assessment-instances/{id}` | Detail |
| 19 | POST | `/prs-assessment-instances/{id}/responses` | Submit responses |
| 20 | GET | `/prs-assessment-instances/{id}/responses` | Get responses |
| 21 | GET | `/prs-assessment-instances/{id}/scale-results` | Per-scale scores |
| 22 | GET | `/prs-assessment-instances/{id}/final-result` | Aggregated result |

### Consolidated (recommended) — 12
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/prs-catalog` | Diseases+scales+questions+options combined read (merges #1,4,7,9,12) |
| 2 | POST | `/prs-catalog/{entity_type}` | Generic admin content create: disease\|scale\|question\|option (merges #2,5,8,10,13) |
| 3 | PATCH | `/prs-catalog/{entity_type}/{id}` | Generic admin content update (merges #3,6,11) |
| 4 | GET | `/prs-disease-scale-map` | List mappings |
| 5 | POST | `/patient-scale-assignments` | Assign scale to patient |
| 6 | GET | `/patient-scale-assignments` | List assignments |
| 7 | PATCH | `/patient-scale-assignments/{id}` | Update/deactivate (merges #16) |
| 8 | POST | `/prs-assessment-instances` | Start assessment |
| 9 | GET | `/prs-assessment-instances/{id}` | Detail |
| 10 | POST | `/prs-assessment-instances/{id}/responses` | Submit responses |
| 11 | GET | `/prs-assessment-instances/{id}/responses` | Get responses |
| 12 | GET | `/prs-assessment-instances/{id}/results` | scale-results + final-result via `?expand=` (merges #21,22) |

---

## 8. anamnesis (assessments, questions, options, responses)

### Original (per-action) — 8
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/anamnesis-questions` | List questions |
| 2 | POST | `/anamnesis-questions` | Admin create |
| 3 | GET | `/anamnesis-options` | List options |
| 4 | POST | `/patients/{id}/anamnesis` | Start assessment |
| 5 | GET | `/patients/{id}/anamnesis` | Get current version |
| 6 | PATCH | `/patients/{id}/anamnesis/{anamnesis_id}/responses` | Submit responses |
| 7 | PATCH | `/patients/{id}/anamnesis/{anamnesis_id}/complete` | Mark complete |
| 8 | POST | `/patients/{id}/anamnesis/new-version` | Create update version |

### Consolidated (recommended) — 6
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/anamnesis-catalog` | Questions+options combined (merges #1,3) |
| 2 | POST | `/anamnesis-catalog` | Admin create (merges #2) |
| 3 | POST | `/patients/{id}/anamnesis` | Start assessment |
| 4 | GET | `/patients/{id}/anamnesis` | Get current version |
| 5 | PATCH | `/patients/{id}/anamnesis/{anamnesis_id}` | Responses + complete via body (merges #6,7) |
| 6 | POST | `/patients/{id}/anamnesis/new-version` | Create update version |

---

## 9. files (patient_eeg_files, patient_medical_history_files)

### Original (per-action) — 14
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | POST | `/patients/{id}/eeg-files/presign-upload` | Get presigned PUT URL |
| 2 | POST | `/patients/{id}/eeg-files` | Confirm upload, save metadata |
| 3 | GET | `/patients/{id}/eeg-files` | List |
| 4 | GET | `/eeg-files/{id}` | Detail |
| 5 | GET | `/eeg-files/{id}/download-url` | Presigned GET |
| 6 | PATCH | `/eeg-files/{id}/review` | Doctor review |
| 7 | PATCH | `/eeg-files/{id}/supersede` | Mark corrected version |
| 8 | POST | `/patients/{id}/medical-history-files/presign-upload` | Get presigned PUT URL |
| 9 | POST | `/patients/{id}/medical-history-files` | Confirm upload |
| 10 | GET | `/patients/{id}/medical-history-files` | List |
| 11 | GET | `/medical-history-files/{id}` | Detail |
| 12 | GET | `/medical-history-files/{id}/download-url` | Presigned GET |
| 13 | PATCH | `/medical-history-files/{id}` | Update metadata |
| 14 | DELETE | `/medical-history-files/{id}` | Soft-delete |

### Consolidated (recommended) — 7
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | POST | `/patients/{id}/files/presign-upload` | Generic presign, `doc_type=eeg\|medical_history` param (merges #1,8) |
| 2 | POST | `/patients/{id}/files` | Generic confirm upload (merges #2,9) |
| 3 | GET | `/patients/{id}/files` | Generic list, filterable by doc_type (merges #3,10) |
| 4 | GET | `/files/{id}` | Detail, either type (merges #4,11) |
| 5 | GET | `/files/{id}/download-url` | Presigned GET, either type (merges #5,12) |
| 6 | PATCH | `/files/{id}` | Review/supersede/metadata-update via body (merges #6,7,13) |
| 7 | PATCH | `/files/{id}/deactivate` | Soft-delete, either type (merges #14) |

---

## 10. consent (consent_templates, consent_records)

### Original (per-action) — 10
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/consent-templates` | List templates |
| 2 | POST | `/consent-templates` | Admin create version |
| 3 | GET | `/consent-templates/{id}` | Detail |
| 4 | POST | `/consent-records` | Generate consent instance |
| 5 | GET | `/consent-records` | List |
| 6 | GET | `/consent-records/{id}` | Detail |
| 7 | PATCH | `/consent-records/{id}/sign` | Sign |
| 8 | PATCH | `/consent-records/{id}/revoke` | Revoke |
| 9 | GET | `/patients/{id}/consents` | Patient's consent history |
| 10 | GET | `/staff/{id}/consents` | Staff's consent history |

### Consolidated (recommended) — 6
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/consent-templates` | List templates |
| 2 | POST | `/consent-templates` | Admin create version |
| 3 | POST | `/consent-records` | Generate consent instance |
| 4 | GET | `/consent-records` | List, `?patient_id=`/`?staff_id=` filters cover #9,10 |
| 5 | GET | `/consent-records/{id}` | Detail |
| 6 | PATCH | `/consent-records/{id}/status` | sign/revoke merged (merges #7,8) |

---

## 11. store (products, store_orders, order_items, device_assignments)

### Original (per-action) — 14
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/products` | List catalog |
| 2 | POST | `/products` | Admin create |
| 3 | PATCH | `/products/{id}` | Admin update |
| 4 | POST | `/store-orders/device` | Create device order |
| 5 | POST | `/store-orders/accessory` | Create accessory order |
| 6 | GET | `/store-orders` | List orders |
| 7 | GET | `/store-orders/{id}` | Detail |
| 8 | PATCH | `/store-orders/{id}/doctor-approve` | Doctor approves device sale |
| 9 | PATCH | `/store-orders/{id}/dispatch` | Regional Admin dispatches |
| 10 | PATCH | `/store-orders/{id}/confirm-received` | Clinic confirms receipt |
| 11 | PATCH | `/store-orders/{id}/mark-collected` | Patient collects |
| 12 | PATCH | `/store-orders/{id}/cancel` | Cancel order |
| 13 | GET | `/device-assignments` | List device assignments |
| 14 | PATCH | `/device-assignments/{id}/collected` | Mark collected |

### Consolidated (recommended) — 8
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/products` | List catalog |
| 2 | POST | `/products` | Admin create/update combined via upsert semantics is overkill — keep create only |
| 3 | PATCH | `/products/{id}` | Admin update |
| 4 | POST | `/store-orders` | `order_type=device\|accessory` param (merges #4,5) |
| 5 | GET | `/store-orders` | List orders |
| 6 | GET | `/store-orders/{id}` | Detail |
| 7 | PATCH | `/store-orders/{id}/status` | approve/dispatch/receive/collect/cancel all merged (merges #8-12) |
| 8 | PATCH | `/device-assignments/{id}/status` | purchase_status transitions incl. collected (merges #14; #13 covered by store-orders list join) |

---

## 12. inventory (inventory, stock_transfers)

### Original (per-action) — 8
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/inventory` | List stock levels |
| 2 | GET | `/inventory/{clinic_id}/{product_id}` | Specific stock row |
| 3 | PATCH | `/inventory/{id}/adjust` | Manual adjustment |
| 4 | POST | `/stock-transfers` | Initiate transfer |
| 5 | GET | `/stock-transfers` | List transfers |
| 6 | PATCH | `/stock-transfers/{id}/dispatch` | Mark dispatched |
| 7 | PATCH | `/stock-transfers/{id}/confirm-received` | Mark received |
| 8 | POST | `/stock-transfers/replenishment-request` | Regional Admin requests from Super Admin |

### Consolidated (recommended) — 6
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/inventory` | List, `?clinic_id=`/`?product_id=` filters cover #2 |
| 2 | PATCH | `/inventory/{id}/adjust` | Manual adjustment |
| 3 | POST | `/stock-transfers` | Initiate transfer |
| 4 | GET | `/stock-transfers` | List transfers |
| 5 | PATCH | `/stock-transfers/{id}/status` | dispatch/receive merged (merges #6,7) |
| 6 | POST | `/stock-transfers/replenishment-request` | Replenishment request |

---

## 13. payments (payments)

### Original (per-action) — 6
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | POST | `/payments/razorpay-order` | Create Razorpay order |
| 2 | POST | `/webhooks/razorpay` | Webhook receiver |
| 3 | GET | `/payments` | List payments |
| 4 | GET | `/payments/{id}` | Detail |
| 5 | PATCH | `/payments/{id}/waive` | Clinic Admin waives |
| 6 | PATCH | `/payments/{id}/refund` | Initiate refund |

### Consolidated (recommended) — 5
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | POST | `/payments/razorpay-order` | Create Razorpay order |
| 2 | POST | `/webhooks/razorpay` | Webhook receiver |
| 3 | GET | `/payments` | List payments |
| 4 | GET | `/payments/{id}` | Detail |
| 5 | PATCH | `/payments/{id}/status` | waive/refund merged (merges #5,6) |

---

## 14. notifications (notifications)

### Original (per-action) — 6
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/notifications` | List |
| 2 | PATCH | `/notifications/{id}/read` | Mark one read |
| 3 | PATCH | `/notifications/mark-all-read` | Mark all read |
| 4 | GET | `/events/stream` | SSE live feed (Section 25) |
| 5 | GET | `/notifications/unread-count` | Badge count |
| 6 | DELETE | `/notifications/{id}` | Dismiss |

### Consolidated (recommended) — 5
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/notifications` | List |
| 2 | PATCH | `/notifications/read` | Accepts single id or array — merges #2,3 |
| 3 | GET | `/events/stream` | SSE live feed |
| 4 | GET | `/notifications/unread-count` | Badge count |
| 5 | PATCH | `/notifications/{id}/dismiss` | Dismiss |

---

## 15. audit (audit_logs, activity_logs)

### Original (per-action) — 6
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/audit-logs` | Query trigger-written log |
| 2 | GET | `/audit-logs/{id}` | Detail |
| 3 | GET | `/activity-logs` | Query app-written log |
| 4 | GET | `/activity-logs/{id}` | Detail |
| 5 | GET | `/audit-logs/export` | Compliance export |
| 6 | GET | `/activity-logs/export` | Compliance export |

### Consolidated (recommended) — 5
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/audit-logs` | Query, detail via `?id=` too |
| 2 | GET | `/audit-logs/{id}` | Detail |
| 3 | GET | `/activity-logs` | Query |
| 4 | GET | `/activity-logs/{id}` | Detail |
| 5 | GET | `/logs/export` | Generic export, `?type=audit\|activity` param (merges #5,6) |

---

## 16. reports (cross-module read-only)

### Original (per-action) — 8
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/reports/clinic-dashboard` | Clinic-level dashboard |
| 2 | GET | `/reports/region-dashboard` | Region-level dashboard |
| 3 | GET | `/reports/patient-outcomes` | Outcomes report |
| 4 | GET | `/reports/doctor-workload` | Workload report |
| 5 | GET | `/reports/store-sales` | Sales report |
| 6 | GET | `/reports/payment-summary` | Payment summary |
| 7 | GET | `/reports/export/{report_type}` | Export any report |
| 8 | GET | `/reports/audit-summary` | Audit rollup |

### Consolidated (recommended) — 6
| # | Method | Endpoint | Description |
|---|---|---|---|
| 1 | GET | `/reports/dashboard` | `?scope=clinic\|region\|system` param merges #1,2 |
| 2 | GET | `/reports/patient-outcomes` | Outcomes report |
| 3 | GET | `/reports/doctor-workload` | Workload report |
| 4 | GET | `/reports/store-sales` | Sales report |
| 5 | GET | `/reports/payment-summary` | Payment summary (audit-summary folds into audit module's own export, merges #8) |
| 6 | GET | `/reports/export/{report_type}` | Export any report |

---

## Grand Totals

| | Modules | Endpoints |
|---|---|---|
| Old system (measured) | 21 router files | 119 |
| New system — original (per-action) | 16 | **218** |
| New system — consolidated (recommended) | 16 | **148** |
| Reduction | — | **32%** |

*This document itemizes and refines the earlier rough estimate (~220/~156) given in conversation. Treat as a planning catalog — re-count once routers are implemented, and update this file alongside `Anava_Backend_Architecture_v1.md` Section 8 if consolidation rules change during build.*
