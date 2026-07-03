# Anava Clinic — Project Memory
Last Updated: June 2026
Read this file at the START of every new session. Contains everything needed to resume work without re-feeding context.

---

## WHAT IS THIS PROJECT

Modifying existing NeuroWellness backend (FastAPI + Supabase) into Anava Clinic.
Same codebase. New architecture, new flow, new DB schema.
80-90% code reuse. Features repositioned into logical clinical order.

Platform: Neurological care. Multi-region, multi-clinic.
Owner: Mana Health Sciences Group.

---

## FOLDER STRUCTURE

```
D:\PCS\backend-v2\
├── backend/        ← FastAPI code goes here
├── Documents/      ← All spec documents
│   ├── Anava_Master_Document_v1.docx   ← AUTHORITATIVE SOURCE
│   ├── Anava_Application_Bible_v3.docx ← Reference only
│   ├── Anava_Clinic_Lifecycle.docx     ← Reference only
│   └── Anava_Patient_Lifecycle.docx    ← Reference only
├── SQL/            ← DB schema SQL files go here
└── memory/         ← This folder. Session memory.

Existing codebase: D:\PCS\Backend_v1\neurowellness\
```

---

## TECH STACK (FINAL — CONFIRMED)

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (Python) — keep existing |
| Database | AWS RDS PostgreSQL — replaces Supabase |
| Auth | AWS Cognito User Pool — replaces Supabase Auth |
| File Storage | AWS S3 (presigned URLs only, never direct) |
| Migrations | Alembic — keep existing |
| Payments | Razorpay |
| AWS SDK | boto3 |
| ORM | SQLAlchemy (async) |

---

## DEVELOPMENT ORDER (NON-NEGOTIABLE)

1. Master Document finalized → 2. DB Schema (SQL) → 3. Backend Code
NO code before schema. NO schema changes without updating Master Doc first.

### Build Phases (inside Phase 3 — code):
1. Foundation (RDS schema, Cognito, IAM, S3, JWT middleware)
2. Super Admin Bootstrap
3. Admin Flows A-I (regions, clinics, staff)
4. Patient Registration J-M (6-step status machine, doctor allocation, CA protocol)
5. Appointment Block N-R (sessions 1-4, treatment, billing enforcement)
6a. Store — Orders (catalog, device/accessory order, Doctor approval, Razorpay)
6b. Store — Inventory (stock transfers, dispatch, receipt)
7. Follow-up + Transfers + Exit (S-V)
8. Consent System
9. Logging (audit_logs triggers + activity_logs)
10. S3 Integration
11. RLS Policies

---

## THE 7 ROLES

```
Super Admin      → system-wide, full access, central stock, bootstrap only
Regional Admin   → one per region, approves all staff, manages main branch stock, receives orders
Clinic Admin     → one per clinic, requests staff, daily ops, payment waivers
Doctor           → authorizes CA protocol, treatment plans, approves device sales
Clinical Asst    → DESIGNS assessment protocol, Session 1+3, administers treatment
Receptionist     → registers patients, books all appointments, initiates store orders
Patient          → receives care
```

---

## CLINIC LIFECYCLE

```
Region Setup (Super Admin: unique country+state pair)
    ↓
Clinic Request (clinic_requests table) → Super Admin approves
    ↓
status=setup (onboard Clinic Admin + min: 1 Doctor + 1 CA + 1 Receptionist)
    ↓
status=active (all patient care happens here)
    ↓
status=pending_closure (Regional Admin identifies receiving clinic → transfer all patients/staff)
    ↓
status=closed (TERMINAL — no records deleted)
```

3 clinic types: anava_owned | partner (needs clinic_join_anava + clinic_leave_anava consents) | mobile (future)
Main branch: is_main_branch=TRUE — holds regional stock

---

## PATIENT LIFECYCLE

```
Phase 1: Registration (6-step status machine)
  demographics_complete → disease_selected → consent_signed →
  anamnesis_complete → general_prs_complete → registration_complete

Phase 2: Pre-Clinical Setup (AUTO-TRIGGERED)
  Doctor auto-allocated (lowest active_patient_count)
  CA DESIGNS assessment protocol → Doctor AUTHORIZES it
  Session 1 blocked until assessment_protocol_requests.status='approved'

Phase 3: Appointment Block (core clinical unit)
  Session 1: CA only (EEG + Main PRS + diagnostics) — ZERO doctor contact
  Session 2: Doctor meets patient (Path A: treatment plan | Path B: more tests)
  Session 3: CA only (additional tests) — ZERO doctor contact — conditional
  Session 4: Doctor meets patient (reviews S1+S3, assigns plan) — always after S3
  Treatment Sessions: standard 5, Doctor can extend beyond 5 (extended sessions ALWAYS billed)
  Extended session: patient MUST pay before session starts (or Clinic Admin waives)

Phase 4: Home Treatment (patient uses own device at home)
  home_treatment_visit possible — does NOT start new block

Phase 5: Follow-up Cycles (new appointment_blocks record each time)
  Same session structure
  Follow-up PRS stage = 'followup'
  Treatment sessions: 1 to N (standard 5, Doctor decides, can extend, extended billed)
  treatment_plans chain via parent_plan_id

Phase 6: Discharge/Exit
  patient_clinic_exit consent → portal read-only → records permanent
```

