# TODOS

## Eng Review (2026-07-20)

### Confirm whether the Appointment/scheduling flow is live, and if so wire payments to it

**What:** Determine whether `scheduling/AppointmentService` (the post-Flow-Pivot booking model) is reachable by real traffic anywhere right now, and if it is, connect its lifecycle to `payments`' billing gate.

**Why:** Verified via grep across all of `app/modules/scheduling/`: nothing there references `treatment_sessions` or `payment_status`. `payments/service.py:81-86` is the only billing gate in the codebase and it writes into the OLD `treatment_sessions.payment_status` column — a table the new Appointment flow never creates a row in. If the Appointment flow is live in any environment that matters, this means paying for an appointment currently gates nothing, and an unpaid appointment is never blocked from proceeding. This is a revenue-control bypass, not an architecture nit — it was escalated during the eng review specifically because "may be disconnected" turned out to be "verified disconnected."

**Context:** This is the same fork identified as the July 2026 Flow Pivot (memory: `project_flow_pivot_jul2026`) — a fixed Session1-4 model (`clinical/` — `sessions`, `treatment_cycles`, `treatment_sessions` tables) coexists with a new Appointment/Device-Session model (`scheduling/` — `appointments` table). `clinical/` still enforces its old "Session 1 blocked until protocol approved" business rule (`clinical/service.py:174-183`) and is fully routed in `main.py`. Per project memory, the Flow Pivot docs are pending doctor sign-off and conflict with what's already built in stages 0-12 — so the Appointment flow may not be customer-facing yet, which would make this lower urgency than it looks from code alone. That status needs to be confirmed by whoever owns deployment/frontend integration before deciding whether this is a hotfix or can wait for the doctor sign-off to land. If confirmed live: the fix is either (a) have `AppointmentService.create`/status-transition write a `treatment_sessions` row payments can gate against, or (b) build a new billing gate keyed off `appointments` directly and retire the `treatment_sessions` one.

**Effort:** M (investigation) + M-L (fix, depends on which direction)
**Priority:** P0 — pending confirmation of live status; treat as P0 if scheduling is reachable by real traffic, otherwise P1 blocked on Flow Pivot sign-off
**Depends on:** Flow Pivot doctor sign-off (per project memory)

---

### Close the inventory clinic-scope leak

**What:** `inventory/router.py:15-17` `list_inventory` has no `assert_clinic_scope` call and doesn't force-fill `clinic_id` for `clinic_admin`/`receptionist` the way `patients/router.py` does.

**Why:** `InventoryRepository.list(clinic_id=None, ...)` returns every clinic's inventory when `clinic_id` is omitted, and a `clinic_admin` can pass another clinic's `clinic_id` explicitly and read its stock levels. Lower sensitivity than the patient-data leaks fixed this session (stock counts, not clinical/financial data) but same missing-scope-check shape, and could expose commercially sensitive device inventory to a competing clinic's admin.

**Context:** Fix is mechanical — copy the `elif clinic_id is None and ctx.role in (...): clinic_id = ctx.clinic_id` pattern from `patients/router.py`, plus an `assert_clinic_scope` call. Same shape as the clinical/payments/store fixes already applied this session (see `app/core/scoping.py`).

**Effort:** S
**Priority:** P1
**Depends on:** None

---

### Empty audit/ and reports/ module placeholders — build the read side

**What:** `app/modules/audit/` and `app/modules/reports/` are 0-byte placeholder packages with no router/service/schemas/repository, and aren't wired into `main.py`.

**Why:** The system already writes audit-shaped data — `app/core/events.py` emits an outbox event on nearly every create/update across every module, and `scheduling` has its own `AppointmentAuditLogRepository` — but there's no endpoint for staff to query it, and no reporting/analytics surface at all. For a clinic system handling patient clinical/financial data, a future compliance or dispute question ("who changed this record and when") currently has no API to answer it even though the underlying event data already exists in the DB.

**Context:** Since the event/outbox data already exists, building the read side is mostly a query layer over `events`/`AppointmentAuditLogRepository`, not new instrumentation.

**Effort:** M
**Priority:** P2
**Depends on:** None

---

### admin <-> staff bidirectional top-level import coupling

**What:** `admin/service.py:17` top-level-imports `staff.repository`, and `staff/service.py:11` top-level-imports `admin.repository` — the one pair of modules in the codebase that cross-import at load time both ways, instead of the deferred (in-function) imports used everywhere else specifically to dodge circularity.

**Why:** Works today but is fragile — a third top-level cross-import completing an actual cycle would produce a real `ImportError`. Signals these two modules aren't as separable as the directory structure suggests.

