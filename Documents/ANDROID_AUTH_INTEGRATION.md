# Anava Backend — Auth Integration Spec (for Android/Kotlin client)

Source: `backend/app/modules/auth/`, `backend/app/core/{middleware,security,exceptions,cognito}.py`, `backend/app/config.py`, `backend/app/main.py`. Generated 2026-08-15 from the actual FastAPI source, not docs — treat this as ground truth over any older doc.

---

## 1. Base URL / environment

- **Prod base URL:** `https://api.anavaclinics.com`
- **API prefix:** everything is mounted under `/api/v1`, e.g. full login URL = `https://api.anavaclinics.com/api/v1/auth/login`
- **Staging URL:** not present anywhere in this repo (no staging config file, no second ALB target found). Confirm with backend team before hardcoding a staging flavor — do not guess a subdomain.
- **API versioning:** no header-based versioning. Version is baked into the path prefix (`/api/v1/...`). There is no `Accept-Version` or similar header.
- **Health checks** (no auth, useful for connectivity checks from the app): `GET /health` (liveness), `GET /health/ready` (DB check).

`auth_mode` is a backend deploy-time config, not something the client sends. In production it is `"cognito"`. This means:

- `POST /auth/register`, `POST /auth/local-login` are **dev-only** and return `404` in prod. Ignore them for the Android build.
- Use the flows marked "cognito mode" below.

You can confirm current mode by calling the public endpoint:

`GET /api/v1/auth/config` → `{"auth_mode": "cognito"}`

---

## 2. POST /auth/login

Real Cognito password login. Works for staff and patients alike. No auth header required (public route).

**URL:** `POST https://api.anavaclinics.com/api/v1/auth/login`

### Request body

```json
{
  "username": "patient@example.com",
  "password": "correct-horse-battery-staple"
}
```

| field        | type   | required | notes                                                                                                     |
| ------------ | ------ | -------- | --------------------------------------------------------------------------------------------------------- |
| `username` | string | yes      | Email OR E.164 phone number — either works once that channel is a verified alias on the Cognito account. |
| `password` | string | yes      | plaintext, sent over HTTPS only                                                                           |

### Success response — `200 OK`

```json
{
  "access_token": "eyJraWQiOiJ...<JWT>",
  "token_type": "bearer",
  "refresh_token": "eyJjdHkiOiJ...<opaque-cognito-refresh-token>"
}
```

`refresh_token` may be `null` in some flows (e.g. local dev token issuance) but on real Cognito login it is populated. `access_token` is a Cognito-issued RS256 JWT (see §4 for expiry).

### Error responses

All errors — across **every** endpoint in this backend — use one envelope shape (see §6). For login specifically:

**Bad credentials (wrong password / unknown user) — `403 Forbidden`, not 401:**

```json
{
  "error": {
    "code": "INVALID_CREDENTIALS",
    "message": "Incorrect email/phone or password",
    "details": []
  }
}
```

⚠️ Important gotcha: login failure is `403`, **not** `401`. Do not special-case `401` as "bad password" in your client — see §4 for what actually triggers 401.