Special Event (any phase): Patient Relocation Transfer
- NOT an exit
- patient_relocation_transfer consent signed
- Auto doctor allocation at new clinic MANDATORY
- Active block carries over, NEVER restarts
- Old clinic → read-only permanently

---

## APPOINTMENT BLOCK — CRITICAL RULES

- One active block per patient at a time
- Session 1 NOT complete until ALL reports generated
- Session 3 only if Session 2 outcome = 'additional_tests_requested'
- Session 4 ALWAYS follows Session 3, NEVER skipped
- Treatment sessions: standard=5, extended=6+ (always billed, payment required to start)
- treatment_plans.parent_plan_id NEVER NULL for follow-up plans
- device purchase ONLY after initial block treatment sessions complete

---

## CONSENT SYSTEM (8 types, NEVER deleted)

| Consent | When | Witness? |
|---------|------|---------|
| patient_onboarding | Registration Step 3 | YES (Receptionist) |
| patient_clinic_exit | Discharge | No |
| patient_clinic_transfer | Clinic closure transfer | No |
| patient_relocation_transfer | Patient relocates | No |
| staff_onboarding | Any staff joins | No |
| staff_offboarding | Any staff leaves | No |
| clinic_join_anava | Partner joins | No |
| clinic_leave_anava | Partner closes | No |

---

## LOGGING (both NEVER deleted, append-only)

- audit_logs: DB triggers only (every INSERT/UPDATE/DELETE on key tables)
- activity_logs: application writes (semantic events — who did what and why)
- No UPDATE or DELETE ever on either table

---

## PAYMENTS — RAZORPAY

- Session billing: per clinic config (Clinic Admin sets which phases are billable)
- Extended treatment sessions (6+): ALWAYS billed, must pay BEFORE session starts
- Store: Razorpay for both devices and accessories
- Webhook: Razorpay → /webhooks/razorpay → verify HMAC → update DB
- Payment waiver: Clinic Admin only

---

## STORE MODULE

Two sections:
1. Devices: Receptionist initiates, Doctor approval MANDATORY, only THIS clinic's patients
2. Accessories: Receptionist initiates, no doctor approval needed

Stock hierarchy:
```
Super Admin (central stock)
    ↓ dispatches to
Main Branch per Region (Regional Admin manages)
    ↓ fulfills orders
Individual Clinics (no permanent stock, order-triggered only)
    ↓
Patient collects IN-CLINIC (no home delivery)
```

Device order status flow:
pending_doctor_approval → doctor_approved → pending_dispatch →
dispatched_to_clinic → received_at_clinic → collected_by_patient

---

## S3 STRUCTURE

```
neurowellness-prod-bucket/
└── regions/{region_slug}/clinics/{clinic_id}/
    ├── patients/{patient_id}/
    │   ├── profile/
    │   ├── medical_history/
    │   ├── eeg_reports/
    │   ├── assessments/
    │   ├── prescriptions/
    │   ├── imaging/
    │   └── other_documents/
    └── staff/{staff_id}/documents/
```

- Backend enforces all paths. Frontend NEVER constructs S3 paths.
- Access via presigned URLs only (time-limited).
- On patient transfer: files STAY in original clinic folder. New clinic accesses via presigned URLs.
- Files NEVER deleted.

---

## SUPER ADMIN BOOTSTRAP

One-time only. AWS Secrets Manager approach.
1. Store creds in Secrets Manager: anava/superadmin-init
2. Run bootstrap_superadmin.py (idempotent)
3. Creates Cognito user + profiles + admins records
4. Cognito forces password change on first login
5. Delete the secret after confirmed working

---

## PRS SYSTEM CHANGES (vs existing NeuroWellness)

3 PRS stages:
- general_registration: registration Step 5, lighter battery
- main_clinical: Session 1, comprehensive, pre-authorized by Doctor
- followup: follow-up Session 1, tracks progress vs baseline

