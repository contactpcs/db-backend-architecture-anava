# Treatment Protocol wizard — endpoint contract

For the frontend developer building the tDCS Protocol screen.

Base URL: `/api/v1` · Auth: `Authorization: Bearer <token>` on every call.

Two things to know before wiring anything:

1. **`clinic_id` is required on Step 1.** Without it the picker offers devices the clinic does not own. The database independently refuses such a protocol, so the user would fill in all 8 steps and fail at the end.
2. **Steps 1–6 are reads.** Nothing is written until Step 8. Step 7 is a pure preview — it writes nothing and can be re-run freely.

---

## Step 1 · Device

```http
GET /neuromod/devices?clinic_id={clinicId}&phase=1&active_only=true
```

**Always send `clinic_id`.** Returns only devices that clinic owns.

```jsonc
[{
  "device_id": "…", "device_code": "SOTERIX-1X1", "device_name": "Soterix 1x1 tDCS",
  "model_number": "1x1", "modality": "tDCS",
  "phase": 1,                  // 1 = selectable, 2 = grey out (not yet configurable)
  "is_active": true,
  "company_name": "Soterix Medical Inc.", "company_code": "Soterix",
  "clinic_quantity": 3         // how many units this clinic owns
}]
```

- `phase: 2` → render the card greyed out, matching "other devices can be added but are not configurable yet".
- `clinic_quantity` is **units owned**, not concurrent capacity. Do not use it for scheduling.
- Empty array = the clinic has no devices recorded. Show "No devices configured for this clinic — contact your clinic admin", not an empty grid.

Supporting call for company grouping:
```http
GET /neuromod/device-companies?active_only=true
```

> ⚠ The prototype allows **up to 3 devices**. The backend currently stores **one** device per protocol. See "Known gaps" — confirm with backend before building multi-select.

---

## Step 2 · Condition

```http
GET /neuromod/conditions?device_id={deviceId}
```

Passing `device_id` scopes the evidence chips to that device's dosing rows.

```jsonc
[{ "condition_id": "…", "condition_name": "Depression", "display_order": 1 }]
```

> ⚠ Multi-select and the "Other condition (free text)" box have nowhere to be stored yet.

---

## Step 3 · Diagnosis codes (ICD-10)

```http
GET  /neuromod/diagnoses?condition_id={id}&condition_id={id2}
POST /neuromod/diagnoses/resolve
     { "diagnosis_ids": ["…"], "device_id": "…" }
```

`resolve` returns the ranked-by-evidence suggestion that fills the "Resolution" panel and the Suggested / Evidence columns.

---

## Step 4 · Electrode placement

```http
GET  /neuromod/placements?device_id={id}&condition_id={id}
POST /neuromod/placements/validate
     { … montage … }
```

`validate` enforces the electrode rule shown in the UI: tDCS = exactly 1 anode + 1 cathode; HD-tDCS = 1 anode + up to 4 returns.

> ⚠ **Custom montages have no endpoint and no table.** The predefined library is superadmin-owned and read-only to the application by design. Saving a doctor-created montage needs new backend work — do not build that panel yet.

---

## Step 5 · Stimulation parameters

```http
GET /neuromod/dosing?device_id={id}&condition_id={id}&placement_id={id}
```

Returns the suggested dose that seeds mA / duration / ramp, and drives "Reset to suggested".

> The contraindication warning in the prototype is static text. There is no contraindication endpoint.

---

## Step 6 · Assessment scales

```http
GET /neuromod/scales?condition_ids={id}&condition_ids={id2}
```

> ⚠ Returns the **suggested** scales only. Per-scale **cadence / window** cannot be saved — no table exists. Render the table, but treat the cadence controls as not-yet-wired.

---

## Step 7 · Session schedule

### Preview — writes nothing, re-runnable

```http
POST /treatment-protocols/schedule-preview
{
  "start_date": "2026-08-20",
  "session_count": 20,
  "sessions_per_week": 5,
  "follow_up_every_n": 5,
  "skip_dates":  ["2026-08-25"],   // removedDays
  "extra_dates": ["2026-08-29"]    // extraDays
}
```

```jsonc
{
  "sessions":   [{ "session_number": 1, "planned_date": "2026-08-20" }],
  "follow_ups": [{ "after_session_number": 5, "planned_date": "2026-08-27" }],
  "session_count": 20, "follow_up_count": 4,
  "first_date": "…", "last_date": "…", "week_count": 5
}
```

Call this on every calendar edit — start date, regenerate, day painted in or out, checkpoint interval changed. It is pure date arithmetic and touches no tables.

