# Data Capture Verification Plan v1

**Date:** 2026-08-24
**Scope:** every route in `backend/app/modules/*` — confirm what each endpoint actually persists to DB, find dead columns, find spec'd-but-uncaptured fields.

## Why this plan exists

Backend has drifted fast (Appointments-spine schema rewrite 11 Aug 2026, `core.sessions` retirement, protocol_instances absorbing treatment_cycle — commit `6e2a0a7`). Docs/spec (`Documents/*.docx`) and DB (`SQL/v1/*.sql`, up to file 58) may now disagree with what code actually writes. No one has walked every route end-to-end since the spine rewrite. This plan is audit-only — **no code or schema changes** until you review findings and greenlight fixes, per [[feedback_dev_order]].

## Current surface (measured 2026-08-24)

| Module | Routes | Router file |
|---|---|---|
| scheduling | 38 | `app/modules/scheduling/router.py` |
| treatment_protocols | 30 | `app/modules/treatment_protocols/router.py` |
| device_sessions | 27 | `app/modules/device_sessions/router.py` |
| admin | 26 | `app/modules/admin/router.py` |
| staff | 20 | `app/modules/staff/router.py` |
| auth | 19 | `app/modules/auth/router.py` |
| reception | 18 | `app/modules/reception/router.py` |
| patients | 14 | `app/modules/patients/router.py` |
| prs | 12 | `app/modules/prs/router.py` |
| payments | 10 | `app/modules/payments/router.py` |
| files | 7 | `app/modules/files/router.py` |
| store | 7 | `app/modules/store/router.py` |
| anamnesis | 5 | `app/modules/anamnesis/router.py` |
| consent | 5 | `app/modules/consent/router.py` |
| clinical | 4 | `app/modules/clinical/router.py` |
| inventory | 4 | `app/modules/inventory/router.py` |
| notifications | 4 | `app/modules/notifications/router.py` |
| reports | 0 | **stub only, no router** |
| audit | 0 | **stub only, no router** |

~250 live routes across 17 wired modules. `reports`/`audit` still unbuilt (confirms 2026-08-19 gap memory, re-verify not stale before relying on it).

## Method (per route, repeat for every route in scope)

1. **Read router** → confirm which service method it calls, what request schema it accepts.
2. **Read service → repository** → confirm every field on the incoming schema is actually passed to an INSERT/UPDATE. Flag any accepted-but-dropped field (silently discarded = capture gap).
3. **Cross-check DB write against `SQL/v1/*.sql` (latest column state per table — later-numbered file wins on conflict) and `SQL/v1/erd.dbml`** → confirm target table/columns exist as expected, no write to a column that no longer exists (stale code) or a column left permanently NULL by every caller (dead column, candidate for drop).
4. **Cross-check against spec** (`Documents/Anava_Master_Database_Architecture_v3_0.docx`, `Anava_Protocol_Sessions_Payments_Plan_v2.docx`, `Anava_Application_Bible_v3.docx`) → any spec'd field with zero code path writing it = real capture gap, not just a doc/code drift nit.
5. **Record row** in the gap table: `endpoint | table.column | captured? Y/N/PARTIAL | evidence file:line | note`.

Do NOT trust old memory ([[project_anava_overview]], [[project_doctor_api_gap]]) as current fact — both flagged stale-on-read (48 and 4 days old respectively, and the appointments-spine rewrite happened after the older one). Use them only as a hypothesis list to re-check, not a source of truth.

## Execution order — mirrors your ask (login → training protocol), then branches out

**Phase 1 — Identity & onboarding spine** (blocks everything downstream, do first)
`auth` → `admin`/`staff` (profile creation) → `consent` → `anamnesis` → `prs` → doctor auto-allocation (`patients` service)

**Phase 2 — Clinical entry point named in your ask**
`patients` (registration_status machine) → `treatment_protocols` (this is your "training protocol route" — protocol_instance creation/approval, the module SQL file 45/47/58 rewrote most recently, highest drift risk)

**Phase 3 — Downstream execution**
`scheduling` (appointments spine, biggest route count, most recent schema churn) → `device_sessions` (TDCS sessions, new module since last memory snapshot) → `clinical`

**Phase 4 — Money & materials**
`payments` → `store` → `inventory`

**Phase 5 — Support**
`files` → `notifications` → `reception` → `reports`/`audit` (confirm still-stub, decide if in scope for this pass or a separate build task)

## Deliverables

1. **Gap report** — one table per module (5-row schema above), appended into a single `Documents/DATA_CAPTURE_GAP_REPORT.md`.
2. **Unused-column list** — per table, columns with zero write-paths found across all modules. Cross-referenced against `SQL/v1/erd.dbml` so nothing flagged is actually written via a trigger/default instead of app code (e.g. `protocol_scales_from_prs` trigger, `custom_montage_device_trigger`) before calling it dead.
3. **Cleanup proposal** — a draft `SQL/v1/59_*.sql` (not applied) listing proposed `ALTER TABLE ... DROP COLUMN` for confirmed-dead columns, held for your review per [[feedback_dev_order]] — schema changes get proposed then locked, never auto-applied.

## Explicitly out of scope this pass

- No DB writes, no migrations run, no code edits — report only.
- `reports`/`audit` module build-out — that's a separate build-order decision already parked in [[project_doctor_api_gap]]-style memory, flag but don't build.
- Frontend — verifying capture is a backend-only exercise; if a gap traces back to the frontend never sending a field, note it but don't touch `.tsx` per [[feedback_frontend_scope]].
