# Anava_App_v1 — Build Notes

Generated from live production schema introspection on 2026-07-20. Every table,
column, RLS policy, trigger, function, view, sequence, and enum in `SQL/v1/`
is sourced verbatim from the running database — nothing hand-transcribed from
memory. Verification counts below prove nothing was dropped.

## Verified zero-loss (source count → generated count)

| Object | Source | Generated |
|---|---|---|
| Tables | 61 | 61 |
| Columns | 719 | 719 |
| RLS policies | 152 | 152 |
| Triggers | 63 | 63 |
| App-specific functions | 11 | 11 |
| Enum types | 1 | 1 |
| Sequences | 1 | 1 |
| Views | 1 | 1 |
| Extensions | 3 | 3 |
| Primary keys | 61 tables (63 PK columns, some composite) | 61 |
| Unique constraints | 37 | 37 |
| Indexes (non-PK/unique-backing) | ~213 | 213 |

No table renamed. No column renamed. No RLS policy dropped. No trigger dropped.
No function dropped.

## Net-new work in this build (did not exist in source)

- **188 foreign keys** — curated by hand, every entry verified programmatically
  against real column names on both sides before being written (see
  `gen_v1_sql.py`'s self-check). 9 `*_id`-shaped columns deliberately excluded
  as non-references (5 polymorphic, 2 HTTP correlation IDs, 2 external Razorpay
  gateway IDs) — listed explicitly in `11_foreign_keys.sql`'s header comment.
- **Schema separation** — 61 tables placed into `core` (42), `reference` (13),
  `compliance` (3), `ops` (3). `analytics` schema created empty (no source data
  yet — future ETL target). `archive` schema created empty (lifecycle state,
  not a fixed table set).
- **Partitioning** (Layer 4) on 7 tables: `appointments`, `sessions`,
  `treatment_sessions` (yearly), `audit_logs`, `activity_logs`,
  `appointment_audit_logs`, `notifications` (monthly). Initial partitions cover
  2024-2028 (yearly) / Jan 2025-Dec 2027 (monthly) plus a DEFAULT partition
  catching anything outside that window.
- **`ca_doctor_assignments` gets RLS enabled** — it did NOT have RLS in
  production (confirmed live: `rls=False force=False`), inconsistent with its
  sibling assignment tables. Fixed here. Uses the same policy pattern as
  `clinic_staff_assignments`/`doctor_patient_assignments` — reviewed before
  final execution, since this is new protection, not a verbatim copy.
- **`anava_readonly`, `anava_compliance` roles** — new, least-privilege, per
  the Layer 3 design. Placeholder passwords in `02_roles.sql` — must be
  rotated before use.
- **`search_path`** set at the database level (`19_search_path.sql`) so every
  verbatim RLS policy, trigger, and function body above continues resolving
  bare table names correctly without a single line of their logic being
  rewritten. Table names are unique across all 5 schemas — verified, no
  collisions.

## Deliberately deferred — not silently dropped, named here on purpose

1. **`payments` partitioning** — NOT applied in this pass. `payments` has a
   `UNIQUE (idempotency_key)` constraint. Partitioning requires the partition
   key to be part of every unique constraint, which would weaken idempotency-key
   uniqueness to "unique per partition" instead of globally unique — a real
   product tradeoff, not a technical default. `payments` stays a normal table
   until this is explicitly decided.
2. **Enum/CHECK constraints on ~20 status-shaped columns** (`appointments.status`,
   `patients.registration_status`, `treatment_cycles.status`, etc.) — NOT
   applied. Converting these safely requires auditing the full literal value
   set each column actually uses in live app code first (the `appointments.status`
   set is confirmed from `scheduling/service.py`: `scheduled, confirmed,
   checked_in, in_progress, completed, no_show, cancelled, rescheduled` — but
   the other ~19 columns haven't had the same audit). Locking a wrong value
   set silently drops rows at data-migration time. Columns remain `TEXT`
   with their existing defaults, unchanged from source, until that audit runs.
3. **`mrn_seq` continuity** — `04_sequences.sql` creates a fresh sequence
   starting at 10001 (matching source's definition). At real data migration
   (Phase D), this must be advanced past the highest imported MRN before the
   app writes through it again. Not a structural DDL concern — a migration
   runbook step.
4. **Ongoing partition maintenance** — the initial partitions above are a
   starting buffer, not a permanent solution. Someone/something must create
   the next partition ahead of the current date on a recurring basis (Layer 7
   operational job), or inserts eventually hit the DEFAULT partition instead
   of a dated one. Not automated in this SQL — flagged as a required follow-up.
5. **Grants replicate the Layer 3 design, not a byte-for-byte copy of the 248
   live grants** — live grants were mostly default-owner privileges on a single
   flat schema; the new grants implement the intended least-privilege model
   (`anava_app`, `anava_readonly`, `anava_compliance`) instead of reproducing
   an undifferentiated set. Reviewed as a deliberate design choice, not an
   oversight.
6. **Compliance-schema net-new tables** (`erasure_requests`,
   `data_portability_requests`, `staff_termination_authorizations`,
   `compliance_incidents`, `guardian_id` on `consent_records`, grievance-officer
   role type) — not in this build. These come from the separate Closure Schema
   Change Requirements workstream, Layer 5, and were explicitly scoped as a
   follow-on, not part of this Phase A/B structural build.

## Resolved during build — 3 real issues hit and fixed, not papered over

1. **`appointments` and `sessions` de-partitioned.** Originally planned as
   yearly-partitioned per Layer 4. Postgres requires the partition key inside
   every unique constraint on a partitioned table — that would have forced
   their PKs composite `(id, date)`, which breaks every FK pointing at the
   single-column ID (`appointment_requests.parent_appointment_id`,
   `payments.session_id`, 5 references total). Fixing that properly means
   adding a companion date column to every referencing table — real future
   work (expand-contract), not something to improvise mid-build. Both tables
   kept as normal, unpartitioned tables with their original single-column PKs,
   exactly matching source. The other 5 candidates (`treatment_sessions`,
   `audit_logs`, `activity_logs`, `appointment_audit_logs`, `notifications`)
   have zero incoming FKs and partitioned cleanly — no compromise there.
2. **Missing semicolons after function bodies** (`14_functions.sql`) —
   `pg_get_functiondef()` doesn't include a trailing `;`, caused a syntax
   error on the second function in the file. Fixed in the generator.
3. **`search_path` timing** — `ALTER DATABASE ... SET search_path` only takes
   effect for *new* connections, not the session already running the build.
   PL/pgSQL functions resolve their embedded table references at `CREATE
   FUNCTION` time (not lazily at first call), so `recalculate_final_result`
   failed with "relation does not exist" until `search_path` was also `SET`
   on the live session before running `14_functions.sql`.
4. **Extensions (`pgcrypto`, `btree_gist`) landed inside `core` schema** —
   `CREATE EXTENSION` with no `SCHEMA` clause uses the first `search_path`
   entry. Not a functional break (249 extension-internal functions still
   worked), but architecturally messy — `core` is meant to be pure business
   data. Fixed: moved both extensions to a dedicated `extensions` schema,
   appended to `search_path`. Applied live via `ALTER EXTENSION ... SET
   SCHEMA` (safe — changes catalog location only, not the function's OID, so
   every existing `DEFAULT gen_random_uuid()` binding kept working unchanged).

## Real finding surfaced by functional smoke testing — not a v1 defect

The `postgres` role bypasses RLS despite `rolsuper=false`, `rolbypassrls=false`,
and `FORCE ROW LEVEL SECURITY` on every table (which per Postgres docs should
apply RLS to the table owner too). Confirmed via live test: `postgres` reading
`patients` with zero session context sees all 7 rows in source prod; `anava_app`
correctly sees 0. **Confirmed present in source production too, not introduced
by this build** — `Anava_App_v1` faithfully reproduced existing behavior.
`anava_app` (the actual app role, `DATABASE_URL`) is correctly RLS-scoped —
verified live, blocked as expected. Only the `postgres` credential
(`MIGRATION_DATABASE_URL`, used for migrations/admin/this build) sees
unfiltered data. That credential was already structurally separated from the
app in `.env` — this finding is the reason that separation matters, not a new
requirement. Full detail in memory: `project_rls_bypass_finding.md`.

## Build status: COMPLETE

`Anava_App_v1` created and fully built on RDS (2026-07-20). All 21 files in
`SQL/v1/run_all.sql` order executed successfully. Verified against source with
automated counts — every number matches exactly:

| | Source | Anava_App_v1 |
|---|---|---|
| Tables | 61 | 61 |
| RLS enabled+forced | 61 | 61 |
| RLS policies | 152 | 152 |
| Triggers | 63 | 63 |
| App functions | 11 | 11 |
| Foreign keys | 188 (curated) | 188 |
| Primary keys | 61 | 61 |
| Unique constraints | 37 | 37 |

Functional smoke test passed: `gen_random_uuid()` default works, `anava_app`
correctly blocked by RLS without session context, `super_admin` context
correctly permitted, `fn_audit_trigger` fired into partitioned
`compliance.audit_logs`, `fn_set_updated_at` fired on `UPDATE`. Test row
inserted and cleaned up.

**Not done — deliberately out of scope for this build**, see "Deliberately
deferred" above: `payments` partitioning, enum/CHECK audit on status columns,
ongoing partition maintenance automation, compliance-schema net-new tables
(Layer 5, gated on legal sign-off).

## Data migration — COMPLETE (2026-07-21)

All real production data copied from `public.*` (source) into the layered
schemas. Verified: **12,870 / 12,870 rows migrated, 0 mismatches, across 59
tables** (`alembic_version`/`schema_migrations` excluded — old alembic
history, meaningless in a database built from `SQL/v1/` files, not alembic).

Method: topological order by FK dependency, triggers disabled per-table during
load (avoids synthetic `audit_logs` entries, preserves real `created_at`/
`updated_at`), FK constraints kept **active** throughout — deliberately, so
any real data-quality problem in source surfaces as an error instead of being
silently imported. `mrn_seq` resynced to 10024 (past the highest imported real
MRN, 10023) so new patient registrations don't collide with migrated ones.

### A second, larger class of FK mapping errors — found and fixed via the data itself

The curated FK_MAP in Section "Net-new work" above was **wrong for 37 of its
188 entries** — not a typo, a systematic wrong assumption. The app resolves
`doctor_id`/`ca_id`/`patient_id`-shaped columns to **`profiles.id`**, not to
the role table's own primary key (`doctors.doctor_id` / `clinical_assistants.ca_id`
/ `patients.patient_id`) — confirmed both empirically (every real row tested
matched `profiles.id`, zero exceptions) and in the app's own code:
`app/core/resolve.py`'s docstring states this outright — `resolve_doctor_profile_id`
/ `resolve_ca_profile_id` / `resolve_patient_profile_id` exist specifically
because "FK columns reference profiles(id) directly, while the public API
accepts the role-table id instead." **`ca_doctor_assignments` is the sole
exception** — it stores the role tables' own PKs directly (confirmed in
`app/modules/staff/repository.py` — a raw insert with no resolution step).

This was caught because FK constraints were kept active during migration
instead of disabled — the very first row that exercised one of these 37
columns (`patients.primary_doctor_id`) failed with `ForeignKeyViolationError`
instead of silently importing a wrong reference. All 18 `doctor_id`/`ca_id`
entries and all 19 `patient_id` entries were corrected: dropped and recreated
against `profiles(id)`, both in the generator (`FK_MAP` in `gen_v1_sql.py`,
so `11_foreign_keys.sql` now reflects this permanently) and live on
`Anava_App_v1`. Re-verified after the fix: still 188 FKs, all still valid.

**Two smaller issues also hit and fixed during this pass:**
- A leftover test artifact (`'Test Region'` row + its cascaded audit-log
  entries) from the earlier RLS smoke test had never been cleaned up because
  that insert wasn't supposed to succeed in the first place. Found via a row
  count that didn't match source (`regions`: 2 vs source's 1) and removed
  before migrating real data.
