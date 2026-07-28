# Anava Clinic Backend — Brutal Architecture Review v1.0
Principal Architecture Approval Review — Pre-Production Gate

Reviewer stance: final approval board before deploying mission-critical healthcare software. No score inflation, no credit for effort, no assumption of missing information.

---

## 0. A Framing Correction I Will Not Paper Over

The brief asks me to review this "assuming one month from production." Read literally against what actually exists on disk, that assumption is false, and pretending otherwise would make this review dishonest rather than rigorous.

**Actual state as of this review:** `backend/` contains a working foundation (auth, DB/RLS context wiring, exception handling, an outbox table) and exactly **one** of seventeen planned modules (`auth`) has any code beyond `__init__.py`. The other sixteen — `admin`, `staff`, `patients`, `clinical`, `scheduling`, `prs`, `anamnesis`, `files`, `consent`, `store`, `inventory`, `payments`, `notifications`, `audit`, `reports`, plus `app/integrations/` and `app/workers/` — are **empty directories**. Zero tests exist (`tests/unit`, `tests/integration` are empty). Zero business endpoints exist beyond a dev-only login stub and a debug whoami route.

This review therefore covers two genuinely different things, scored separately so neither inflates the other:
1. **The architecture as documented** (`Anava_Backend_Architecture_v1.md`, the SQL schema, the API catalog, the dev plan) — judged on design quality, as if faithfully implemented.
2. **The architecture as actually built today** — judged on what exists, including real bugs found by reading the code, not assumed.

Conflating these two would either unfairly punish good documentation for being early-stage, or unfairly credit incomplete code for having a good spec. Both are tracked throughout.

---

## 1. Executive Summary

The **documented design** is genuinely strong — better than what most teams have one month before launch, let alone one month into planning. The schema shows real production discipline (soft deletes, `FORCE ROW LEVEL SECURITY`, deferred FK handling for legitimate circular references, content-hash-pinned consent, `SECURITY DEFINER` audit triggers immune to profile deletion). The 25-section architecture document makes defensible, justified tradeoffs rather than defaulting to fashionable complexity (correctly rejects microservices, Kafka, Redlock, vector search — for now, with stated reasons, not by omission).

The **actual codebase** is a thin, correctly-built vertical slice — and it is not close to production regardless of calendar time, because 16 of 17 feature modules don't exist yet. That is expected at this stage and is not itself a criticism of engineering quality. What *is* a legitimate finding: reading the ~550 lines of foundation code that do exist surfaced **one confirmed blocking-I/O bug that will stall the entire server process under load**, and reading the RLS policy file surfaced **three tables where the database's write-path security policies check role only, not tenant scope** — meaning the "defense in depth" the architecture document promises is not actually in place for those tables today. Neither of these was caught until this review actually read the files line by line instead of trusting the design intent.

**Verdict up front:** Approve the architecture direction. Do not approve for production — not because one month is too short in the abstract, but because sixteen modules, all tests, all integrations, and the two concrete bugs below don't exist/aren't fixed yet. Detail follows.

---

## 2. Architecture Scorecard (summary — full detail in numbered sections below)

| Area | Score /10 | Basis |
|---|---|---|
| Overall Architecture (as documented) | 8/10 | Strong module boundaries, correct layering decisions, honest ADRs |
| System Design (as documented) | 7.5/10 | Good event/queue design; outbox lifecycle management incomplete |
| Backend Architecture (as documented) | 7.5/10 | Clean layered approach, correctly avoids over-engineering; DI is minimal-but-real, not decorative |
| API Design | N/A → 2/10 if forced | Only 3 endpoints exist; cannot honestly score a design against zero real usage |
| Database Integration | 6/10 | Foundation is solid; two real connection-pool inefficiencies found |
| Authentication & Authorization | 5/10 | Auth flow works end-to-end (verified); one blocking bug, one weak-scope gap, RLS write-policy gaps found |
| Module Design | 2/10 as-built, 7.5/10 as-spec | 1 of 17 modules exists |
| Scalability (as documented) | 7/10 | Good instincts (BRIN, partitioning plan, cursor pagination); unproven, zero load ever run |
| Reliability | 3/10 | No retries, no circuit breakers, no timeouts implemented anywhere yet |
| Performance | 3/10 (unproven) | Two concrete inefficiencies found in the only code that exists |
| Security | 4/10 | Strong schema-level intent undermined by found RLS write-policy gaps and a dev secret in a tracked file |
| Cloud Readiness | 2/10 | Local-only by design at this stage; nothing to score for AWS yet |
| Codebase Maintainability | 7/10 | What exists is clean and well-commented; too little exists to fully judge |
| Future Readiness | 6/10 (design), 0/10 (built) | Schema has room; nothing built yet touches these axes |
| **Production Readiness (overall, today)** | **2/10** | Cannot serve a single real clinical workflow yet |

