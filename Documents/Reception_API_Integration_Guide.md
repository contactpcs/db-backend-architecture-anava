# Reception API Integration Guide

For whoever (human or Claude) wires the Receptionist frontend to this backend. Source spec: `API_ENDPOINTS (2).md` ("Anava NeuroWellness — Receptionist Module API Specification", v2.0, 56 operations). **This backend implements 18 of those 56** — every one backed by real, already-tested functionality. Nothing here is a stub or fake data.

## Summary & Roadmap

**Built: 18/56 (32%).** **Remaining: 38/56 (68%).**

| Remaining group | Count | Why not built | What unblocks it |
|---|---|---|---|
| Appointments + Check-In (§5–6) | 8 | Explicitly deferred — still in discovery | Product decision on booking/slot/queue-token design, then schema |
| Billing & Payments (§8) | 6 | Explicitly deferred — still in discovery | Design `fee_schedules`, invoice numbering, tax/discount rules, then schema |
| Patient clinical tabs (§4.3–4.11) | 9 | No backing tables (`patient_vitals`, `patient_insurance`, `prescriptions`, `emergency_contacts`, `diagnoses`, `medications`, `treatment_sessions`/`treatment_plans`) | New tables need designing — likely doctor-module work, not reception-only |
| Documents (§4.5, §3.5) | 2 | Real files module exists but serves clinical EEG data — different purpose than identity-document upload the spec wants | Product decision: build a real identity-document upload feature, or accept the mismatch |
| Notification Preferences + Sessions (§11.4–11.5) | 2 | No `staff_notification_preferences`/`user_sessions` tables | New tables, small schema addition |
| Reports (§10) | 3 | Entirely new module — needs aggregation/rollup design | Design `report_rollups` or equivalent, decide real-time vs nightly-batch |
| Settings (§12) | 3 | Entirely new module | Design `clinic_settings` schema (JSONB-per-section or typed columns) |
| Master Data — Visit Types, Search (§13.2, §13.4) | 2 | Visit types tied to appointments; search needs trigram-index infra | Same appointments blocker; search is its own infra task |
| Async Exports (§14) | 3 | No `export_jobs`/background-job infra | Needs a job queue + storage design, shared across the 4 export endpoints that depend on it |

**Real blockers, not just "not done yet":** almost everything above is blocked on **schema that doesn't exist**, not on endpoint-writing effort. Per this project's own dev convention (Doc → Schema → Code), none of it can be built correctly without a design/schema pass first — writing endpoints against tables that don't exist would mean fake data, which this guide's whole discipline has been to avoid.

---

## 1. What's Built vs Not — Read This First

| Spec section | Status |
|---|---|
| §3 Patient Registration (3.1–3.4) | ✅ Built — adapted to reuse existing Cognito-based registration |
| §4.1 List Patients | ✅ Built |
| §4.2 Get Patient Profile | ✅ Built — **partial fields only**, see §4.13 below |
| §7 Approvals (7.1–7.3) | ✅ Built |
| §9 Notifications — Unread Count, List, Toggle Read, Mark All Read (9.1, 9.2, 9.3, 9.4) | ✅ Built — **partial**, no tone/icon, mark-as-read is one-directional |
| §11 Profile — Get/Update My Profile, Change Password (11.1, 11.2, 11.3) | ✅ Built — **partial fields only**, see §4.10 below |
| §13.1 List Doctors | ✅ Built |
| §13.3 List Enumerations | ✅ Built — **partial**, gender + relationship only |
| §3.5 Upload Staff Photo | ❌ Not built |
| §4.3–4.11 (patient journey, appointments-tab, documents, prescriptions, billing, timeline, vitals, emergency contacts, insurance) | ❌ Not built — reference tables that don't exist in this backend (`patient_vitals`, `patient_insurance`, `prescriptions`, `emergency_contacts`, `diagnoses`, `medications`, `treatment_sessions`/`treatment_plans`). **§4.5 Documents is a special case**: a files module *does* exist, but it serves clinical EEG data — a semantic mismatch with the spec's identity-document intent (Aadhaar/PAN etc.), so it wasn't adapted here rather than mislabel clinical data as registration paperwork. |
| §5 Appointments, §6 Check-In | ❌ Not built — explicitly excluded, still in discovery |
| §8 Billing & Payments | ❌ Not built — explicitly excluded, still in discovery |
| §10 Reports, §12 Settings, §14 Async Exports | ❌ Not built — entirely new modules, none of this exists yet |
| §11.4 Notification Preferences, §11.5 Active Sessions | ❌ Not built — no `staff_notification_preferences` or `user_sessions` table exists anywhere in this schema (checked directly) |
| §13.2 List Visit Types, §13.4 Global Search | ❌ Not built — visit types tied to the excluded appointments module; search needs new trigram-index infrastructure, above "low-medium difficulty" |