- The extensions-schema fix (`pgcrypto`/`btree_gist` → dedicated `extensions`
  schema) had been applied live earlier but not baked into the generator
  scripts — a routine regeneration silently reverted it. Fixed in the
  generator source itself (`gen_v1_sql_part2.py`, `gen_v1_sql_part4.py`) so
  it can't regress on a future rebuild.

### Final verification

| | Source | Anava_App_v1 |
|---|---|---|
| Total rows (59 tables) | 12,870 | 12,870 |
| Mismatched tables | — | 0 |
| Foreign keys | 188 (37 corrected) | 188 |
| `mrn_seq` | last_value 10023 (real) | resynced to 10024 |

Flow Pivot tables (`treatment_cycles`, `sessions`, `appointments`,
`treatment_sessions`, `payments`, and related) all migrated with **0 rows**
each — confirmed empty in source before migrating, so Blocker 1 (Flow Pivot
model decision) was genuinely moot for this migration. It still must be
resolved before any *new* data is written through either flow.

## Layer 5 — Compliance schema, COMPLETE (2026-07-21)

Built and verified against the 12 items in the Closure Schema Change
Requirements doc (sourced from the compliance policy, treated as final per
approval to proceed). Files: `20_layer5_compliance_tables.sql` through
`24_layer5_grants.sql`, appended to `run_all.sql`.