---

## 3. Strengths

1. **Schema discipline is genuinely above-average.** `FORCE ROW LEVEL SECURITY` on every table (closes the table-owner-bypasses-RLS hole most teams miss), soft-delete-only on every PHI table (`ON DELETE RESTRICT` consistently, no hard deletes found anywhere), deferred FKs used correctly for real circular references (`appointment_requests`↔`appointments`, `prs_assessment_instances`↔`prs_final_results`), generated columns instead of app-computed values (`extended_sessions`, `percentage` fields).
2. **The audit trigger is well-built.** `SECURITY DEFINER`, `TEXT` record_id (correctly handles mixed UUID/TEXT PKs after a real bug was caught and fixed mid-design), reads actor from session context rather than requiring app code to pass it explicitly.
3. **Honest, justified ADRs, not cargo-culted ones.** The architecture doc explains *why* microservices/Kafka/Redlock/vector-search are rejected *for now*, with a stated re-evaluation trigger, not just "we don't need it." That's the difference between an engineering decision and a shortcut.
4. **What's built was actually run, not just written.** Unlike most "architecture review" targets, the auth chain, RLS context wiring, and migration were executed against a real local Postgres and verified to produce correct 401/403/200 responses. That's real evidence, not a claim.
5. **Correct build-sequencing insight.** Recognizing that Consent/Anamnesis/PRS must be built before Patients (because registration steps 3-5 depend on them) rather than following the Master Document's product-flow letter order is a genuine dependency-graph correction, not busywork.

---

## 4. Weaknesses

Ranked by production impact, detailed with Problem / Risk / Impact / Recommendation / Priority in Section 15.

1. Sixteen of seventeen modules are empty — the system cannot do anything clinical yet.
2. **Blocking synchronous HTTP call inside the async request path** (`app/core/security.py::_fetch_cognito_jwks`, uses `httpx.get` not `httpx.AsyncClient`) — dormant today because `auth_mode=local` never calls it, will stall the entire uvicorn worker on every JWKS cache miss once Cognito is wired in.
3. **RLS write-policies on `clinic_staff_assignments`, `treatment_plans`, `treatment_sessions` check role only, not tenant scope** (`15_rls_policies.sql` lines 210-218, 302-306, 322-326) — inconsistent with the equivalent policies on `treatment_cycles`/`sessions` two sections earlier in the same file, which correctly check `clinic_id = rls_clinic_id()`. A bug in application-layer permission checks for these three tables currently has no DB-level backstop.
4. **`require_clinic_scope` doesn't actually check the resource's clinic** (`app/core/permissions.py`) — by its own comment, it only verifies the caller *has* a clinic scope, deferring the real comparison to "the service layer," which doesn't exist yet for any module. As written today, this dependency provides much less protection than its name implies.
5. **Every authenticated request opens two separate pooled DB connections** — one in `AuthContextMiddleware._load_profile_and_scope` (raw `engine.connect()`), one later in the actual endpoint's `get_db()` dependency. No caching of the identity/scope lookup exists despite Redis already being provisioned for exactly this kind of thing.
6. **Zero tests exist.** CI workflow (`backend-ci.yml`) references `pytest --cov=app` — today that command finds nothing to run and would report 0% coverage as a "pass."
7. **`outbox_events` has no consumer and no lifecycle plan.** The relay process described in the architecture doc (`app/workers/event_relay.py`) doesn't exist. Rows would accumulate forever once any module starts calling `emit_event()` — no partitioning or archival policy was defined for this table the way it correctly was for `audit_logs`/`activity_logs`.
8. **No retry/timeout/circuit-breaker code exists anywhere**, despite being a named architecture requirement (Section 10 of the architecture doc). `app/integrations/` is empty — no Cognito, S3, Razorpay, SES, or SNS client exists yet, so there is nothing to actually apply retry logic to, but the gap is real for anyone assuming it's handled.
9. **`/health/ready` only checks Postgres**, not Redis or the queue — a cache/queue outage wouldn't be caught by the readiness probe once deployed, so orchestration would keep routing traffic to an instance that can't actually complete requests needing those dependencies.
10. **Dev JWT secret is committed in `.env.example`** (`dev-only-insecure-secret-do-not-use-in-prod`) — correctly labeled and low actual risk since it's dev-only, but nothing in code *enforces* this can't leak into a prod config (no startup assertion, no secret-scanning CI step).