New tables/fields:
- patient_scale_assignments: which scales assigned to each patient per stage
- prs_scales.applicable_for: which stage each scale belongs to
- prs_assessment_instances.assessment_stage: distinguishes the 3 types

---

## ANAMNESIS CHANGES (vs existing NeuroWellness)

- block_id added (NULL at registration — no block exists yet)
- version field (increments on updates)
- responses stored as JSONB (structured medical history)
- Same questionnaire for all diseases

---

## ALL DB TABLES (quick reference)

Core: profiles, admins, regions, clinics, clinic_staff_assignments, doctors, clinical_assistants, receptionists, patients, patient_disease_selection

Requests: clinic_requests, staff_requests

Clinical: doctor_patient_assignments, assessment_protocol_requests, appointment_blocks, sessions, treatment_plans, treatment_sessions, device_assignments, patient_clinic_transfers

Consent: consent_templates, consent_records

PRS: prs_diseases, prs_scales, prs_questions, patient_scale_assignments, prs_assessment_instances, prs_responses

Anamnesis: anamnesis_assessments

Payments: payments

Store: products, inventory, stock_transfers, store_orders, order_items

Logging: audit_logs, activity_logs

Total: 37 tables

---

## WHERE WE ARE NOW

Status as of June 2026:
✅ Master Document v1 created (D:\PCS\backend-v2\Documents\Anava_Master_Document_v1.docx)
✅ All 4 documents verified consistent
✅ Memory files created
⏳ NEXT: Create DB Schema SQL files in D:\PCS\backend-v2\SQL\
⏳ THEN: Backend code in D:\PCS\backend-v2\backend\

---

## USER PREFERENCES (important)

- Explain things in simple, in-person conversational style when clarifying concepts
- User has limited prior backend experience — first-principles explanations when needed
- Development order is strict: Doc → Schema → Code (no shortcuts)
- No code written until schema is locked
- Caveman mode active (terse responses, drop filler)

---

## PATIENT SELF-REGISTRATION + RECEPTIONIST APPROVAL GATE (2026-07-03)

Two legitimate patient onboarding paths now exist, both using the same
6-step `registration_status` machine:
- **Staff-registered** (existing, unaffected): receptionist registers a
  walk-in patient in person — witness present, consent activates the
  account immediately, exactly as before.
- **Self-registered** (new): patient reaches the public site (`/register`),
  creates their own account (`POST /auth/register`, public, no auth), and
  works through demographics → disease selection → consent (no witness,
  since no one's physically present) → anamnesis → PRS assessment
  themselves — account stays **inactive** the whole time. Only once
  `registration_status='registration_complete'` does a receptionist see the
  request (`/receptionist/approvals`, already-built page, previously wired
  to dead stubs) and approve/reject it — approval is what finally flips
  `is_active=TRUE`. See `SQL/24_patient_self_registration.sql` for the new
  `patients.self_registered/approval_status/approved_by/approved_at/rejection_reason`
  columns and `PATCH /patients/{id}/approval`.
- Two adjacent pre-existing gaps had to be fixed for this to work at all
  (would've blocked BOTH onboarding paths, not just self-registration):
  scale auto-assignment (`PatientScaleAssignmentService.auto_assign_for_disease`
  existed but was never called from `select_disease()`) and no endpoint
  ever exposed a scale's question list (`GET /prs-catalog/scale-questions`
  added, wraps an already-existing repo method).
- Also fixed the literal original bug report: the public register page's
  clinic dropdown was empty because `GET /auth/clinics` didn't exist —
  added it (excludes only pending_closure/closed clinics, same convention
  `_ensure_clinic_ready_for_staff` uses elsewhere).

## STAFF ONBOARDING POLICY — CORRECTED (2026-07-03)

Code had drifted from what this doc's own "THE 7 ROLES" section always said
("Clinic Admin → ... requests staff", "Regional Admin → ... approves all
staff") by letting clinic_admin AND regional_admin both create doctor/CA/
receptionist profiles directly. Corrected back to the documented design —
see `SQL/22_staff_onboarding_lockdown.sql` for the full policy note:

- clinic_admin: submit staff_request only (open_position/candidate_referral)
  + PATCH-update existing staff (now audited via outbox events). No create,
  no delete.
- regional_admin: approves staff_requests; approval no longer auto-creates
  the profile — regional_admin creates it themselves as a separate step,
  full CRUD retained.
- super_admin: unchanged, full CRUD everywhere.
- New rule: doctor/CA/receptionist `profiles.email` must be on an official
  org domain (anavaclinic.com / anavaclinics.com / manahealthsciences.com) —
  patients and admin-tier accounts are exempt.
- Audit trail ("who referred/approved/when") was already fully covered by
  existing `staff_requests` columns — no schema change needed.