**6 new tables** (compliance schema): `erasure_requests`, `erasure_request_items`,
`data_portability_requests`, `staff_termination_authorizations`,
`compliance_incidents`, `manual_snapshots` — 18 RLS policies (3 per table),
all `ENABLE + FORCE ROW LEVEL SECURITY`, FKs to `profiles(id)` throughout.

**11 new columns**: `profiles.is_anonymized`/`anonymized_at`; `patients.retention_basis_cleared_at`,
`legal_hold`, `closure_type`, `closure_reason`, `closed_at`, `rejoin_deadline`,
`portal_access_mode`, `last_clinical_contact_at`; `doctors.legal_hold`;
`consent_records.guardian_id`.

**Requirement-by-requirement:**

| # | Requirement | Status |
|---|---|---|
| 1 | erasure_requests + erasure_request_items | Built |
| 2 | Retention/anonymisation columns | Built |
| 3 | data_portability_requests | Built |
| 4 | Patient closure-state columns | Built |
| 5 | Scheduled purge/anonymisation worker | **Not built — Python application code, not SQL/DDL.** DB structure is ready to support it (retention columns, bucket classification on erasure_request_items). Follow-up work, separate from this schema build. |
| 6 | Guardian consent linkage | Built (`consent_records.guardian_id`) |
| 7 | Staff termination dual-authorization | Built |
| 8 | compliance_incidents | Built |
| 9 | Grievance Officer role | No DDL needed — `admins.admin_type` is unconstrained `TEXT` (matches the rest of the schema's status columns), so `'grievance_officer'` is already a valid value. Provisioning one requires setting `profiles.role='grievance_officer'` too (mirrors how `super_admin`/`regional_admin`/`clinic_admin` already work — verified: both columns hold identical values for every existing admin). |
| 10 | De-identified analytics schema | Deliberately still deferred — `analytics` schema exists (Phase A) but empty; building aggregate tables ahead of an actual ETL consumer risks guessing the wrong shape, per the original P2 recommendation. |
| 11 | manual_snapshots | Built |
| 12 | `patients.is_active` naming reconciliation | No schema action — original recommendation was to fix the policy doc's Section 12 reference, not add a redundant column. |

**10 of 12 fully implemented in schema. 1 (#5) is real application code, not
database work. 1 (#10) deliberately deferred pending a consumer** — both
judgment calls carried over from the original gap analysis, not silently
dropped now.

**Functional smoke test** (via `anava_app`, not `postgres`): patient filed
their own erasure request successfully; `response_due_at` auto-computed to
exactly 30 days out (Section 3.5's DPDP timeline); patient blocked by RLS from
filing a request for a different patient; patient's `SELECT` correctly scoped
to their own 1 row only. Test row insert verified — **the cleanup DELETE in
that test silently no-op'd** (see the retention-worker section below for why
and what it revealed); the stray row was found and removed later.

## Retention & erasure worker — COMPLETE (2026-07-21)

`app/workers/retention_purge.py` — item #5, the one requirement from the
compliance doc that's genuine application code rather than schema. Built
following the existing `event_relay.py` conventions (SQLAlchemy,
`get_migration_engine()`, `structlog`, run-once/run-forever split). Config-driven
like the rest of the app — connects via `DATABASE_URL`/`MIGRATION_DATABASE_URL`,
so it targets whichever database the app is pointed at. It currently targets
the old database (cutover, Phase G, hasn't happened) — tested against
`Anava_App_v1` today by overriding those env vars for the test run only, not
by hardcoding the new DB name into application code.

**Two jobs:**
1. **Erasure request processing** — classifies each open request's linked data
   into delete_now / retain_locked / compliance_evidence (14-category registry
   covering Section 12's mapping table), then hard-deletes delete_now items
   once their 30-day grace window passes, advancing the request to `completed`.
2. **Retention sweep** — recomputes each non-anonymised patient's retention
   clock (`GREATEST` of last clinical contact + 7yr, last financial transaction
   + 8yr — the *later* window, per Section 7.2) and anonymises the profile
   once it's cleared and `legal_hold = false`. Also drops partitions on the 5
   partitioned tables once their entire date range is past retention — a
   `DROP`, not a row-by-row `DELETE`.

**`last_clinical_contact_at`/`retention_basis_cleared_at` have no write-through
from the app yet** (flagged as future work in the original design) — the
worker computes both itself every run rather than assume they're already
current, so it's correct standalone instead of silently no-op'ing on empty
columns forever.

**Cognito `AdminDeleteUser`** (Section 7.2, final step after anonymisation) is
NOT called from this worker — logged as a structured event
(`profile_anonymized_cognito_delete_pending`) so it's visible and traceable,
not silently skipped. Needs an `app/integrations/cognito.py` hook before this
is production-complete.

### End-to-end test (synthetic data, not real patients, fully cleaned up)

- **Scenario A** — synthetic patient with an 8-year-old completed appointment.
  Worker correctly computed `last_clinical_contact_at` = that date,
  `retention_basis_cleared_at` = +7 years (already past), anonymised the
  profile (`is_anonymized=true`, name replaced with `ANON-<hash>`).
- **Scenario B** — synthetic patient with 1 notification, filed their own
  erasure request. First run classified it (`notifications` → `delete_now`);
  after backdating the item's `created_at` past the 30-day grace, second run
  hard-deleted the notification and advanced the request to `completed`.
- **Bonus, unplanned but correct**: the same run dropped 6 already-empty
  `notifications` partitions (Jan-Jun 2025) whose full date range had already
  aged past the 1-year retention window — proof the partition-drop logic
  works, not just designed.

### A real finding, caught by this test — not a worker bug, a genuine gap

Cleaning up Scenario B's synthetic erasure request via `anava_app` silently
did nothing — **no `DELETE` RLS policy exists on `erasure_requests` or any of
the 6 new Layer 5 tables.** With `FORCE ROW LEVEL SECURITY` and no policy for
a given command, that command matches zero rows — not an error, a silent
no-op. This is *probably the correct design*, not an oversight to fix:
erasure/portability/incident/termination records are compliance evidence —
Bucket 3 in the policy's own classification, meant to survive independently
and never be deleted through the app layer. But it was never a stated design
choice, and it's exactly what let last turn's smoke-test row and this
session's Scenario B row both survive their intended cleanup silently. Worth
a deliberate decision (keep as-is, matching Bucket 3 semantics) rather than
leaving it as an accidental side effect nobody decided on purpose — flagging
for confirmation, not changing it unilaterally.