---

## 5. File-by-File Review

| File | Verdict |
|---|---|
| `app/core/db.py` | Correct pattern (transaction-scoped `SET LOCAL`, not session-scoped `SET` — the one detail that actually matters for connection-pool safety). No issues found. |
| `app/core/exceptions.py` | Clean, matches the architecture doc's Section 17 hierarchy exactly. No issues found. |
| `app/core/security.py` | **Contains the blocking-I/O bug (#2 above).** Otherwise correctly designed (local/Cognito mode parity, same claim shape either way). |
| `app/core/middleware.py` | Correct request-ID/logging middleware. Auth middleware works but has the double-connection inefficiency (#5) and duplicates scope-resolution business logic that the architecture's own Section 5 says belongs in a service layer, not middleware — an acknowledged, labeled shortcut in its own docstring, but still a present violation. |
| `app/core/permissions.py` | `require_role` is correct and complete. `require_clinic_scope` is the weak link (#4) — reads as protective, isn't yet. |
| `app/core/events.py` | Correct outbox-write implementation (proper JSONB casting, right table). Consumer side doesn't exist (#7). |
| `app/main.py` | Correctly wires middleware order (auth before permission checks), global exception handler present. Fine for its current scope. |
| `app/config.py` | Reasonable Pydantic Settings; no environment-specific validation (e.g., refusing to boot with `local_jwt_secret` unchanged when `environment=production`) — a cheap, missing guardrail. |
| `alembic/versions/0001_baseline_schema.py` | Functionally correct (verified against a real DB) but structurally risky long-term: a single monolithic revision applying 20 raw SQL files via a hand-rolled statement splitter, with a `DROP SCHEMA CASCADE` downgrade and no safety guard against running it by accident later once real data exists. |
| `SQL/15_rls_policies.sql` | Strong on SELECT policies throughout. **Inconsistent on INSERT/UPDATE policies** — three tables (#3 above) got role-only checks while sibling tables in the same file got correctly tenant-scoped checks. This reads like an authoring oversight, not a deliberate choice — no comment explains the difference. |
| `docker-compose.yml`, `Dockerfile`, CI workflow | Correct for their stated scope (local dev, CI skeleton). Nothing to fault; also nothing to score highly for since neither has been exercised against a real deploy target. |
| 16 empty module directories | Nothing to review. Flagged as the primary production blocker, not a code-quality finding. |

---

## 6. Architecture Review (Section 1 of the requested review)

Separation of concerns, layering, and module boundaries are well-defined *on paper* — `router → service → repository → model`, one folder per bounded context, cross-module calls only through service functions. This is the correct choice for a team of one human plus AI pairing (Clean Architecture's extra entity-mapping ring would be pure ceremony here). Dependency direction is currently trivially correct because there's almost nothing to violate it yet — the one real violation found (middleware doing business-logic scope resolution) is small, contained, and explicitly labeled as temporary in its own comment, which is the honest way to take a shortcut, not the dishonest way.

**Score: 8/10 as documented.** Not higher, because the design has never been tested against a second module — one working vertical slice proves the pattern is *usable*, not that it *scales to sixteen more modules* without drift. Not lower, because what exists respects its own rules and the one violation is self-aware.

## 7. System Design Review

Request lifecycle is sound and matches documentation exactly (verified, not assumed — the 401/403/200 test sequence proved middleware ordering is correct). Event-driven design (outbox + relay + SQS) is architecturally correct but **half-built**: the write side works, the read/publish side doesn't exist. A system that can record "this happened" but never tell anyone is not yet an event-driven system, it's a very small audit log with delusions.

**Score: 7.5/10 as documented, 3/10 as built** (write path only, no relay, no consumers, no queue integration code at all despite ElasticMQ running).

## 8. Backend Review (Clean/Hexagonal/DI/Repository/Service/DTO)

Correctly pragmatic: DI happens through FastAPI's `Depends()` for real seams (DB session, auth context, permission checks) rather than a heavyweight IoC container nobody on a two-entity team needs. No repository layer exists yet to review — `_load_profile_and_scope` uses raw SQL directly rather than a repository, which is defensible as a deliberate, labeled exception (auth predates the modules whose tables it queries) but should not become the house style once `admin`/`staff` modules exist with real repositories for those same tables.

**Score: 7.5/10 as documented (design intent is right), unratable on repository/DTO/service quality since none exist yet.**

## 9. API Design Review

Cannot be honestly scored. Three endpoints exist: `/health`, `/health/ready`, `/auth/local-login`, plus one debug endpoint. None of REST conventions, versioning, pagination, filtering, error-response consistency, or rate limiting can be evaluated against real usage — the API catalog document describes 148 endpoints that don't exist in code yet. **Grading the design document alone: 7/10** (the consolidation work removing 70 redundant endpoints was genuinely good judgment). **Grading current API surface: 2/10** — three endpoints is not an API.

## 10. Database Integration Review

Transaction boundary is correct (one transaction per request, RLS context applied inside it). Two real problems found: the double-connection-per-request issue (#5) and the complete absence of any repository layer to evaluate for N+1 risk, since no queries beyond raw auth-lookup SQL exist yet. Connection pooling is configured (`pool_size=10`, `max_overflow=5`) but never load-tested — those numbers are guesses, not measurements.

**Score: 6/10.** Foundation is sound; nothing has been proven under any real query load.

## 11. Authentication & Authorization Review

This is where the review earns its "brutal" framing. The auth flow **works** — verified end-to-end, three times, with different roles. That's real and should be credited. But reading the code line-by-line surfaced two findings that "it works in the demo" completely missed:

- The Cognito JWKS fetch is synchronous I/O inside an async handler — this is not a style nitpick, it's a **production incident waiting to happen**: under concurrent load, one slow/failed JWKS fetch blocks the *entire* event loop, not just that one request, meaning every other in-flight request on that worker stalls too. This is invisible today because local dev never exercises the Cognito code path.
- `require_clinic_scope` doesn't scope anything yet — it's a placeholder with a protective-sounding name, and the RLS write-policies that were supposed to catch what it misses (#3) don't consistently catch it either, on three specific tables.

**Score: 5/10.** Would be 8/10 if either the blocking-I/O bug were fixed or the RLS gaps were closed; both being present at once, on the security-critical layer of a healthcare system, caps it here.

## 12. Module Design Review

Sixteen of seventeen modules score N/A (don't exist). `auth`: cohesive, single-responsibility, correctly minimal for what it needs to do today. No circular dependencies possible yet since there's only one module to depend on anything.

**Score: 2/10 as-built** (reflects that a "module design" review needs modules to review), **7.5/10 as-specified** (the Section 6 module responsibility table in the architecture doc is well-reasoned).

## 13. Scalability Review (50M patients / 5000 clinics / 100K concurrent / hundreds of millions of audit rows)

The instincts are right: cursor pagination mandated everywhere, BRIN indexes planned for the two biggest tables, partitioning planned before data accumulates rather than retrofitted, RDS Proxy planned once connection counts justify it, read replicas deliberately deferred rather than premature. All correct calls **on paper**.

None of it has been tested against anything resembling this scale, or any scale at all — there is no load test, no data seeded past a handful of dev rows, and the double-connection auth inefficiency (#5) alone would matter enormously at 100K concurrent users (double the connection pool pressure of every other endpoint, for zero functional benefit) yet was never caught until this review, because nothing has been run under load.

**Score: 7/10 as documented, unratable as built** (no system exists yet at any scale to measure).

## 14. Reliability, Performance, Security, Cloud — Condensed

- **Reliability: 3/10.** No retries, no circuit breakers, no timeouts anywhere in code (nothing to apply them to yet, but the gap is real for `app/integrations/`, which will need every one of these on day one and currently has zero client code). Health checks are shallow (`/health/ready` doesn't check Redis/queue).
- **Performance: 3/10 (unproven).** Two concrete inefficiencies found (double DB connection per request, synchronous blocking I/O in the JWKS path) in the *only* code that exists — a bad sign for what similar review of sixteen more modules might find if the same rigor isn't applied to each.
- **Security: 4/10.** Strong schema intent (RLS, `FORCE ROW LEVEL SECURITY`, soft deletes, audit triggers) undermined by the found RLS write-policy gaps on three tables and the incomplete `require_clinic_scope`. No secrets-manager integration exists yet (correctly deferred to Stage 13, but that means today's security posture, if this went to production today, would be materially weaker than the architecture doc implies).
- **Cloud Readiness: 2/10.** Entirely local by design at this stage (correct sequencing choice per the dev plan) — but that means, read literally, zero cloud readiness exists today. Not a criticism of the plan; a factual statement about the current state the "one month from production" framing must reckon with.

## 15. Technical Debt — P0/P1/P2/P3

| # | Finding | Problem | Risk | Production Impact | Recommendation | Priority |
|---|---|---|---|---|---|---|
| 1 | Blocking `httpx.get` in Cognito JWKS fetch | Sync I/O inside async request path | Stalls entire worker process on every cache-miss fetch | Under concurrent load at Stage 13 cutover, one slow JWKS fetch degrades every in-flight request on that worker | Replace with `httpx.AsyncClient`, await it properly | **P0** |
| 2 | RLS write-policy gaps on `clinic_staff_assignments`/`treatment_plans`/`treatment_sessions` | INSERT/UPDATE policies check role only, not tenant scope | Cross-clinic write if application-layer check has a bug | A Doctor or CA at Clinic A could, via an app bug, write records at Clinic B with the DB providing no backstop | Add `clinic_id = rls_clinic_id()` (or the equivalent doctor/CA-ownership check) to all three, matching the pattern already correct on `treatment_cycles`/`sessions` | **P0** |
| 3 | `require_clinic_scope` doesn't compare resource clinic | Placeholder logic, name overstates what it does | False sense of security for any endpoint that adds this dependency expecting real enforcement | Every future module author will assume this actually protects tenant boundaries | Either complete it (accept a resource-fetcher callback) or rename it to make the partial nature obvious until completed | **P0** |
| 4 | Zero tests | No unit/integration tests exist | Every future change is unverified by anything but manual curl | CI's `pytest` step is currently a no-op that reports success | Add a minimal but real test for the one thing that exists (auth chain) before building module #2, so the pattern is established early | **P0** |
| 5 | 16 empty modules | No clinical functionality exists | N/A — expected at this stage | Cannot serve any real workflow | Proceed per Development Plan Stage 4 onward | **P0** (blocking, but expected/tracked, not a surprise) |
| 6 | Double DB connection per authenticated request | No caching of identity/scope lookup | Doubles connection pool pressure for zero benefit | Meaningful at 100K concurrent user target | Cache resolved `RequestContext` in Redis keyed by token hash, short TTL (30-60s) | **P1** |
| 7 | `outbox_events` has no consumer or lifecycle plan | Relay/worker doesn't exist; no archival policy | Table grows unboundedly once any module emits events | Eventually a large, un-partitioned, never-pruned table | Build `event_relay.py` before any module starts calling `emit_event()` in earnest; define retention/archival for published rows | **P1** |
| 8 | No retry/timeout/circuit-breaker code | `app/integrations/` empty | Every future external call (Cognito/S3/Razorpay/SES/SNS) starts from zero resilience | A slow Razorpay or S3 call could hang a request indefinitely with no code path stopping it | Build these into the integration client base pattern before the first real integration client is written, not per-client after the fact | **P1** |
| 9 | `/health/ready` doesn't check Redis/queue | Shallow readiness probe | Orchestrator keeps routing to instances that can't fully serve requests | Silent partial-outage risk once Redis/SQS-dependent endpoints exist | Extend readiness check once those dependencies are actually used by any endpoint | **P2** |
| 10 | Monolithic single-revision Alembic baseline with `DROP SCHEMA CASCADE` downgrade | No incremental migration story yet; destructive downgrade with no guard | Low today (no real data), high once data exists | An accidental `alembic downgrade` against a populated environment is unrecoverable without a separate backup | Add a runtime guard (refuse downgrade if `environment != local`) before any non-local environment exists | **P2** |
| 11 | Dev JWT secret committed in `.env.example` | Low actual risk (clearly labeled, dev-only) but no enforcement | A copy-paste into a real prod `.env` would go undetected | Would only matter if someone skipped Stage 13's real Cognito cutover and shipped local auth mode to prod | Add a startup assertion: refuse to boot if `auth_mode=local` and `environment=production` | **P2** |
| 12 | No environment-specific config validation | `config.py` accepts any combination of settings | Misconfiguration (e.g., wildcard CORS) could ship silently | Low today, real once a real deploy pipeline exists | Add Pydantic validators asserting safe combinations per environment | **P3** |
| 13 | Dead `schema_migrations` table in `13_indexes.sql` | Superseded by Alembic's own version table | None (unused) | None | Remove for cleanliness | **P3** |
| 14 | Master Document Section 15 stale vs `SQL/*.sql` | Documentation drift already identified in prior review | New engineers reading only the Word doc build against stale schema | Onboarding risk, not a runtime risk | Write the Schema Addendum (already recommended, not yet done) | **P3** |

---

## 16. Production Readiness Checklist

- [ ] All 16 remaining modules built and tested
- [ ] Test suite exists and actually runs in CI (currently a no-op pass)
- [ ] P0 findings #1-#5 above closed
- [ ] `event_relay.py` built, outbox actually drains
- [ ] `app/integrations/` clients built with real retry/timeout/circuit-breaker behavior
- [ ] RLS policies audited table-by-table for the same role-only-vs-tenant-scoped gap found on 3 tables — no evidence the remaining ~35 policies were checked with this same scrutiny
- [ ] Real AWS cutover (Stage 13) completed and re-verified with the same rigor as local auth was
- [ ] Load test at a meaningful fraction of the 100K-concurrent target
- [ ] Security review repeated post-cutover (today's review only covers local-mode code paths)
- [ ] Disaster-recovery drill executed at least once

None of these are checked today. This list is the actual distance to "production-ready," not a calendar estimate.

---

## 17. Recommended Improvements

### Quick Wins (<1 day each)
- Fix the blocking `httpx.get` → `httpx.AsyncClient` (P0 #1).
- Add `clinic_id = rls_clinic_id()` to the three under-scoped RLS write-policies (P0 #2).
- Write one real test proving the auth 401/403/200 chain, wire it into CI so `pytest` stops being a no-op (P0 #4).
- Add the environment-mismatch boot guard for `auth_mode`/dev secret (P2 #11).
- Remove the dead `schema_migrations` table (P3 #13).

### Medium-term (this build phase, Stages 4-9)
- Complete or rename `require_clinic_scope` before a second module comes to depend on it (P0 #3).
- Build `event_relay.py` alongside whichever module first needs live notifications (per the dev plan, that's Stage 11, but the relay itself should exist earlier so events aren't silently piling up unconsumed from Stage 8 onward).
- Cache the identity/scope lookup in Redis (P1 #6) — do this once, in `middleware.py`, before it's copy-pasted as a pattern anywhere else.
- Build the `app/integrations/` base client pattern (retry/timeout/circuit-breaker) before the first real external integration (Files/S3 in Stage 7 is the first candidate).

### Long-term (Stages 10-22)
- Full RLS policy audit against the same rigor applied here, not just the tables this review happened to sample.
- Load testing program before Stage 15 (per the dev plan, this is already scheduled — don't skip it under deadline pressure).
- DR drill (already Stage 20 in the dev plan — keep it there, don't let it slip to "after launch").

---

## 18. Final Scores

| Category | Score | Why | Biggest Weakness | Biggest Strength |
|---|---|---|---|---|
| Overall Architecture (design) | 8/10 | Justified, non-fashionable decisions | Unproven at scale of more than 1 module | Honest, reasoned ADRs |
| Overall Architecture (built) | 2/10 | 1 of 17 modules exist | Everything not yet built | The 1 module that exists is correctly structured |
| System Design | 7.5/10 design, 3/10 built | Good event design, half-implemented | Outbox has no consumer | Transactional outbox pattern is correctly chosen and correctly written on the write side |
| Module Design | 7.5/10 design, 2/10 built | Sound boundaries on paper | 16 modules missing | `auth` module respects its own architecture rules |
| API Design | 7/10 design, 2/10 built | Good consolidation work in the catalog doc | 3 endpoints exist | Catalog document itself is rigorous |
| Security | 4/10 | Real gaps found by actually reading the code | RLS write-policy inconsistency; blocking JWKS bug | Schema-level security discipline is genuinely strong |
| Scalability | 7/10 design, unratable built | Correct instincts (BRIN, partitioning, cursor pagination) | Never tested | Partitioning planned before data exists, not retrofitted |
| Reliability | 3/10 | No resilience code anywhere yet | No retries/circuit-breakers/timeouts | Health checks exist (if shallow) |
| Performance | 3/10 unproven | Two real inefficiencies found in tiny existing codebase | Double DB connection per request | Async-first design intent throughout |
| Maintainability | 7/10 | Clean, well-commented, follows its own rules | Too little exists to fully judge | Folder structure genuinely will scale to 500+ endpoints if discipline holds |
| Cloud Readiness | 2/10 | Correctly local-only at this stage | Nothing cloud-side exists yet | Local-first plan deliberately defers this, not an oversight |
| Developer Experience | 7/10 | Docker one-command local stack, clear docs | No test suite to develop against yet | Genuinely easy to onboard into given the docs produced |
| Future Proofing | 6/10 design, 0/10 built | Schema has room (multi-country columns already present) | Nothing built touches these axes yet | Deliberately deferred rather than half-built now |
| **Production Readiness** | **2/10** | Cannot run a single clinical workflow today | Everything in Section 16's checklist | The foundation that exists was actually verified, not just claimed |

---

## 19. Final Verdict

**Overall Backend Architecture Score (as documented): 7.8/10**
**Overall System Design Score (as documented): 7.5/10**
**Overall Production Readiness Score (as built, today): 2/10**

**Would I approve this architecture for production? No.**

Not because the design is wrong — it's one of the better-reasoned pre-code architectures reviewed at this rigor level. The refusal is because "approve for production" has to mean the running system, and the running system is a foundation plus one stub module. That is exactly where a Stage-3-of-22 project should be; it is not a system a review board can wave through.

**Blocking issues before any production approval:**
1. P0 #1 — blocking I/O bug in the auth path (must fix before Stage 13, ideally now while it's cheap).
2. P0 #2 — RLS write-policy gaps on 3 tables (must fix before those tables carry real writes, i.e., before Stage 4-8 modules go live).
3. P0 #3 — `require_clinic_scope` is not real tenant enforcement yet.
4. P0 #4 — no test suite; CI is currently decorative.
5. P0 #5 — 16 of 17 modules don't exist.

**Estimated engineering effort before this could honestly be re-reviewed for production approval:** the Development Plan's own Stage 4-22 sequence is the correct estimate — this review found no reason to believe that plan is wrong, only confirmation that today's actual position is Stage 3, not Stage 21. The four quick-win P0 fixes (items 1-4 above) should happen this week, independent of and before the next module is started, because every subsequent module will inherit whichever of these patterns (fixed or not) exist when it's built.

---

*This review is a point-in-time snapshot. Re-run this level of scrutiny — reading actual code and actual policies, not trusting design intent — at minimum before Stage 13 (real AWS cutover) and before Stage 18 (production provisioning). Both are named checkpoints in the Development Plan; this review is what should happen at each one.*