**Context:** Fix direction: either merge admin+staff's shared bits into one module, or extract the shared piece (staff-assignment CRUD) into its own small module both can import from.

**Effort:** M
**Priority:** P3
**Depends on:** None

---

### scheduling/router.py: use assert_owns_profile instead of hand-rolled inline checks

**What:** `scheduling/router.py:114-115,155-158,187-188` hand-roll `if ctx.role == "patient" and str(x["patient_id"]) != ctx.user_id: raise PermissionError_(...)` three times instead of calling the shared `assert_owns_profile` helper every other module uses.

**Why:** Functionally equivalent, purely a DRY/consistency gap — but call sites that reimplement shared logic can silently drift if `scoping.py`'s rules ever change. Also worth confirming during the fix whether `get_appointment_request` intentionally skips the doctor-ownership check that `get_appointment` has (may be intentional — doctors see all clinics' pending requests — or may be an oversight).

**Context:** Mechanical swap once the doctor-visibility question is confirmed.

**Effort:** S
**Priority:** P3
**Depends on:** None

---

### Split scheduling/service.py's AppointmentService god-class

**What:** `scheduling/service.py` is 596 lines, the only file over 500 in the codebase. `AppointmentService` alone handles 11 responsibilities (create/get/list/list_upcoming/list_today/audit-write/transition-authorization/update_status/update_fields/reschedule/audit_log).

**Why:** Not broken, but the `_authorize_transition` state-machine logic (role-aware, checks doctor-only statuses, checks allowed-from map) is intricate enough that a future bug fix risks touching unrelated methods by accident.

**Context:** Deliberately deferred past the Flow Pivot / dual-model TODO above — this file is likely to change shape again once the Session/Appointment model question resolves, so splitting now risks doing it twice.

**Effort:** M
**Priority:** P3
**Depends on:** Dual-model TODO above (do that first)

---

### Stand up real test coverage for the backend

**What:** Build out `tests/unit`/`tests/integration` for the ~120 files not touched by this review's smoke tests. pytest/pytest-asyncio/pytest-cov/testcontainers[postgres] are already configured in `pyproject.toml` — infra is ready, nothing beyond this session's 37 tests (`tests/unit/test_scoping.py`, `test_fsm.py`, `test_config.py`, `test_resolve.py`, `test_clinical_router_scoping.py`, `test_payments_store_router_scoping.py`) exists yet.

**Why:** Per the "well-tested code is non-negotiable" preference — 0% coverage on 129 files means every module's ownership checks, business rules, and status-transition guards (including the ones fixed this session) have no regression backstop going forward except the narrow slice just added.

**Context:** Prioritize security-relevant modules first (patients, consent, files, prs, anamnesis — the ones that already do ownership checks correctly, to lock in they stay correct) and the modules touched today. `testcontainers[postgres]` is the right tool for real integration tests against the actual schema/RLS setup rather than mocking the DB layer throughout.

**Effort:** XL
**Priority:** P2
**Depends on:** None

---

### Batch the 5 N+1 query loops

**What:** `store/service.py:59-67` (one product lookup per line item on order create), `clinical/service.py:152-156` (one insert per scale on protocol approval), `prs/service.py:100-104` (one insert per scale on assignment), `prs/service.py:250-253` (two queries per submitted answer on response submission), `prs/service.py:300-303` (one query per question on scoring) each loop-and-query instead of batching.

**Why:** Fine at current volumes (single-digit line items/scales/questions per request) but scales linearly with clinic growth; PRS scoring runs on every assessment submission, the highest-volume of the five.

**Context:** None require new infra — straightforward `WHERE id = ANY(...)` / batched-insert rewrites of each loop.

**Effort:** M
**Priority:** P2
**Depends on:** None

---

### Add a shared pagination helper and apply it across all list() endpoints

**What:** Only `scheduling/repository.py`'s `AppointmentRepository.list` has `skip`/`limit`. Every other `list()` across admin, anamnesis, clinical, consent, inventory, notifications (hardcoded `LIMIT 100`, no offset), patients, payments, prs, staff, store returns the full unbounded result set.

**Why:** Fine today with small per-clinic patient counts; becomes a real problem as clinics accumulate years of patients/appointments/consent records — an unbounded `list patients` call eventually pulls thousands of rows into memory and over the wire in one response.

**Context:** Solve once, systematically — build a shared pagination helper (mirroring the `sql_helpers.py` pattern) rather than hand-rolling `skip`/`limit` in each of the 11+ repositories again.

**Effort:** L
**Priority:** P2
**Depends on:** None