**First-login password change required (staff account created via AdminCreateUser, still on Cognito's auto-emailed temp password) — `400 Bad Request`:**

```json
{
  "error": {
    "code": "NEW_PASSWORD_REQUIRED",
    "message": "Password change required (NEW_PASSWORD_REQUIRED)",
    "details": [
      {"session": "AYABeH...<cognito-session-token>"}
    ]
  }
}
```

On this response, show a "set new password" screen and call `POST /auth/login/new-password` with the `session` value from `details[0].session`. This is unlikely to be hit by patient accounts (patients self-register with their own real password), but implement it defensively for staff-role logins if the app ever supports staff login.

**Generic Cognito failure (rate limiting, service error, etc.) — `403 Forbidden`:**

```json
{
  "error": {
    "code": "COGNITO_LOGIN_FAILED",
    "message": "Login failed: <cognito client error string>",
    "details": []
  }
}
```

**Validation error (missing/malformed field) — `422 Unprocessable Entity`:**

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "<pydantic validation message>",
    "details": []
  }
}
```

### Related: POST /auth/login/new-password

Only needed if you hit `NEW_PASSWORD_REQUIRED` above.

```json
// request
{
  "username": "staff@anavaclinics.com",
  "new_password": "newSecurePassword123",
  "session": "AYABeH...<from the login error's details[0].session>"
}
```

Success response: same `TokenResponse` shape as `/auth/login` (`200`, `access_token`/`token_type`/`refresh_token`).

### Related: forgot password (not core to login wiring, included for completeness)

`POST /auth/forgot-password/start` — body `{"username": "..."}`, always `204 No Content` regardless of whether the account exists (deliberate, avoids user enumeration).

`POST /auth/forgot-password/confirm`:

```json
{
  "username": "patient@example.com",
  "code": "123456",
  "new_password": "newSecurePassword123",
  "confirm_password": "newSecurePassword123"
}
```

`204 No Content` on success. `400` with `code: "PASSWORD_MISMATCH"` if the two passwords don't match (checked before it even calls Cognito).

---

## 3. GET /auth/me

The "who am I" call — call this immediately after login to get role/profile/patient state. Requires `Authorization: Bearer <access_token>` header (see §4).

**URL:** `GET https://api.anavaclinics.com/api/v1/auth/me`

