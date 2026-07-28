# Anava Clinic — Development Plan v1.0
## Start to Production: Full Build-to-Deploy Roadmap

Companion to `Anava_Backend_Architecture_v1.md` and `Anava_API_Endpoint_Catalog_v1.md`. This document sequences every stage from today's setup through production go-live and post-launch operation, for a solo developer paired with Claude Code (no parallel human tracks — sessions are sequential, paced by review time and external dependencies, not raw coding speed).

**Core principle driving the sequencing:** local-first, cloud-second. Every module is built and proven against local Docker services (Postgres, Redis, filesystem-as-S3, mock-JWT auth) before any real AWS service is wired in. This keeps AWS/Cognito/Razorpay account provisioning off the critical path — those get started in parallel today and wired in as a dedicated later stage, not depended on from day one.

---

## Master Timeline (all stages, in order)

| # | Stage | What it delivers | GO condition | Needs you specifically |
|---|---|---|---|---|
| 0 | Pre-work (today, parallel) | AWS account/IAM request started, Razorpay sandbox signup started, PRS/anamnesis seed content sourced | Requests submitted | **Yes — start today** |
| 1 | Repo + CI skeleton | Folder structure (Section 5), GitHub repo, CI pipeline running lint/typecheck/test on every push, even against near-empty code | A trivial PR triggers and passes CI | No |
| 2 | Foundation | Docker Compose (Postgres+Redis), fix P0 SQL bug, Alembic migration from `SQL/*.sql`, `/health` endpoint | `docker compose up` + `alembic upgrade head` + `/health` 200 | No |
| 3 | Core package | DB session/`SET LOCAL` helper, mock-JWT auth, permission dependencies, exception hierarchy, outbox writer | Protected test endpoint 403s/200s correctly | No |
| 4 | Admin + Staff modules | Regions, clinics, clinic_requests, staff CRUD, staff_requests | Script creates region→clinic→staff via API | No |
| 5 | Consent + Anamnesis + PRS (general_registration) | Pulled forward — registration needs these to exist | Consent sign, anamnesis submit, general PRS submit all work via API | **Yes — seed content must exist** (or use placeholders now, swap later) |
| 6 | Patients module | Full 6-step registration, doctor auto-allocation | End-to-end registration script reaches `registration_complete` + doctor allocated | No |
| 7 | Files module | EEG + medical history, local-filesystem-as-S3 | Presign→upload→review works locally | No |
| 8 | Clinical + Scheduling | Cycles, protocol requests, S1-S4 sessions, treatment plans/sessions, appointments, minimal payments stub (status field + manual waive only) | Full S1→S2→(S3→S4)→treatment sequence runs, extended-session payment gate blocks correctly | No |
| 9 | PRS main_clinical + followup | Extends stage 5's scoring engine to the other two stages | Session 1 / follow-up assessments score correctly | No |
| 10 | Store + Inventory + real Razorpay | Device/accessory orders, doctor approval, dispatch, real payment flow | Full device order chain incl. real Razorpay sandbox payment | **Yes — Razorpay sandbox keys needed** |
| 11 | Notifications + real-time (SSE) | Outbox `LISTEN`/`NOTIFY` relay, Redis pub/sub, SSE endpoint | Booking pushes a live event to a connected test client | No |
| 12 | Follow-up + Transfers + Exit | Follow-up cycles, relocation transfer, clinic closure | Relocation carries an active cycle over without restart | No |
| 13 | Real AWS cutover | Swap every local mock for real Cognito, RDS, S3, Secrets Manager | App runs identically against real AWS; `bootstrap_superadmin.py` succeeds | **Yes — AWS access must be granted by now** |
| 14 | Logging hardening | BRIN indexes on both log tables, partitioning, RLS policy audit (every table, not just written but tested) | Section 22 tech-debt checklist: all P0/P1 closed | No |
| 15 | Security & load hardening | WAF rules, rate limiting live, secrets rotated out of any dev shortcuts, load test at target concurrency | Load test report meets latency targets, security checklist (Section 18) passed | No |
| 16 | CI/CD to staging | GitHub Actions: build→push ECR→deploy staging→smoke test | A merge to main auto-deploys to staging and passes smoke tests | No |
| 17 | Staging soak + UAT | Run real workflows end-to-end in staging with synthetic (never real PHI) data, you personally click through every role's golden path | Sign-off checklist per role (Doctor, Receptionist, Admin, etc.) | **Yes — you do the UAT** |
| 18 | Production environment provisioning | Separate AWS account/VPC, production RDS Multi-AZ, production Cognito pool, production S3 bucket + replication, Secrets Manager prod secrets | Infra-as-code applied, nothing manually clicked that isn't reproducible | **Yes — approves real AWS spend** |
| 19 | Production deploy (blue/green) | First production deploy via ECS+CodeDeploy blue/green, DNS cutover | Health checks green on new target group before old one is retired | **Yes — go/no-go call** |
| 20 | Disaster-recovery drill | Restore latest RDS snapshot into a scratch environment, verify data integrity, confirm the DR runbook (Section 21) actually works | Restore succeeds, runbook followed as written, gaps fixed | No |
| 21 | Hypercare (first 2 weeks live) | Daily log/alert review, fast-turnaround bug fixes, no unattended changes | Zero unresolved P0/P1 incidents by end of window | **Yes — you're the first responder alongside me** |
| 22 | Steady-state operations | Normal sprint cadence, Section 24 future-enhancements backlog groomed as real needs arise | — | Ongoing |

