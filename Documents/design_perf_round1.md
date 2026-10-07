# Performance round 1 (2026-10-05)

Outcome of the first load-test round. Test kit, raw results and the tracker
workbook are in `D:\PCS\Documents\Perf_Test` (outside this repo).

No API contract, response shape or screen changes. Nothing a user sees changes
except under overload (a quick "server busy" answer instead of a long hang).

## What the tests showed

Local backend on a laptop, sandbox RDS 28 ms away, 302 real read requests from
six staff roles.

| Finding | Evidence |
|---|---|
| Every request made about 13 database round trips, even a trivial one | `/auth/me` took 366 ms with one user |
| Most of those were connection liveness checks | Each pooled checkout pinged the database, twice per request, several round trips each |
| The appointments list spent 62 of 72 ms in one security function | `core.fn_can_read_protocol()` ran once per row: 40 ms for 280 rows, 0.8 ms inline |
| Under overload requests hung for 30 s, then failed as a bare 500 | Pool wait was the 30 s default |
| With more than about 5 connections busy, the sandbox database stalls for everyone | A new connection needed up to 2 s to plan a 20 ms query, while a pure-CPU query stayed fast. Points to memory on the ~1 GB instance. Not fixable in code |

Individual queries are otherwise fine: measured by Postgres itself, the
database does about 7 ms of execution per request, plus planning on the first
few runs of each statement per connection.

## Changes

### 1. Liveness check only for idle connections (`app/core/db.py`, `app/config.py`)

`pool_pre_ping=True` is replaced by a checkout listener that pings a pooled
connection only when it has been idle for `db_ping_after_idle_seconds`
(default 5). A dead connection is discarded and the checkout retried, exactly
as before.

- Trade-off: a connection that dies and is reused within 5 s fails one request
  (500), then is replaced. Verified both cases against the live database.
- `DB_PING_AFTER_IDLE_SECONDS=0` restores a ping on every checkout.

### 2. Auth lookup without BEGIN/COMMIT (`app/core/middleware.py`)

`_load_profile_and_scope` runs `SELECT * FROM ops.auth_context(:sub)` in
autocommit mode. The single statement is its own transaction, so the consent
self-heal write still commits before any rejection, and the function's
`set_config(..., true)` calls still last for exactly that statement.

Changes 1 and 2 together: `/auth/me` 366 ms -> 131 ms on the laptop
(about 13 round trips -> about 5).

### 3. Overload protection (`app/core/db.py`, `app/core/middleware.py`, `app/main.py`, `app/config.py`)

- `pool_timeout` is now `db_pool_timeout_seconds` (default 10, was 30).
- A pool timeout returns `503` with `Retry-After: 2` and
  `{"error": {"code": "SERVER_BUSY", ...}}`, from both the auth middleware and
  the endpoint path, and logs a `db_pool_timeout` warning.

### 4. Inline protocol read check (`SQL/v1/108`, alembic `0060`) - NOT YET APPLIED

The SELECT policies on `protocol_conditions`, `protocol_diagnoses` and
`protocol_scales` get the function's own `EXISTS` written inline. Same rows
admitted; reachability is still delegated to `protocol_plan`'s policy.

Apply with `alembic upgrade head`. Check with
`Perf_Test/analysis/rls_visibility.py` (a "before" file is already saved).
The sandbox has one clinic, so every staff role sees every row before and
after: the check proves nothing is lost, not that nothing is over-shared. The
policy text itself is the proof of that: it is the function body, verbatim.

## Verification

- 278 unit tests pass; ruff and mypy clean.
- All 302 responses captured from the old code and the new code back to back:
  301 byte-identical. The other one differs only in list order, and the old
  code differs from itself the same way (see open items).
- Audit and activity logging untouched: the endpoint's transaction and its
  `set_config` call (which the audit trigger reads) are unchanged.

## Results (laptop, same hour, old code vs new code)

| Users | Old: req/s, median | New: req/s, median |
|---|---|---|
| 40 | 10.6, 611 ms | 12.4, 193 ms |
| 60 | 7.5, 3.8 s | 9.3, 2.1 s |
| 70 | 8.4, 4.3 s | 10.8, 2.7 s |

Responses are about 60 percent faster below the limit and throughput about
25 percent higher above it. The limit itself (about 10 requests per second
here) did not move, because it is set by the sandbox database stalling, and by
28 ms per round trip from a laptop. These numbers are not production capacity.

## Open items

- **Database size (needs AWS console):** check `FreeableMemory`, `SwapUsage`,
  `CPUCreditBalance` on the RDS instance during a run. Expected fix is a larger,
  non-burstable instance for production.
- **Unordered list:** `SELECT * FROM prs_scale_results WHERE instance_id = :id`
  (`prs/repository.py`) has no `ORDER BY`, so `scale_results` comes back in a
  different order from call to call. Pre-existing. Not changed here.
- **Not done on purpose:** caching the auth context (decided against: a
  deactivated user must be blocked on the next request) and forcing generic
  query plans (unproven benefit, real risk).
- **Not yet tested:** patient screens, writes under load, the deployed
  environment.