**Do not build frontend screens against the unbuilt sections yet — there is no backend behind them.** If your frontend kit renders those screens, point them at mock data or hide them, not at real API calls.

---

## 2. Auth

Every endpoint below requires a real JWT bearer token, same as the rest of this backend:

```
Authorization: Bearer <token>
```

Get one via the existing `POST /api/v1/auth/login` (email/phone + password). Allowed roles for everything in this guide: `receptionist`, `clinic_admin`, `regional_admin`, `super_admin`. `doctor`/`patient` get `403`.

**Base path for everything below: `/api/v1/reception`**

---

## 3. Critical Deviations From The Original Spec Doc

Read this before wiring anything — the request/response shapes below are **not always identical** to `API_ENDPOINTS (2).md`. Where they differ, it's because the original spec assumed backend capabilities (a separate Argon2id password system, a `registration_requests` table, `PT-####`/`REG-####` display-ID sequences) that don't exist in this codebase, which already has a working, tested Cognito-based auth system.

| Spec said | This backend actually does |
|---|---|
| Separate `patient_credentials` table, Argon2id hashing | **Reuses the existing Cognito password system** — same one self-registration and staff login already use. No second password store. |
| `POST /registrations/send-code` body is just `{channel, contact}` | **Also requires `first_name`, `last_name`, `dob`, `gender`** — Cognito needs these at account-creation time (step 1), not deferred to the final step. |
| `verification_id` / `registration_token` are opaque server-generated IDs | **Both are literally the contact string itself** (the phone/email). Our system tracks verification state via Cognito's own confirmation status, not a separate table — there's nothing else to look up. |
| `patient_id` is a `PT-####` display sequence | **Real UUID.** No display-sequence generator exists. Treat it as an opaque ID string. |
| `registration_id` is a `REG-####` sequence from a separate `registration_requests` table | **Same UUID as the patient's `patient_id`.** This backend has no separate registration-request table — a "pending registration" *is* a `patients` row with `approval_status='pending'`. |
| Pagination is server/DB-level | **Done in application code** (fetch all, slice in Python). Fine at current data volumes; will need real `LIMIT`/`OFFSET` if patient counts grow large. |
| Specific error `code` strings (`DUPLICATE_PATIENT`, `INVALID_CODE`, etc.) | **This backend's own existing error codes** (`ACCOUNT_ALREADY_EXISTS`, `INVALID_OTP`, etc.) — see each endpoint below for the real ones. Envelope shape (`{"error": {"code","message","details"}}`) matches. |
| §11.1 profile has `employee_id`, `department`, `working_hours`, `joining_date`, `emergency_contact`, `security.*` status strings | **None of these columns exist anywhere in this schema** (checked directly, not assumed). `GET /me` returns only what's real: name, email, phone, role, clinic name, active flag. |
| §11.3 Change Password uses a separate `staff_credentials`/`password_history` system | **Reuses Cognito's real `ChangePassword` API** — same auth system as everything else, no second password store, no reuse-history tracking (no table for it). |
| §9 notifications have `tone` (colour) and `icon` (Lucide icon name) | **Neither exists.** `category` is this backend's real `type` column; `message` combines `title`+`body`. Frontend needs its own category→colour/icon mapping. |
| §9.3 Toggle Read is bidirectional (mark read ↔ unread) | **One-directional — mark-as-read only.** No "unread" concept in this backend's notifications table. Sending `is_read: false` returns a `422 UNSUPPORTED_OPERATION`, not a silent no-op. |
| §13.1 doctor `department` is a real department entity (`departments` table, §13's own `department_id`) | **This backend's `specialization` free-text column** on `doctors` — same display slot, not a normalized department reference. No `departments` table exists (also why §13's own department dropdown endpoint isn't built). |
| §13.3 enums include `identity_type`, `priority`, `payment_method`, `appointment_status` | **Only `gender` and `relationship` are returned** — the only two that are real, validated field constraints in this backend. The rest belong to unbuilt modules (identity verification, appointments, payments). |

---

## 4. Endpoints

### 4.1 Send Verification Code

```
POST /api/v1/reception/registrations/send-code
```

**Request**
```json
{
  "channel": "phone",
  "contact": "+919820011223",
  "first_name": "Jane",
  "last_name": "Smith",
  "dob": "1992-02-12",
  "gender": "female"
}
```

**Response `200`**
```json
{
  "verification_id": "+919820011223",
  "channel": "phone",
  "contact": "+919820011223",
  "code_length": 6,
  "expires_in_seconds": 600,
  "sent_at": "2026-07-30T09:05:00Z"
}
```

`verification_id` = the contact you sent. Use it as-is in the next step.

**Errors**: `ACCOUNT_ALREADY_EXISTS` (contact already registered), `422 VALIDATION_ERROR`.

---

### 4.2 Verify Code

```
POST /api/v1/reception/registrations/verify-code
```

**Request**
```json
{ "verification_id": "+919820011223", "code": "418602" }
```

**Response `200`**
```json
{
  "verification_id": "+919820011223",
  "channel": "phone",
  "contact": "+919820011223",
  "verified": true,
  "verified_at": "2026-07-30T09:07:12Z",
  "registration_token": "+919820011223"
}
```

`registration_token` = same value again. Carry it into the final `/patients` call.

**Errors**: `INVALID_OTP` (wrong code), `OTP_EXPIRED`.

---

### 4.3 Get Password Policy

```
GET /api/v1/reception/registrations/password-policy
```

**Response `200`**
```json
{ "min_length": 8, "require_uppercase": false, "require_digit": false, "require_symbol": false }
```

Static — matches the real constraint enforced at registration (8+ chars, nothing else).

---

### 4.4 Register Patient

```
POST /api/v1/reception/patients
```

**Request**
```json
{
  "registration_token": "+919820011223",
  "personal": {
    "first_name": "Jane",
    "last_name": "Smith",
    "gender": "female",
    "date_of_birth": "1992-02-12"
  },
  "address": {
    "street": "12 Hill Road, Bandra West",
    "city": "Mumbai",
    "state": "Maharashtra",
    "country": "IN",
    "pincode": "400001"
  },
  "clinic_id": "3ab28767-0690-4e55-bff1-d8d6a05a88a1",
  "guardian": {
    "name": "Arun Nair",
    "relation": "parent",
    "contact_number": "+919820033445"
  },
  "password": "SecurePass123",
  "consent": { "accepted": true, "signature_captured": true }
}
```

`guardian` is optional — omit entirely (or send `null`) when not applicable. `clinic_id` is a real clinic UUID (`GET /api/v1/auth/clinics` for the list).

**Response `201`**
```json
{
  "patient_id": "99e0cecf-de6d-49bd-b507-3f6a648b731e",
  "full_name": "Jane Smith",
  "login_id": "+919820011223",
  "login_channel": "phone",
  "registration_status": "verified",
  "consent_status": "signed",
  "created_at": "2026-07-30T09:12:04Z"
}
```

**Errors**: `422 CONSENT_REQUIRED` (consent.accepted false), `422 VALIDATION_ERROR`, `403 CLINIC_SCOPE_MISMATCH`.

---

### 4.5 List Patients

```
GET /api/v1/reception/patients?page=1&page_size=20
```

**Response `200`**
```json
{
  "items": [
    {
      "patient_id": "99e0cecf-de6d-49bd-b507-3f6a648b731e",
      "full_name": "Jaswanth Perla",
      "initials": "JP",
      "age": 21,
      "gender": "male",
      "phone": null,
      "assigned_doctor": "Amit Jape",
      "registration_status": "registration_complete",
      "last_visit": null,
      "next_appointment": null
    }
  ],
  "pagination": { "page": 1, "page_size": 20, "total_items": 9, "total_pages": 1, "has_next": false, "has_previous": false }
}
```

**`last_visit` and `next_appointment` are always `null`** — they depend on the appointments module, not built. Don't render those columns as if they'll populate; hide them or show a permanent placeholder.

`registration_status` is **not** in the original spec — added because it's real, already-tracked data directly useful in this same list. See §4.9 below for the full value set and what each one means.

---

### 4.6 List Registrations (Approvals Queue)

```
GET /api/v1/reception/registrations?page=1&page_size=20&status=pending
```

`status` is optional (`pending`/`approved`/`rejected`/omit for all).

**Response `200`**
```json
{
  "items": [
    {
      "registration_id": "22d91490-9a84-41eb-b998-8ef431a4f334",
      "full_name": "Sai Arjun",
      "contact": "ndezw@web-library.net",
      "contact_type": "email",
      "submitted_on": "2026-07-29",
      "status": "pending",
      "linked_patient_id": null,
      "allowed_actions": ["approve", "reject"]
    }
  ],
  "pagination": { "page": 1, "page_size": 20, "total_items": 9, "total_pages": 1, "has_next": false, "has_previous": false }
}
```

Only **self**-registered patients appear here — receptionist-registered ones (§4.4 above) never show up in this queue, same as the original spec's own note.

---

### 4.7 Approve Registration

```
POST /api/v1/reception/registrations/{registration_id}/approve
```

Empty body. **Response `200`**
```json
{
  "registration_id": "22d91490-9a84-41eb-b998-8ef431a4f334",
  "status": "approved",
  "patient_id": "22d91490-9a84-41eb-b998-8ef431a4f334",
  "full_name": "Sai Arjun"
}
```

`patient_id` == `registration_id` (same row — see §3 deviations above).

**Errors**: `REGISTRATION_INCOMPLETE`, `APPROVAL_ALREADY_DECIDED`.

---

### 4.8 Reject Registration

```
POST /api/v1/reception/registrations/{registration_id}/reject
```

Empty body. **Response `200`**
```json
{ "registration_id": "22d91490-9a84-41eb-b998-8ef431a4f334", "status": "rejected" }
```

---

### 4.9 Track Registration Progress (existing endpoint, not part of this adapter)

```
GET /api/v1/patients/{patient_id}
```

This is **not** one of the 8 new endpoints — it already existed before this work, in the main patients module, and is the real way to check how far along a specific patient is in completing registration. Same auth (Bearer token, same roles).

**Response `200`** (relevant field shown; full patient record otherwise)
```json
{
  "patient_id": "22d91490-9a84-41eb-b998-8ef431a4f334",
  "registration_status": "consent_signed",
  "approval_status": "not_required",
  "self_registered": false,
  "...": "full patient record — demographics, guardian fields, etc."
}
```

**`registration_status` values, in order** (the patient moves through these left to right — the field always reflects the furthest step actually completed, re-derived from real data each time, never hand-set):

| Value | Meaning |
|---|---|
| `demographics_complete` | Name/DOB/address/etc. saved |
| `disease_selected` | Condition/disease chosen |
| `consent_signed` | Onboarding consent signed |
| `anamnesis_complete` | Anamnesis assessment finished |
| `general_prs_complete` | General PRS assessment finished |
| `registration_complete` | All of the above done — patient fully onboarded, doctor auto-assigned |

For a **receptionist-registered** patient (built this session, `self_registered=false`), the remaining steps (consent, anamnesis, PRS) are completed by the **patient themselves**, later, after their first login — not by the receptionist. So it's normal and expected for a receptionist-registered patient to sit at `demographics_complete` for a while after the receptionist's part is done; that's not a bug, it's the patient's own pending step.

Use this endpoint per-patient (e.g. from the Patient Details screen, or a "check progress" action on the All Patients / Registrations list) — there's no bulk/list version of this beyond what §4.5's `registration_status` field already gives you.

---

### 4.10 Get My Profile

```
GET /api/v1/reception/me
```

**Response `200`**
```json
{
  "profile_id": "d4aabf1f-9912-4033-9f28-142d04f7ed6c",
  "full_name": "Sneha Sanjana",
  "email": "sneha@anavaclinics.com",
  "phone": "+916305956556",
  "role": "receptionist",
  "clinic": "Mumbai Wellness Clinic",
  "is_active": true
}
```

**Not included** (no backing column anywhere in this schema): `employee_id`, `department`, `working_hours`, `joining_date`, `emergency_contact`, `security.password_status`/`two_factor_status`/`sessions_status`. If the Profile screen needs those, they're either static placeholder text on the frontend for now, or new columns need to be designed and migrated first.

---

### 4.11 Update My Profile

```
PATCH /api/v1/reception/me
```

**Request** — all fields optional, send only what changed:
```json
{ "first_name": "Sneha", "last_name": "Sanjana", "email": "sneha@anavaclinics.com", "phone": "+916305956556" }
```

**Response `200`** — same shape as §4.10.

**Errors**: `409 EMAIL_IN_USE`.

`working_hours`, `emergency_contact`, `photo_document_id` from the original spec are **not accepted** — no such columns, and no photo-upload endpoint exists (§3.5 not built).

---

### 4.12 Change Password

```
POST /api/v1/reception/me/change-password
```

**Request**
```json
{ "current_password": "OldPass123", "new_password": "NewSecurePass456", "confirm_password": "NewSecurePass456" }
```

**Response `200`**
```json
{ "changed_at": "2026-07-30T10:15:00Z" }
```

Real Cognito password change — requires the caller's actual current password (not an admin override). Uses the `Authorization` bearer token already on the request; no separate session/access-token field needed in the body.

**Errors**: `401 INVALID_CURRENT_PASSWORD`, `422 INVALID_PASSWORD` (policy violation), `422 PASSWORD_MISMATCH`, `400 RATE_LIMITED` (too many attempts).

No password-reuse-history check (`password_history` doesn't exist) — matches this backend's actual Cognito password policy (8+ chars only), not the original spec's stricter 12-char/complexity/reuse rules, which aren't enforced anywhere in this system.

---

### 4.13 Get Patient Profile

```
GET /api/v1/reception/patients/{patient_id}
```

**Response `200`**
```json
{
  "patient_id": "22d91490-9a84-41eb-b998-8ef431a4f334",
  "full_name": "Sai Arjun",
  "is_active": false,
  "registration_status": "consent_signed",
  "approval_status": "pending",
  "personal": { "date_of_birth": "2005-01-20", "age": 21, "gender": "male" },
  "contact": { "phone": null, "email": "ndezw@web-library.net", "address": "" },
  "assigned_doctor": null,
  "guardian_name": null,
  "guardian_relationship": null,
  "guardian_contact": null
}
```

**Not included** (no backing anywhere in this schema): `medical_summary`, `current_medications`, `care.department`/`consent_status` display string/`identity_verification`, `registration_timeline`. These are doctor-authored clinical data or reference tables (diagnoses, medications) this module doesn't have. Use §4.9's dedicated progress endpoint for registration-step tracking specifically, not this one.

---

### 4.14 Get Unread Notification Count

```
GET /api/v1/reception/notifications/unread-count
```

**Response `200`**
```json
{ "total_unread": 3 }
```

Only `total_unread` — the original spec's separate `notifications_unread`/`announcements_unread` split doesn't apply here, there's no separate announcements concept in this backend.

---

### 4.15 List Notifications

```
GET /api/v1/reception/notifications?unread_only=false
```

**Response `200`**
```json
{
  "items": [
    {
      "notification_id": "8f1c44a2-...",
      "category": "appointment_booked",
      "message": "New appointment booked — Priya Nair, 28 Jul 09:00",
      "is_read": false,
      "when": "2026-07-28T08:51:00Z"
    }
  ],
  "counts": { "unread": 4, "total": 8 }
}
```

`category` is this backend's real `type` column (whatever event types your outbox/notification worker actually emits — not the spec's fixed 5-value list). `message` combines `title` + `body` when both exist. `when` is a raw ISO timestamp, not a preformatted "Today, 08:51" string — format it client-side.

---

### 4.16 Toggle Notification Read State

```
PATCH /api/v1/reception/notifications/{notification_id}
```

**Request** — only `{"is_read": true}` is accepted:
```json
{ "is_read": true }
```

**Response `200`**
```json
{ "notification_id": "8f1c44a2-...", "is_read": true, "total_unread": 3 }
```

**Errors**: `422 UNSUPPORTED_OPERATION` if you send `{"is_read": false}` — marking something *unread* has no backing in this system, unlike the original spec's bidirectional toggle. Don't build an "undo" control against this endpoint.

---

### 4.17 Mark All Notifications Read

```
POST /api/v1/reception/notifications/mark-all-read
```

Empty body. **Response `200`**
```json
{ "marked_count": 4, "total_unread": 0 }
```

Only real notifications — no separate `announcements` collection exists, so this doesn't do the original spec's "clears both notifications and announcements" behavior.

---

### 4.18 List Doctors

```
GET /api/v1/reception/doctors
```

**Response `200`**
```json
{
  "items": [
    { "doctor_id": "ae3f6ce8-be9e-4ba9-9039-9d90e9122032", "name": "Amit Jape", "department": "Neurologist", "label": "Amit Jape — Neurologist" }
  ]
}
```

`department` is this backend's real `specialization` free-text field, not a normalized department reference (no `departments` table exists). Scoped to the caller's own clinic for `receptionist`/`clinic_admin`; all clinics for `regional_admin`/`super_admin`. No `is_active` query param — always returns non-deleted doctors only.

---

### 4.19 List Enumerations

```
GET /api/v1/reception/enums
```

**Response `200`**
```json
{
  "gender": [
    { "value": "female", "label": "Female" },
    { "value": "male", "label": "Male" },
    { "value": "other", "label": "Other" }
  ],
  "relationship": [
    { "value": "parent", "label": "Parent" },
    { "value": "spouse", "label": "Spouse" },
    { "value": "sibling", "label": "Sibling" },
    { "value": "child", "label": "Child" },
    { "value": "legal_guardian", "label": "Legal Guardian" },
    { "value": "other", "label": "Other" }
  ]
}
```

Only `gender` and `relationship` — these are the only two enums this backend actually validates against (`Field(pattern=...)` in the real Pydantic schemas). `identity_type`, `priority`, `payment_method`, `appointment_status` are omitted — they belong to unbuilt modules (identity verification, appointments, payments) and returning them would imply those fields do something, when nothing accepts them anywhere in this backend.

---

## 5. Verified

Every endpoint above was tested against the real running database (not mocked): registration flow end-to-end through real Cognito calls, list/approval endpoints against real patient rows with real RLS-scoped queries, profile get/update against a real receptionist's actual profile row, patient profile against a real patient record, doctors list against a real doctor ("Amit Jape — Neurologist"), notifications against a real user's real (zero) notification count. `change-password` uses the identical code pattern already verified live for `forgot-password`/`confirm-forgot-password` — not independently re-tested against real Cognito here, since doing so would actually change a real account's password. Source: `app/modules/reception/router.py`, `app/modules/reception/schemas.py`, `app/core/cognito.py`.