---

## Part A — Pre-Work (Stage 0, start immediately, runs in parallel with everything else)

These have external lead time — starting them late is the single most common way a plan like this hits a wait it didn't need to:
- **AWS account + IAM access** for whoever/whatever needs to provision Cognito/RDS/S3/Secrets Manager later (Stage 13, 18).
- **Razorpay sandbox account** and test-mode API keys (Stage 10).
- **PRS scale content and anamnesis question content** — the actual clinical questionnaire text/scoring tables. If this doesn't exist yet in final form, say so now: Stage 5 will use placeholder seed data and swap real content in later without blocking on it, but real content should be sourced in parallel starting today, not discovered as a gap when Stage 5 arrives.

## Part B — Core Feature Development (Stages 1-12)

This is the session plan already agreed: builds every module in dependency-correct order (not the Master Document's product-flow letter order — Consent/Anamnesis/PRS-general are pulled forward because Patient Registration literally cannot complete without them). Each stage is proven against local Docker services only — no cloud dependency, so nothing here can be blocked by AWS/Razorpay account delays. RLS policies are written and tested **per table, in the same stage that table's module is built** — not deferred to one late "apply RLS" phase, which would otherwise become a high-risk retrofit across 50+ tables all at once.

## Part C — Hardening (Stages 13-15)

This is where local mocks get replaced with real cloud services (Stage 13) and where the Section 22 technical-debt list gets closed out for real: BRIN indexes and partitioning on `audit_logs`/`activity_logs` before they accumulate production data (retrofitting partitioning on a live multi-hundred-million-row table is a much larger job than doing it before data exists), a full pass confirming every RLS policy is not just written but actually tested against real role/tenant scenarios, and a load test at a realistic concurrency slice of the 100K-user target before trusting production traffic to it.

## Part D — Deployment Pipeline (Stages 16-19)

CI/CD is set up in Stage 1 (not bolted on later) so every stage from 2 onward is already covered by automated lint/type-check/test on every push — by the time Stage 16 formalizes the staging deploy step, the pipeline is a known, exercised thing, not a new risk introduced at the end. Staging soak (Stage 17) is where you personally walk every role's golden path against synthetic data — this is the last checkpoint before anything touches a real AWS production account, and it's explicitly a "you do this" stage, not something to delegate to me, since sign-off on clinical workflow correctness is a judgment call only you can make for this business.

Production environment provisioning (Stage 18) is a separate AWS account/VPC from staging — this is non-negotiable for a healthcare system even at small scale, because "staging" must never be able to reach production PHI, and vice versa. Blue/green deploy (Stage 19) means the very first production release is already using the safe-rollback deployment mechanism, not a plain in-place deploy that gets "upgraded to blue/green later" — retrofitting safe deploys after a bad first deploy already happened defeats the purpose.

## Part E — Go-Live & Operate (Stages 20-22)

Disaster-recovery drill (Stage 20) happens **before** real patient data accumulates, not after — an untested backup/restore procedure discovered to be broken during an actual incident is the worst possible time to find that out. Hypercare (Stage 21) is a deliberately short, high-attention window right after go-live, not indefinite — it ends when the system has proven itself stable, at which point work moves to Stage 22's normal cadence, drawing from the Section 24 future-enhancements list only as real usage actually demands each item (fine-grained RBAC, notification preferences, read replicas, etc.) — never speculatively.

---

## Blocker & Risk Register

| Risk | Where it would bite | Mitigation already built into this plan |
|---|---|---|
| AWS account provisioning takes days/weeks | Stages 13, 18 | Started in Stage 0, runs in parallel with ~12 stages of local-only development — by the time it's needed, it's had a long runway |
| Razorpay sandbox signup delay | Stage 10 | Same — started Stage 0, needed only at Stage 10 |
| PRS/anamnesis clinical content not finalized | Stage 5 | Placeholder seed data unblocks development now; real content swap is a data-only change, not a code change |
| RLS policies retrofitted all at once late | Historically the biggest source of late-stage surprise bugs in multi-tenant systems | Eliminated by writing+testing RLS per table, per stage, throughout Part B |
| Untested backup/DR procedure discovered broken during a real incident | Post-launch | DR drill is Stage 20, before go-live, not after |
| Partitioning retrofitted on a live, huge log table | Stage 14 | Scheduled before Stage 18 (production provisioning) — partitioning is defined before any production data exists to migrate |
| First production deploy is the first time blue/green is exercised | Stage 19 | CI/CD (including staging deploy) is live from Stage 16, exercised repeatedly against staging before it's ever pointed at production |
| Scope creep from Section 24 "future enhancements" pulled in early | Ongoing | Explicitly gated — only pulled into Stage 22 backlog when a real, measured need appears, never spec'd in advance |

---

## What Paces This Plan

Not code-writing speed — module scaffolding, endpoint wiring, and tests are fast with AI pairing. The actual pace-setters are: (1) your review/understanding time before each stage's output is trusted as a foundation for the next (say so anytime you want a slower walkthrough of a stage instead of moving straight to the next), (2) the handful of stages explicitly marked "needs you specifically" — account approvals, seed content sourcing, UAT sign-off, production go/no-go — none of which I can do on your behalf, and (3) how much calendar time you can actually spend per week, which only you know and which turns this stage list into real dates once you tell me.

---

*This is v1.0. Update alongside the architecture doc and API catalog as stages complete or get reordered — don't let this drift into a stale planning artifact the way Master Document Section 15 drifted from the real schema.*