### Success response — `200 OK` (patient example, mid self-registration)

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "email": "patient@example.com",
  "first_name": "Asha",
  "last_name": "Rao",
  "role": "patient",
  "clinic_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "region_id": null,
  "is_active": false,
  "consent_signed": false,
  "consent_type_required": "patient_onboarding",
  "self_registered": true,
  "patient_id": "c4a760a8-dbcf-4e1e-bd4e-b4e2c62e2b1e",
  "registration_status": "pending_disease_selection",
  "doctor_id": null,
  "email_verified": true,
  "phone_verified": false
}
```

### Full field reference (every field always present, nullable ones shown as such)

| field                     | type              | notes                                                                                                                                                                                                                                                                                                         |
| ------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                    | UUID string       | `profiles.id` — the internal identity ID. Use this, not patient_id, as the generic "user id".                                                                                                                                                                                                              |
| `email`                 | string            |                                                                                                                                                                                                                                                                                                               |
| `first_name`            | string            |                                                                                                                                                                                                                                                                                                               |
| `last_name`             | string            |                                                                                                                                                                                                                                                                                                               |
| `role`                  | string            | one of:`patient`, `doctor`, `clinical_assistant`, `receptionist`, `clinic_admin`, `regional_admin`, `super_admin`                                                                                                                                                                               |
| `clinic_id`             | UUID or`null`   | tenant scope — clinic the user is scoped to (staff) or patient's primary clinic                                                                                                                                                                                                                              |
| `region_id`             | UUID or`null`   | only populated for regional/super admin roles                                                                                                                                                                                                                                                                 |
| `is_active`             | bool              | `false` until onboarding consent is signed (or, for self-registered patients, until the full registration wizard + receptionist approval completes)                                                                                                                                                         |
| `consent_signed`        | bool              |                                                                                                                                                                                                                                                                                                               |
| `consent_type_required` | string or`null` | `"patient_onboarding"` / `"staff_onboarding"` when `is_active` is false; `null` once active                                                                                                                                                                                                           |
| `self_registered`       | bool              | true if patient signed up via the public OTP wizard rather than being staff-registered                                                                                                                                                                                                                        |
| `patient_id`            | UUID or`null`   | **public** patient ID — only set when `role == "patient"`. All patient-scoped endpoints (`/patients/{patient_id}/...`) key off this, not `id`.                                                                                                                                                   |
| `registration_status`   | string or`null` | patient-only; drives which step of the signup wizard to resume (e.g.`pending_disease_selection`, `pending_consent`, `pending_anamnesis`, `pending_prs`, `registration_complete`, or similar — check `patients` table / `patients/schemas.py` for the full enum if you need to branch UI on it) |
| `doctor_id`             | UUID or`null`   | only set when`role == "doctor"` — the public doctor ID used in `/doctors/{doctor_id}/...` paths                                                                                                                                                                                                          |
| `email_verified`        | bool              | always`true` outside of (cognito mode + role=='patient'); meaningful only for cognito-mode patients who signed up via one channel and haven't verified the other                                                                                                                                            |
| `phone_verified`        | bool              | same caveat as above                                                                                                                                                                                                                                                                                          |

### Error responses

`401` if no/malformed Authorization header, or token invalid/expired at the middleware layer in certain paths — see §4 for the exact split between 401 and 403 on bad tokens. `404` with `code: "PROFILE_NOT_FOUND"` if the token's subject has no matching `profiles` row (shouldn't normally happen for a token you just got from `/login`).

---

## 4. Token handling

**Header:** `Authorization: Bearer <access_token>` — standard bearer scheme, exact header name `Authorization`, exact prefix `Bearer ` (capital B, one space). This is required on every endpoint except the public allowlist (login, register, forgot-password, health, config, clinics list, docs).

**Token format:** Cognito-issued JWT (RS256, validated against Cognito's JWKS in prod). The backend does not mint or re-sign tokens — it passes through the raw Cognito `AccessToken`.

**Expiry:** Not set in this backend's code — Cognito's own App Client token expiry setting controls it (default Cognito access token TTL is 60 minutes unless the User Pool App Client is configured otherwise). This repo does not expose an `expires_in` field in the `/auth/login` response — the backend's `TokenResponse` schema is only `{access_token, token_type, refresh_token}`, no expiry field. **You will need to either decode the JWT's `exp` claim client-side, or ask the backend/infra team for the configured Cognito App Client access-token TTL.** Do not assume 3600s.

**Refresh token flow:** `refresh_token` is returned by `/auth/login` (Cognito's real refresh token) but **there is currently no backend endpoint that accepts it**. Grepped the whole `auth` router — no `/auth/refresh` or similar exists yet. The `TokenResponse` schema comment literally says: *"Returned now so it's available once silent-refresh is built; nothing consumes it yet."* So:

- Store the refresh token (SharedPreferences/EncryptedSharedPreferences or Android Keystore-backed storage), since it'll be needed once a refresh endpoint ships.
- For now, **there is no silent-refresh path**. On token expiry, force logout and send the user back to the login screen. Do not build a refresh call against a URL that doesn't exist yet.

**What happens on 401 today:**

- `401` with `code: "MISSING_TOKEN"` — no `Authorization` header at all, or it doesn't start with `Bearer `.
- Token present but invalid/expired — this actually raises the app's `PermissionError_` exception internally, which maps to **`403`**, `code: "INVALID_TOKEN"`, not 401. (See `core/security.py::verify_token` → raises `PermissionError_`, and `core/exceptions.py` → `PermissionError_.status_code = 403`.)
- So in practice: treat **both 401 and 403 with `code` in `{"MISSING_TOKEN", "INVALID_TOKEN", "AUTHENTICATION_REQUIRED"}`** as "not authenticated, force logout / send to login screen." Don't rely on the numeric status code alone to decide "log the user out" — check the `error.code` string too.
- `403` with `code: "CONSENT_REQUIRED"` is a **different** case — it means the token is valid but the account hasn't signed onboarding consent yet. Do NOT log the user out for this; route them into the consent flow instead. It only fires outside a small allowlist of consent/patient-registration paths (see `PATIENT_SELF_REGISTRATION_PATH_PREFIXES` in `core/middleware.py` if you need the exact path list).
- `403` with `code: "PROFILE_NOT_FOUND"` — token is valid Cognito-wise but no matching `profiles` row exists. Treat as auth failure / logout.

Recommended Android-side rule: build one interceptor that inspects `error.code` on any 401/403, and only force-logout on `{MISSING_TOKEN, INVALID_TOKEN, AUTHENTICATION_REQUIRED, PROFILE_NOT_FOUND}`; leave `CONSENT_REQUIRED` and `PERMISSION_DENIED` (a real role/scope denial, distinct from auth) to be handled by the calling screen instead.

---

## 5. CORS / required headers

- **No custom API key, client-id, or app-version header is required or read by the backend.** Grepped `core/middleware.py` and `config.py` — the only header the backend inspects for auth purposes is `Authorization`. CORS is configured with an explicit origin allowlist (`cors_allowed_origins` — currently only localhost dev origins in the sample config; prod origins are set via env var, not in this repo) but **CORS is a browser-only mechanism and does not apply to a native Android HTTP client** — you can ignore it entirely.
- Backend does read `X-Request-ID` if you send it (optional) — it echoes it back and uses it for log correlation. Not required, but nice to send a UUID per-request if you want easier support/debugging correlation with backend logs.
- No API-key header, no `X-Client-Id`, no `X-App-Version` gate anywhere in the codebase today.

---

## 6. Standard error envelope

Every error response across the **entire** API (not just auth) uses this exact shape — build one parser for it:

```json
{
  "error": {
    "code": "SOME_MACHINE_READABLE_CODE",
    "message": "Human-readable message, safe to show or log",
    "details": []
  }
}
```

- `error.code`: stable machine-readable string, e.g. `VALIDATION_ERROR`, `INVALID_CREDENTIALS`, `NOT_FOUND`, `PERMISSION_DENIED`, `AUTHENTICATION_REQUIRED`, `CONFLICT`, `BUSINESS_RULE_VIOLATION`, `EXTERNAL_SERVICE_UNAVAILABLE` (503, retryable), `INTERNAL_ERROR` (500), `HTTP_ERROR` (fallback wrapper for any stray framework-level HTTPException).
- `error.message`: displayable string.
- `error.details`: usually `[]`, but can carry structured extra data — e.g. login's `NEW_PASSWORD_REQUIRED` puts `[{"session": "..."}]` here. Treat as `List<Any>` / free-form JSON array, not a fixed shape.

Status code ↔ default code mapping (from `core/exceptions.py`):

| status | default code                     | exception class          |
| ------ | -------------------------------- | ------------------------ |
| 400    | `BUSINESS_RULE_VIOLATION`      | `BusinessRuleError`    |
| 401    | `AUTHENTICATION_REQUIRED`      | `AuthenticationError`  |
| 403    | `PERMISSION_DENIED`            | `PermissionError_`     |
| 404    | `NOT_FOUND`                    | `NotFoundError`        |
| 409    | `CONFLICT`                     | `ConflictError`        |
| 422    | `VALIDATION_ERROR`             | `ValidationError`      |
| 503    | `EXTERNAL_SERVICE_UNAVAILABLE` | `ExternalServiceError` |
| 500    | `INTERNAL_ERROR`               | `FatalError`           |

Individual endpoints override the `code` string (not the status) for specificity — e.g. 403/`INVALID_CREDENTIALS`, 403/`INVALID_TOKEN`, 404/`PROFILE_NOT_FOUND`. Always branch UI logic on `error.code`, use `status_code` only for coarse retry/backoff behavior.

---

## 7. Other endpoints already consumed (relevant to a patient dashboard)

These are **not** part of the website's current auth flow but are the natural next calls for a patient-facing Android dashboard, since the mobile app is presumably patient-facing. All require the `Authorization: Bearer <token>` header and (for the `/me/...` ones) the caller's role must be `patient`.

### GET /patients/

**URL:** `GET /api/v1/patients/{patient_id}`

Full patient profile record. Success `200`:

```json
{
  "patient_id": "c4a760a8-dbcf-4e1e-bd4e-b4e2c62e2b1e",
  "profile_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "mrn": "MRN-000123",
  "registration_status": "registration_complete",
  "primary_clinic_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "primary_doctor_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "emergency_contact_name": "Rohit Rao",
  "emergency_contact_phone": "+919876543210",
  "registration_completed_at": "2026-06-01T10:15:00Z",
  "created_at": "2026-05-28T09:00:00Z",
  "first_name": "Asha",
  "last_name": "Rao",
  "email": "patient@example.com",
  "phone": "+919812345678",
  "gender": "female",
  "dob": "1990-04-12",
  "address": "12 MG Road",
  "city": "Bengaluru",
  "state": "Karnataka",
  "country": "India",
  "pincode": "560001",
  "profile_is_active": true,
  "doctor_name": "Dr. Kavita Iyer",
  "doctor_first_name": "Kavita",
  "doctor_last_name": "Iyer",
  "doctor_phone": "+919900011122",
  "doctor_specialization": "Dermatology"
}
```

(Schema continues past `doctor_specialization` in `patients/schemas.py` — self-registration/approval fields exist too; read `backend/app/modules/patients/schemas.py::PatientRead` directly if you need the complete tail, this doc captures the dashboard-relevant portion.)

### GET /me/appointments

**URL:** `GET /api/v1/me/appointments?include_past=false`

Patient's own appointments (all types, including protocol-generated planned sessions). Query param `include_past` (bool, default `false`).

Success `200` — array of appointment objects:

```json
[
  {
    "appointment_id": "e2a1a1a0-1111-4a3b-8c2d-abcdefabcdef",
    "clinic_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "patient_id": "c4a760a8-dbcf-4e1e-bd4e-b4e2c62e2b1e",
    "patient_name": "Asha Rao",
    "doctor_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "doctor_name": "Dr. Kavita Iyer",
    "doctor_public_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
    "responsible_doctor_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "responsible_doctor_name": "Dr. Kavita Iyer",
    "ca_id": null,
    "cycle_id": null,
    "plan_id": null,
    "protocol_id": null,
    "session_number": null,
    "appointment_date": "2026-08-20",
    "start_time": "10:30:00",
    "end_time": "11:00:00",
    "hold_expires_at": null,
    "appointment_type": "follow_up",
    "status": "paid",
    "reason": "Routine follow-up",
    "patient_complaint": null,
    "notes": null,
    "cancellation_reason": null,
    "booked_by": "c4a760a8-dbcf-4e1e-bd4e-b4e2c62e2b1e",
    "booked_by_role": "patient",
    "cancelled_by": null,
    "rescheduled_from": null,
    "rescheduled_to": null,
    "checked_in_at": null,
    "started_at": null,
    "completed_at": null,
    "created_at": "2026-08-10T08:00:00Z"
  }
]
```

Notes:

- `appointment_type` ∈ `initial | follow_up | device_session | protocol_followup`.
- `doctor_id`/`doctor_name` can be `null` for a `device_session` (no doctor attached — a clinical assistant runs it); use `responsible_doctor_*` if you need "which doctor is behind this" regardless of type.
- A `"planned"`-status row (protocol-generated future session) can have `start_time`/`end_time` both `null` — date is set, time isn't chosen yet.

### GET /me/appointments/availability

**URL:** `GET /api/v1/me/appointments/availability?from_date=2026-08-20&to_date=2026-08-27`

Query params: `from_date` (required, `YYYY-MM-DD`), `to_date` (optional, defaults to `from_date`).

Success `200` — array of open slots on the patient's allocated doctor's calendar:

```json
[
  {
    "date": "2026-08-20",
    "start_time": "09:00:00",
    "end_time": "09:30:00",
    "is_available": true
  },
  {
    "date": "2026-08-20",
    "start_time": "09:30:00",
    "end_time": "10:00:00",
    "is_available": false
  }
]
```

### Booking endpoints (for later, not needed for a read-only dashboard)

- `POST /me/appointments/initial` — books an initial visit
- `POST /me/appointments/follow-up` — books a follow-up
- both take a `MyAppointmentBook` body and return a single `AppointmentRead` object (same shape as above) with `201`.
- `PATCH /me/appointments/{appointment_id}/reschedule`, `PATCH /me/appointments/{appointment_id}/cancel`, `PATCH /me/appointments/{appointment_id}/claim-slot` also exist for a fuller booking UI later — see `backend/app/modules/scheduling/router.py` and `schemas.py` for their exact request bodies when you get to that screen.

---

## 8. Quick-reference summary for the Android networking layer

1. Base URL: `https://api.anavaclinics.com/api/v1` (staging URL unconfirmed — ask backend team).
2. `POST /auth/login` with `{username, password}` → store `access_token` + `refresh_token`.
3. Attach `Authorization: Bearer <access_token>` to every subsequent call.
4. Call `GET /auth/me` right after login to get role/patient_id/registration_status and drive navigation.
5. No refresh-token endpoint exists yet — on token expiry, force logout, no silent refresh.
6. One error parser suffices: always `{"error": {"code", "message", "details"}}`.
7. No custom headers beyond `Authorization` are required.
8. For dashboard: `GET /patients/{patient_id}`, `GET /me/appointments`, `GET /me/appointments/availability`.