**An extra date is a moved session, not an added one.** Painting a day in moves one of the requested sessions onto it; the total never exceeds `session_count`.

### Availability panel

```http
GET /clinics/{clinicId}/device-schedules
      → [{ day_of_week, start_time, end_time, capacity, … }]   0=Sun..6=Sat

GET /clinics/{clinicId}/device-schedule-overrides?from_date=2026-08-01
      → [{ override_date, is_available, capacity, reason }]    is_available:false = CLOSED

GET /clinics/{clinicId}/device-availability?from_date=…&to_date=…
      → [{ date, start_time, end_time, capacity, booked, remaining, is_available }]
```

Maps onto the prototype's four rows:

| Row | Source |
|---|---|
| **Clinic** — "Mon–Fri, 09:00–18:00" | `device-schedules` |
| **Device** — "4 slots/day" | `device-availability` → `capacity` |
| **Clinic closed** days on the calendar | `device-schedule-overrides` where `is_available: false` |
| **Assistant**, **Patient** | ❌ not modelled — leave static or omit |

---

## Step 8 · Review and push

```http
POST /treatment-protocols
{
  "plan_id": "…", "device_id": "…",
  "tdcs_placement_id": "…", "tdcs_dosing_id": "…",
  "session_count": 20, "follow_up_every_n": 5,
  "device_settings": { "current_ma": 2.0, "duration_min": 30 },
  "notes": "…"
}
→ 201 ProtocolRead   (status: "draft")

POST /treatment-protocols/{protocol_id}/activate
→ generates every appointment on the spine and makes it live
```

**Two calls, and the split matters.** `POST` creates a draft; `activate` is what writes the sessions into Appointments. The prototype's "Treatment Protocol Pushed" modal belongs after `activate`, not after `POST`.

Exactly one placement id and one dosing id — the pair must match the chosen device.

### Errors worth handling

| Code | HTTP | Meaning |
|---|---|---|
| `DEVICE_NOT_AVAILABLE_AT_CLINIC` | 400/409 | Device is not in the clinic's inventory. Caused by a stale tab — refresh Step 1. |
| `PROTOCOL_NOT_ACTIVE` / `PROTOCOL_NOT_CANCELLABLE` | 400 | Lifecycle violation |
| `DEVICE_NOT_FOUND` | 404 | Unknown device id |

### Lifecycle

```http
GET   /treatment-protocols?patient_id=…
GET   /treatment-protocols/{id}            full detail
GET   /treatment-protocols/{id}/sessions   generated appointments
PATCH /treatment-protocols/{id}            DRAFT ONLY
POST  /treatment-protocols/{id}/cancel     { "reason": "…" }
POST  /treatment-protocols/{id}/complete
```

`PATCH` works only while `status = 'draft'`. Once activated, appointments exist and the patient may have booked against them — edit is closed.

---

## After the protocol: what the patient sees

Sessions land as `planned` appointments with a **date but no time**. The patient picks the hour:

```http
GET   /me/appointments                                       all types, planned included
GET   /me/appointments/{id}/device-availability?from_date=…  capacity-based slots
PATCH /me/appointments/{id}/claim-slot   { "start_time": "10:00" }
```

The read-only "Session Schedule" panel in the published view maps to `GET /treatment-protocols/{id}/sessions`.

**Clinical assistant view** is read-only, matching "Read-only · Clinical Assistant". The CA's one write is starting a session:
```http
PATCH /appointments/{id}/status   { "status": "in_progress" }
```
Only a `clinical_assistant` may set this on a `device_session` — it captures which assistant ran it.

---

## Known gaps — do not build these panels yet

| Flow feature | Status |
|---|---|
| Multi-device select (up to 3) | Backend stores **one** device per protocol |
| Multi-condition + "Other" free text | Not stored on the protocol |
| ICD-10 codes persisted on the protocol | Only used to resolve suggestions; not saved |
| **Custom montage create/save/list** | **No table, no endpoint** |
| Per-scale cadence / window | Not stored |
| Protocol note to care team | No column |
| Save as Template | No table |
| Assistant / patient availability | Not modelled |

Everything else in the eight steps is wired and callable today.

---

## Build order

1. **Steps 1–3** — plain reads, and Step 1 proves the clinic filter works
2. **Steps 4–5** — placement and dosing, minus the custom-montage panel
3. **Step 7** — schedule preview plus the availability panel. Highest value: pure reads, no writes, safely re-runnable.
4. **Step 8** — `POST` then `activate`, and confirm the modal fires on the second call
5. **Patient claim-slot flow** — the other half of what Step 7 generates

Full typed schemas: run the API and open `/docs`.
