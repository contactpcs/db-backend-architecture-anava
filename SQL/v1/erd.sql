-- ###########################################################################
--
--   Anava — NeuroWellness
--   erd.sql — COMPLETE consolidated schema for anava_v1
--
--   PostgreSQL 15 (AWS RDS, ap-south-1)
--
-- ###########################################################################
--
-- WHAT THIS FILE IS
--
-- One runnable file containing the ENTIRE database: every object from
-- SQL/v1/00_schemas.sql through 29_patient_guardian_contact.sql, MERGED with
-- the net-new Treatment Protocol module and multi-device neuromodulation
-- catalogue.
--
-- It is a consolidated snapshot, not a replacement for the numbered files.
-- Those remain the reviewable, per-layer build order the architecture document
-- (Section 7, "Local artifacts") calls for. This file is the single-artifact
-- view of the same schema — useful for ERD tooling, for reviewing the whole
-- model at once, and for standing up a complete database in one command.
--
--   psql -d anava_v1 -v ON_ERROR_STOP=1 -f erd.sql
--
--
-- CONTENTS — 89 base tables (plus 1,164 child partitions)
--
--   67  existing tables, verbatim from files 00-29 — the 61 in the original
--       build plus the 6 added by the Layer 5 compliance workstream. No table,
--       column, constraint, policy or trigger is renamed, dropped or altered.
--   22  net-new tables for the Treatment Protocol module:
--        17 in `reference` — device registry, shared clinical catalogue,
--           and per-device placement + dosing tables for all six modalities
--         5 in `core`      — treatment_protocols, protocol_sessions,
--           protocol_followups, and the two PRS response tables
--
--
-- LAYER ORDER (architecture document, Section 4)
--
--   Layer 1  Namespace      §1-2    six schemas; every table placed by what it
--                                   is — core (live OLTP), reference (static
--                                   catalogue), compliance (write-once legal),
--                                   ops (plumbing), analytics + archive (empty)
--   Layer 2  Integrity      §3-9    enums, PKs, uniques, FKs, CHECKs, indexes,
--                                   functions, triggers. Every FK is
--                                   ON DELETE RESTRICT — never CASCADE on
--                                   clinical or financial data.
--   Layer 3  Access         §10-12  roles, RLS (ENABLE + FORCE on every table),
--                                   policies, least-privilege grants
--   Layer 4  Partitioning   §4      declared inline with the seven partitioned
--                                   tables; see the note in §4
--   Layer 5  Retention      §13     compliance tables and columns; retention
--                                   semantics gated on Blocker 2
--
--
-- THE TWO BLOCKERS (architecture document, Section 3)
--
--   Blocker 1 — Flow Pivot fork. Both treatment models are present here
--   because both are present in the source schema: the fixed Session 1-4
--   model (sessions, treatment_cycles, treatment_sessions) and the post-pivot
--   Appointment model (appointments, appointment_requests). This file does not
--   resolve that fork — it reproduces what exists.
--
--   The NEW Treatment Protocol module deliberately sits on the post-pivot side
--   only: protocol_sessions references core.appointments and never touches
--   core.treatment_sessions or core.sessions. So the new module is buildable
--   now, ahead of the doctor sign-off Blocker 1 waits on, and is unaffected
--   whichever way that decision lands.
--
--   Blocker 2 — Legal sign-off. Layer 5 compliance TABLES are included (they
--   exist in files 20-24 and were built on 2026-07-21), but no retention or
--   erasure SEMANTICS are enforced by DDL here. Retention classes are recorded
--   as COMMENTs for the purge worker to read.
--
--
-- VOCABULARY — Plan vs Protocol
--
--   Treatment Plan     core.treatment_plans (existing). The superset: prior
--                      history + the active protocol + the doctor's signature.
--                      Accumulates across the course of treatment.
--   Treatment Protocol core.treatment_protocols (new). The narrower device +
--                      montage + dosing + session-plan configuration a doctor
--                      sets once to start a course. A CHILD of a plan.
--
--   Setting a protocol (session_count + follow_up_every_n) generates every
--   device-session appointment and every follow-up appointment up front:
--   20 sessions with follow-up every 5 produces 20 device-session appointments
--   plus 4 follow-ups, after sessions 5, 10, 15 and 20.
--
--
-- PREREQUISITE — search_path
--
--   §12 issues ALTER DATABASE ... SET search_path. That takes effect for NEW
--   connections only. Every RLS policy in this file calls rls_user_role() /
--   rls_user_id() / rls_clinic_id() unqualified and resolves them through that
--   search_path, so the database must be created and its search_path set
--   BEFORE the connection that runs this file is opened:
--
--     createdb anava_v1
--     psql -d postgres -c "ALTER DATABASE anava_v1 SET search_path =
--         core, reference, compliance, analytics, ops, extensions, public;"
--     psql -d anava_v1 -v ON_ERROR_STOP=1 -f erd.sql
--
--   Running it on a connection opened earlier fails with
--   "function rls_user_role() does not exist" — the same timing trap NOTES.md
--   records for 14_functions.sql.
--
--
-- VERIFICATION
--
--   This file was executed end to end against a throwaway PostgreSQL 16
--   instance. It ran clean from an empty database, and the following were
--   tested functionally rather than assumed:
--     - the exactly-one placement/dosing CHECKs reject zero and two placements
--     - the same-device trigger rejects a tDCS device paired with an rTMS dose
--     - the tDCS (1 anode + 1 cathode) and HD-tDCS (1 + up to 4) electrode
--       rules both reject violations
--     - fn_generate_protocol_sessions(20 sessions, follow-up every 5) produced
--       exactly 24 appointments — 20 device sessions plus follow-ups after
--       sessions 5, 10, 15, 20 — and refuses to run a second time
--     - a PRS row on each response table resolves back to its exact session
--       ordinal; a second PRS on the same session is rejected
--     - the audit triggers wrote every insert into compliance.audit_logs
--
--   Note: verified on PostgreSQL 16; the production target is PostgreSQL 15.
--   Nothing here uses a 16-only feature, but that difference is stated rather
--   than glossed over.
--
-- ###########################################################################


-- ###########################################################################
-- §1  LAYER 1 — Schemas, extensions, roles, sequences
-- ###########################################################################


CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS reference;
CREATE SCHEMA IF NOT EXISTS compliance;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS archive;

COMMENT ON SCHEMA core IS 'Operational data — patients, appointments, treatment, clinical records, commerce. High-write, grows forever.';
COMMENT ON SCHEMA reference IS 'Static catalogue/config data — PRS scales/questions, anamnesis catalogue, consent templates, products. Read-heavy, cacheable.';
COMMENT ON SCHEMA compliance IS 'Legal/audit records — audit trail, consent evidence, (future) erasure/portability/incident tables. Write-once, long retention.';
COMMENT ON SCHEMA analytics IS 'De-identified aggregates for business analytics. No PII. Fed by batch ETL from core, one-way.';
COMMENT ON SCHEMA ops IS 'Infrastructure plumbing — outbox events, migration bookkeeping. Not business data.';
COMMENT ON SCHEMA archive IS 'Lifecycle state, not a fixed table set — detached cold partitions awaiting purge/anonymisation window.';


CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS btree_gist SCHEMA extensions;
-- plpgsql is installed by default in every Postgres database, no action needed.
-- extensions schema is appended to search_path (19_search_path.sql) so gen_random_uuid()
-- and the GIST exclusion operators resolve without qualification, same as before.


-- anava_app exists cluster-wide on the RDS instance (used by the current
-- production database), which is why 02_roles.sql does not create it. That
-- assumption does NOT hold on a fresh cluster — a local build has no roles at
-- all, and §19's grants then fail with 'role "anava_app" does not exist'.
-- Created here conditionally so it is a no-op where it already exists.
--
-- anava_migrate is deliberately not created: the 'postgres' role already
-- serves that purpose today (it owns every table) and continues to for
-- anava_v1 — no redundant role.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anava_app') THEN
        CREATE ROLE anava_app LOGIN PASSWORD 'CHANGE_ME_BEFORE_USE';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anava_readonly') THEN
        CREATE ROLE anava_readonly LOGIN PASSWORD 'CHANGE_ME_BEFORE_USE';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anava_compliance') THEN
        CREATE ROLE anava_compliance LOGIN PASSWORD 'CHANGE_ME_BEFORE_USE';
    END IF;
END
$$;

COMMENT ON ROLE anava_app IS 'Application role. RLS-scoped, read/write on core + compliance + ops, read on reference.';

COMMENT ON ROLE anava_readonly IS 'SELECT-only across all schemas. For reporting/BI. No RLS bypass.';
COMMENT ON ROLE anava_compliance IS 'SELECT/UPDATE on compliance schema only. For erasure/portability/grievance tooling.';
-- Passwords above are placeholders — rotate via ALTER ROLE ... PASSWORD before granting these roles to anyone.


-- Matches source definition exactly (start_value=10001). This is a FRESH sequence
-- for the empty anava_v1 structure. At Phase D (real data migration), this sequence's
-- current value must be advanced past the highest imported MRN before the app writes
-- through it again — that is a migration-time step, not handled by this DDL.
CREATE SEQUENCE core.mrn_seq
    START WITH 10001
    INCREMENT BY 1
    MINVALUE 1
    NO CYCLE;


-- ###########################################################################
-- §2  LAYER 2 — Enum types
-- ###########################################################################

-- Existing: one enum from 03_enum_types.sql.
-- New: five enums for the Treatment Protocol module. Layer 2 calls for
-- native enums on status-shaped columns; new columns get them from day one.
-- The ~20 pre-existing TEXT status columns are NOT converted here — NOTES.md
-- defers that pending a literal-value audit of live app code.


CREATE TYPE core.assessment_taken_by AS ENUM ('patient', 'doctor_on_behalf');

-- v2 Layer 2 — NO new native ENUMs. The architecture v2 board is explicit:
-- "No new native ENUMs — that jumps the audit queue and ALTER TYPE can never
-- remove a value." The five types this module originally declared as enums are
-- therefore text columns with a CHECK pinning the literal set, "ready for
-- wholesale promotion" once the deferred literal-value audit runs across all
-- ~20 pre-existing status columns.
--
-- core.assessment_taken_by (above) is PRE-EXISTING, from 03_enum_types.sql —
-- reproduced verbatim, not introduced here.
--
-- Literal sets pinned by CHECK, declared with their tables in §7 and §8:
--   modality        tDCS | HD-tDCS | taVNS | TPS | rTMS | other
--   evidence_level  A | B | C
--   ear_side        left | right | bilateral
--   hemisphere      left | right | bilateral | midline
--   protocol status draft | active | completed | cancelled | superseded



-- ###########################################################################
-- §3  LAYER 1 — Tables: core schema (existing, 42 tables)
-- ###########################################################################


CREATE TABLE core."admins" (
    "admin_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "profile_id" UUID NOT NULL,
    "admin_type" TEXT NOT NULL,
    "region_id" UUID,
    "clinic_id" UUID,
    "force_password_change" BOOLEAN NOT NULL DEFAULT false,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."anamnesis_assessments" (
    "anamnesis_id" TEXT NOT NULL,
    "patient_id" UUID NOT NULL,
    "submitted_by" UUID,
    "taken_by" core.assessment_taken_by NOT NULL DEFAULT 'patient'::core.assessment_taken_by,
    "cycle_id" UUID,
    "version" INTEGER NOT NULL DEFAULT 1,
    "status" TEXT NOT NULL DEFAULT 'in_progress'::text,
    "completed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."anamnesis_responses" (
    "response_id" TEXT NOT NULL,
    "anamnesis_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "response_value" TEXT,
    "response_values" text[],
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."appointment_audit_logs" (
    "audit_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "appointment_id" UUID NOT NULL,
    "changed_by" UUID,
    "changed_by_role" TEXT,
    "previous_status" TEXT,
    "new_status" TEXT NOT NULL,
    "previous_date" DATE,
    "new_date" DATE,
    "previous_time" TIME,
    "new_time" TIME,
    "change_reason" TEXT,
    "changed_at" TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE ("changed_at");
-- Partitioned by monthly range on changed_at. Initial partitions below;
-- ongoing partition creation ahead of the current date is an operational job (Layer 7),
-- not a one-time setup step — see ops/PARTITION_MAINTENANCE.md.
CREATE TABLE core."appointment_audit_logs_y2025m01" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
CREATE TABLE core."appointment_audit_logs_y2025m02" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
CREATE TABLE core."appointment_audit_logs_y2025m03" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
CREATE TABLE core."appointment_audit_logs_y2025m04" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
CREATE TABLE core."appointment_audit_logs_y2025m05" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
CREATE TABLE core."appointment_audit_logs_y2025m06" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE core."appointment_audit_logs_y2025m07" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE core."appointment_audit_logs_y2025m08" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE core."appointment_audit_logs_y2025m09" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE core."appointment_audit_logs_y2025m10" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE core."appointment_audit_logs_y2025m11" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE core."appointment_audit_logs_y2025m12" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
CREATE TABLE core."appointment_audit_logs_y2026m01" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE core."appointment_audit_logs_y2026m02" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE core."appointment_audit_logs_y2026m03" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE core."appointment_audit_logs_y2026m04" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE core."appointment_audit_logs_y2026m05" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE core."appointment_audit_logs_y2026m06" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE core."appointment_audit_logs_y2026m07" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE core."appointment_audit_logs_y2026m08" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE core."appointment_audit_logs_y2026m09" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE core."appointment_audit_logs_y2026m10" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE core."appointment_audit_logs_y2026m11" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE core."appointment_audit_logs_y2026m12" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE core."appointment_audit_logs_y2027m01" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');
CREATE TABLE core."appointment_audit_logs_y2027m02" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');
CREATE TABLE core."appointment_audit_logs_y2027m03" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');
CREATE TABLE core."appointment_audit_logs_y2027m04" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-04-01') TO ('2027-05-01');
CREATE TABLE core."appointment_audit_logs_y2027m05" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-05-01') TO ('2027-06-01');
CREATE TABLE core."appointment_audit_logs_y2027m06" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-06-01') TO ('2027-07-01');
CREATE TABLE core."appointment_audit_logs_y2027m07" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-07-01') TO ('2027-08-01');
CREATE TABLE core."appointment_audit_logs_y2027m08" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-08-01') TO ('2027-09-01');
CREATE TABLE core."appointment_audit_logs_y2027m09" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-09-01') TO ('2027-10-01');
CREATE TABLE core."appointment_audit_logs_y2027m10" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-10-01') TO ('2027-11-01');
CREATE TABLE core."appointment_audit_logs_y2027m11" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-11-01') TO ('2027-12-01');
CREATE TABLE core."appointment_audit_logs_y2027m12" PARTITION OF core."appointment_audit_logs"
    FOR VALUES FROM ('2027-12-01') TO ('2028-01-01');
CREATE TABLE core."appointment_audit_logs_default" PARTITION OF core."appointment_audit_logs" DEFAULT;


CREATE TABLE core."appointment_requests" (
    "request_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID,
    "cycle_id" UUID,
    "request_type" TEXT NOT NULL DEFAULT 'new'::text,
    "parent_appointment_id" UUID,
    "preferred_date_1" DATE NOT NULL,
    "preferred_date_2" DATE,
    "preferred_date_3" DATE,
    "preferred_time_window" TEXT NOT NULL DEFAULT 'any'::text,
    "patient_complaint" TEXT,
    "reason" TEXT,
    "urgency" TEXT NOT NULL DEFAULT 'normal'::text,
    "status" TEXT NOT NULL DEFAULT 'pending'::text,
    "approved_appointment_id" UUID,
    "submitted_by" UUID NOT NULL,
    "reviewed_by" UUID,
    "review_notes" TEXT,
    "expires_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."appointments" (
    "appointment_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "ca_id" UUID,
    "session_id" UUID,
    "cycle_id" UUID,
    "appointment_request_id" UUID,
    "appointment_date" DATE NOT NULL,
    "start_time" TIME NOT NULL,
    "end_time" TIME NOT NULL,
    "slot_duration_minutes" INTEGER NOT NULL DEFAULT 30,
    "appointment_type" TEXT NOT NULL DEFAULT 'initial_assessment'::text,
    "session_phase" TEXT,
    "status" TEXT NOT NULL DEFAULT 'scheduled'::text,
    "reason" TEXT,
    "patient_complaint" TEXT,
    "notes" TEXT,
    "cancellation_reason" TEXT,
    "booked_by" UUID NOT NULL,
    "booked_by_role" TEXT NOT NULL,
    "cancelled_by" UUID,
    "rescheduled_from" UUID,
    "rescheduled_to" UUID,
    "checked_in_at" TIMESTAMPTZ,
    "started_at" TIMESTAMPTZ,
    "completed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."assessment_protocol_requests" (
    "request_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "clinical_assistant_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "clinic_id" UUID,
    "cycle_id" UUID,
    "protocol_details" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "status" TEXT NOT NULL DEFAULT 'pending'::text,
    "doctor_notes" TEXT,
    "submitted_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "reviewed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."ca_doctor_assignments" (
    "cda_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "ca_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "is_primary" BOOLEAN NOT NULL DEFAULT false,
    "assigned_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "removed_at" TIMESTAMPTZ
);


CREATE TABLE core."clinic_requests" (
    "request_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "request_type" TEXT NOT NULL,
    "clinic_type" TEXT,
    "clinic_id" UUID,
    "region_id" UUID NOT NULL,
    "submitted_by" UUID NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'pending'::text,
    "payload" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "reviewed_by" UUID,
    "review_notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."clinic_staff_assignments" (
    "assignment_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_id" UUID NOT NULL,
    "profile_id" UUID NOT NULL,
    "staff_role" TEXT NOT NULL,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "joined_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "removed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."clinical_assistants" (
    "ca_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "profile_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "qualification" TEXT,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."clinics" (
    "clinic_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_code" TEXT NOT NULL,
    "clinic_name" TEXT NOT NULL,
    "clinic_type" TEXT NOT NULL,
    "owner_name" TEXT NOT NULL DEFAULT 'Anava'::text,
    "status" TEXT NOT NULL DEFAULT 'setup'::text,
    "region_id" UUID NOT NULL,
    "clinic_admin_id" UUID,
    "is_main_branch" BOOLEAN NOT NULL DEFAULT false,
    "timezone" TEXT NOT NULL DEFAULT 'Asia/Kolkata'::text,
    "address" TEXT,
    "city" TEXT,
    "state" TEXT,
    "country" TEXT NOT NULL DEFAULT 'India'::text,
    "phone" TEXT,
    "email" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."device_assignments" (
    "da_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "plan_id" UUID NOT NULL,
    "assigned_by" UUID NOT NULL,
    "device_type" TEXT NOT NULL,
    "purchase_status" TEXT NOT NULL DEFAULT 'purchase_prompted'::text,
    "order_id" UUID,
    "prompted_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "purchased_at" TIMESTAMPTZ,
    "collected_at" TIMESTAMPTZ,
    "returned_at" TIMESTAMPTZ,
    "returned_by" UUID,
    "return_reason" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."doctor_patient_assignments" (
    "assignment_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "doctor_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'active'::text,
    "assigned_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "ended_at" TIMESTAMPTZ
);


CREATE TABLE core."doctor_schedule_overrides" (
    "override_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "doctor_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "override_date" DATE NOT NULL,
    "is_available" BOOLEAN NOT NULL DEFAULT false,
    "start_time" TIME,
    "end_time" TIME,
    "slot_duration_minutes" INTEGER,
    "reason" TEXT,
    "created_by" UUID NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."doctor_session_notes" (
    "note_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "session_id" UUID NOT NULL,
    "cycle_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "session_number" INTEGER NOT NULL,
    "session_phase" TEXT NOT NULL,
    "chief_complaint" TEXT,
    "clinical_observations" TEXT,
    "assessment" TEXT,
    "treatment_plan_notes" TEXT,
    "follow_up_instructions" TEXT,
    "referrals" TEXT,
    "note_content" TEXT,
    "is_confidential" BOOLEAN NOT NULL DEFAULT false,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."doctor_weekly_schedules" (
    "schedule_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "doctor_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "day_of_week" SMALLINT NOT NULL,
    "start_time" TIME NOT NULL,
    "end_time" TIME NOT NULL,
    "slot_duration_minutes" INTEGER NOT NULL DEFAULT 30,
    "break_start" TIME,
    "break_end" TIME,
    "max_appointments" INTEGER,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "effective_from" DATE,
    "effective_until" DATE,
    "created_by" UUID,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."doctors" (
    "doctor_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "profile_id" UUID NOT NULL,
    "specialization" TEXT,
    "license_number" TEXT,
    "hospital_affiliation" TEXT,
    "max_patient_count" INTEGER NOT NULL DEFAULT 30,
    "availability_status" TEXT NOT NULL DEFAULT 'available'::text,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "clinic_id" UUID
);


CREATE TABLE core."inventory" (
    "inventory_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "product_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "quantity" INTEGER NOT NULL DEFAULT 0,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."notifications" (
    "notification_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "recipient_id" UUID NOT NULL,
    "sender_id" UUID,
    "clinic_id" UUID,
    "type" TEXT NOT NULL DEFAULT 'system'::text,
    "delivery_channel" TEXT NOT NULL DEFAULT 'in_app'::text,
    "title" TEXT NOT NULL,
    "body" TEXT,
    "entity_type" TEXT,
    "entity_id" UUID,
    "metadata" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "is_read" BOOLEAN NOT NULL DEFAULT false,
    "read_at" TIMESTAMPTZ,
    "delivered_at" TIMESTAMPTZ,
    "delivery_attempts" INTEGER NOT NULL DEFAULT 0,
    "expires_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE ("created_at");
-- Partitioned by monthly range on created_at. Initial partitions below;
-- ongoing partition creation ahead of the current date is an operational job (Layer 7),
-- not a one-time setup step — see ops/PARTITION_MAINTENANCE.md.
CREATE TABLE core."notifications_y2025m01" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
CREATE TABLE core."notifications_y2025m02" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
CREATE TABLE core."notifications_y2025m03" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
CREATE TABLE core."notifications_y2025m04" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
CREATE TABLE core."notifications_y2025m05" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
CREATE TABLE core."notifications_y2025m06" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE core."notifications_y2025m07" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE core."notifications_y2025m08" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE core."notifications_y2025m09" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE core."notifications_y2025m10" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE core."notifications_y2025m11" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE core."notifications_y2025m12" PARTITION OF core."notifications"
    FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
CREATE TABLE core."notifications_y2026m01" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE core."notifications_y2026m02" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE core."notifications_y2026m03" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE core."notifications_y2026m04" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE core."notifications_y2026m05" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE core."notifications_y2026m06" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE core."notifications_y2026m07" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE core."notifications_y2026m08" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE core."notifications_y2026m09" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE core."notifications_y2026m10" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE core."notifications_y2026m11" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE core."notifications_y2026m12" PARTITION OF core."notifications"
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE core."notifications_y2027m01" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');
CREATE TABLE core."notifications_y2027m02" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');
CREATE TABLE core."notifications_y2027m03" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');
CREATE TABLE core."notifications_y2027m04" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-04-01') TO ('2027-05-01');
CREATE TABLE core."notifications_y2027m05" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-05-01') TO ('2027-06-01');
CREATE TABLE core."notifications_y2027m06" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-06-01') TO ('2027-07-01');
CREATE TABLE core."notifications_y2027m07" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-07-01') TO ('2027-08-01');
CREATE TABLE core."notifications_y2027m08" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-08-01') TO ('2027-09-01');
CREATE TABLE core."notifications_y2027m09" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-09-01') TO ('2027-10-01');
CREATE TABLE core."notifications_y2027m10" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-10-01') TO ('2027-11-01');
CREATE TABLE core."notifications_y2027m11" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-11-01') TO ('2027-12-01');
CREATE TABLE core."notifications_y2027m12" PARTITION OF core."notifications"
    FOR VALUES FROM ('2027-12-01') TO ('2028-01-01');
CREATE TABLE core."notifications_default" PARTITION OF core."notifications" DEFAULT;


CREATE TABLE core."order_items" (
    "item_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "order_id" UUID NOT NULL,
    "product_id" UUID NOT NULL,
    "quantity" INTEGER NOT NULL DEFAULT 1,
    "unit_price" NUMERIC(10,2) NOT NULL
);


CREATE TABLE core."patient_clinic_transfers" (
    "pct_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "from_clinic_id" UUID NOT NULL,
    "to_clinic_id" UUID NOT NULL,
    "from_doctor_id" UUID,
    "to_doctor_id" UUID,
    "transfer_reason" TEXT NOT NULL DEFAULT 'clinic_closure'::text,
    "active_cycle_id" UUID,
    "status" TEXT NOT NULL DEFAULT 'pending'::text,
    "consent_id" UUID,
    "initiated_by" UUID NOT NULL,
    "notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."patient_disease_selection" (
    "pds_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "disease_id" TEXT,
    "disease_unknown" BOOLEAN NOT NULL DEFAULT false,
    "is_primary" BOOLEAN NOT NULL DEFAULT false,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."patient_eeg_files" (
    "eeg_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "cycle_id" UUID,
    "session_id" UUID,
    "performed_by" UUID NOT NULL,
    "reviewed_by" UUID,
    "eeg_type" TEXT NOT NULL DEFAULT 'resting_state'::text,
    "duration_minutes" INTEGER,
    "raw_data_s3_key" TEXT,
    "raw_file_name" TEXT,
    "raw_file_size" BIGINT,
    "raw_checksum" TEXT,
    "raw_checksum_algorithm" TEXT NOT NULL DEFAULT 'sha256'::text,
    "report_s3_key" TEXT,
    "report_file_name" TEXT,
    "report_file_size" BIGINT,
    "report_checksum" TEXT,
    "report_checksum_algorithm" TEXT NOT NULL DEFAULT 'sha256'::text,
    "superseded_by" UUID,
    "recording_notes" TEXT,
    "clinical_findings" TEXT,
    "is_abnormal" BOOLEAN,
    "status" TEXT NOT NULL DEFAULT 'raw_uploaded'::text,
    "performed_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "reviewed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."patient_medical_history_files" (
    "mhf_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "cycle_id" UUID,
    "uploaded_by" UUID NOT NULL,
    "document_type" TEXT NOT NULL,
    "s3_key" TEXT NOT NULL,
    "file_name" TEXT NOT NULL,
    "file_size" BIGINT,
    "mime_type" TEXT,
    "checksum" TEXT,
    "checksum_algorithm" TEXT NOT NULL DEFAULT 'sha256'::text,
    "description" TEXT,
    "document_date" DATE,
    "source_provider" TEXT,
    "is_deleted" BOOLEAN NOT NULL DEFAULT false,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."patient_scale_assignments" (
    "psa_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "scale_id" TEXT NOT NULL,
    "assessment_stage" TEXT NOT NULL,
    "assigned_by" UUID NOT NULL,
    "assignment_reason" TEXT,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "deactivated_at" TIMESTAMPTZ,
    "deactivated_by" UUID,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "disease_id" TEXT
);


CREATE TABLE core."patients" (
    "patient_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "profile_id" UUID NOT NULL,
    "mrn" TEXT NOT NULL,
    "registration_status" TEXT NOT NULL DEFAULT 'demographics_complete'::text,
    "primary_clinic_id" UUID,
    "primary_doctor_id" UUID,
    "blood_group" TEXT,
    "allergies" TEXT,
    "occupation" TEXT,
    "marital_status" TEXT,
    "insurance_provider" TEXT,
    "insurance_policy" TEXT,
    "referred_by" TEXT,
    "emergency_contact_name" TEXT,
    "emergency_contact_phone" TEXT,
    "registration_completed_at" TIMESTAMPTZ,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "self_registered" BOOLEAN NOT NULL DEFAULT false,
    "approval_status" TEXT NOT NULL DEFAULT 'not_required'::text,
    "approved_by" UUID,
    "approved_at" TIMESTAMPTZ,
    "rejection_reason" TEXT
);


CREATE TABLE core."payments" (
    "payment_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "session_id" UUID,
    "order_id" UUID,
    "idempotency_key" TEXT NOT NULL,
    "razorpay_order_id" TEXT,
    "razorpay_payment_id" TEXT,
    "amount" NUMERIC(10,2) NOT NULL,
    "currency" TEXT NOT NULL DEFAULT 'INR'::text,
    "payment_method" TEXT,
    "status" TEXT NOT NULL DEFAULT 'pending'::text,
    "gateway_response" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "waived_by" UUID,
    "waived_reason" TEXT,
    "paid_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."profiles" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "cognito_sub" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "first_name" TEXT NOT NULL,
    "last_name" TEXT NOT NULL,
    "phone" TEXT,
    "role" TEXT NOT NULL,
    "gender" TEXT,
    "dob" DATE,
    "address" TEXT,
    "city" TEXT,
    "state" TEXT,
    "country" TEXT,
    "profile_photo_s3_key" TEXT,
    "pincode" TEXT,
    "language_pref" TEXT NOT NULL DEFAULT 'en'::text,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "consent_signed" BOOLEAN NOT NULL DEFAULT true,
    "email_verified" BOOLEAN NOT NULL DEFAULT false,
    "phone_verified" BOOLEAN NOT NULL DEFAULT false
);


CREATE TABLE core."prs_assessment_instances" (
    "instance_id" TEXT NOT NULL,
    "disease_id" TEXT NOT NULL,
    "patient_id" UUID NOT NULL,
    "session_id" UUID,
    "cycle_id" UUID,
    "initiated_by" core.assessment_taken_by NOT NULL DEFAULT 'patient'::core.assessment_taken_by,
    "administered_by" UUID,
    "assessment_stage" TEXT NOT NULL DEFAULT 'general_registration'::text,
    "status" TEXT NOT NULL DEFAULT 'in_progress'::text,
    "started_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "completed_at" TIMESTAMPTZ,
    "final_result" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "language_code" VARCHAR(10) NOT NULL DEFAULT 'en'::character varying
);


CREATE TABLE core."prs_final_results" (
    "final_result_id" TEXT NOT NULL,
    "instance_id" TEXT NOT NULL,
    "calculated_value" NUMERIC,
    "max_possible" NUMERIC,
    "percentage" NUMERIC,
    "scales_completed" INTEGER NOT NULL DEFAULT 0,
    "scales_total" INTEGER NOT NULL DEFAULT 0,
    "overall_severity" TEXT,
    "overall_severity_label" TEXT,
    "scale_summaries" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "all_risk_flags" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "composite_summary" TEXT,
    "time_stamp" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."prs_responses" (
    "response_id" TEXT NOT NULL,
    "instance_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "given_response" TEXT,
    "response_value" NUMERIC,
    "time_stamp" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "language_code" VARCHAR(10) NOT NULL DEFAULT 'en'::character varying
);


CREATE TABLE core."prs_scale_results" (
    "scale_result_id" TEXT NOT NULL,
    "instance_id" TEXT NOT NULL,
    "scale_id" TEXT NOT NULL,
    "calculated_value" NUMERIC,
    "max_possible" NUMERIC,
    "percentage" NUMERIC,
    "severity_level" TEXT,
    "severity_label" TEXT,
    "subscale_scores" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "risk_flags" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "raw_score_data" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "time_stamp" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."receptionists" (
    "receptionist_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "profile_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "deleted_by" UUID,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."regions" (
    "region_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "region_name" TEXT NOT NULL,
    "country" TEXT NOT NULL,
    "state" TEXT NOT NULL,
    "regional_admin_id" UUID,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."sessions" (
    "session_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID,
    "session_date" TIMESTAMPTZ NOT NULL,
    "session_type" TEXT NOT NULL DEFAULT 'in_person'::text,
    "notes" TEXT,
    "status" TEXT NOT NULL DEFAULT 'scheduled'::text,
    "cycle_id" UUID,
    "clinic_id" UUID,
    "ca_id" UUID,
    "session_phase" TEXT,
    "session_number_in_cycle" INTEGER,
    "outcome" TEXT,
    "started_at" TIMESTAMPTZ,
    "completed_at" TIMESTAMPTZ,
    "payment_status" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."staff_requests" (
    "request_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_id" UUID NOT NULL,
    "regional_admin_id" UUID,
    "request_type" TEXT NOT NULL,
    "position_role" TEXT NOT NULL,
    "candidate_name" TEXT,
    "candidate_email" TEXT,
    "candidate_phone" TEXT,
    "candidate_credentials" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "target_staff_id" UUID,
    "status" TEXT NOT NULL DEFAULT 'pending'::text,
    "submitted_by" UUID NOT NULL,
    "reviewed_by" UUID,
    "review_notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "fulfilled_profile_id" UUID,
    "fulfilled_at" TIMESTAMPTZ
);


CREATE TABLE core."stock_transfers" (
    "st_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "product_id" UUID NOT NULL,
    "from_type" TEXT NOT NULL,
    "from_clinic_id" UUID,
    "to_clinic_id" UUID NOT NULL,
    "quantity" INTEGER NOT NULL,
    "order_id" UUID,
    "status" TEXT NOT NULL DEFAULT 'pending'::text,
    "initiated_by" UUID NOT NULL,
    "received_by" UUID,
    "notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "dispatched_at" TIMESTAMPTZ,
    "received_at" TIMESTAMPTZ
);


CREATE TABLE core."store_orders" (
    "order_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "clinic_id" UUID NOT NULL,
    "initiated_by" UUID NOT NULL,
    "approved_by" UUID,
    "order_type" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'pending_doctor_approval'::text,
    "total_amount" NUMERIC(10,2),
    "treatment_plan_id" UUID,
    "cancelled_by" UUID,
    "cancelled_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."treatment_cycles" (
    "cycle_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "ca_id" UUID,
    "clinic_id" UUID NOT NULL,
    "cycle_type" TEXT NOT NULL,
    "cycle_number" INTEGER NOT NULL DEFAULT 1,
    "scheduled_date" DATE,
    "status" TEXT NOT NULL DEFAULT 'in_progress'::text,
    "notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."treatment_plans" (
    "plan_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "doctor_id" UUID NOT NULL,
    "cycle_id" UUID NOT NULL,
    "device_type" TEXT NOT NULL,
    "protocol_details" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "sessions_prescribed" INTEGER NOT NULL,
    "standard_sessions" INTEGER NOT NULL DEFAULT 5,
    "extended_sessions" INTEGER,
    "status" TEXT NOT NULL DEFAULT 'active'::text,
    "parent_plan_id" UUID,
    "demo_phase_status" TEXT NOT NULL DEFAULT 'pending'::text,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE core."treatment_sessions" (
    "ts_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "plan_id" UUID NOT NULL,
    "session_id" UUID NOT NULL,
    "patient_id" UUID NOT NULL,
    "ca_id" UUID NOT NULL,
    "session_number" INTEGER NOT NULL,
    "billing_type" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'scheduled'::text,
    "payment_status" TEXT NOT NULL DEFAULT 'pending'::text,
    "session_notes" TEXT,
    "patient_feedback" TEXT,
    "started_at" TIMESTAMPTZ,
    "completed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE ("created_at");
-- Partitioned by yearly range on created_at. Initial partitions below;
-- ongoing partition creation ahead of the current date is an operational job (Layer 7),
-- not a one-time setup step — see ops/PARTITION_MAINTENANCE.md.
CREATE TABLE core."treatment_sessions_y2024" PARTITION OF core."treatment_sessions"
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE core."treatment_sessions_y2025" PARTITION OF core."treatment_sessions"
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE core."treatment_sessions_y2026" PARTITION OF core."treatment_sessions"
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE core."treatment_sessions_y2027" PARTITION OF core."treatment_sessions"
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
CREATE TABLE core."treatment_sessions_y2028" PARTITION OF core."treatment_sessions"
    FOR VALUES FROM ('2028-01-01') TO ('2029-01-01');
CREATE TABLE core."treatment_sessions_default" PARTITION OF core."treatment_sessions" DEFAULT;



-- ###########################################################################
-- §4  LAYER 1 — Tables: reference schema (existing, 13 tables)
-- ###########################################################################


CREATE TABLE reference."anamnesis_options" (
    "option_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "option_label" TEXT NOT NULL,
    "option_value" TEXT NOT NULL,
    "display_order" INTEGER NOT NULL DEFAULT 0
);


CREATE TABLE reference."anamnesis_questions" (
    "question_id" TEXT NOT NULL,
    "section_number" INTEGER NOT NULL,
    "section_title" TEXT NOT NULL,
    "question_code" TEXT NOT NULL,
    "question_text" TEXT NOT NULL,
    "answer_type" TEXT NOT NULL,
    "is_required" BOOLEAN NOT NULL DEFAULT true,
    "display_order" INTEGER NOT NULL DEFAULT 0,
    "depends_on_question_id" TEXT,
    "depends_on_value" TEXT,
    "helper_text" TEXT,
    "status" BOOLEAN NOT NULL DEFAULT true
);


CREATE TABLE reference."consent_templates" (
    "template_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "consent_type" TEXT NOT NULL,
    "version" INTEGER NOT NULL DEFAULT 1,
    "title" TEXT NOT NULL,
    "content" TEXT NOT NULL,
    "content_hash" TEXT,
    "effective_date" DATE,
    "expiry_date" DATE,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "role" TEXT
);


CREATE TABLE reference."products" (
    "product_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "name" TEXT NOT NULL,
    "description" TEXT,
    "category" TEXT NOT NULL,
    "price" NUMERIC(10,2) NOT NULL,
    "sku" TEXT,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_disease_question_map" (
    "dq_map_id" TEXT NOT NULL,
    "disease_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "display_order" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_disease_scale_map" (
    "ds_map_id" TEXT NOT NULL,
    "disease_id" TEXT NOT NULL,
    "scale_id" TEXT NOT NULL,
    "display_order" INTEGER NOT NULL DEFAULT 0,
    "is_required" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_diseases" (
    "disease_id" TEXT NOT NULL,
    "disease_code" TEXT NOT NULL,
    "disease_name" TEXT NOT NULL,
    "version" TEXT NOT NULL DEFAULT 'v1.0'::text,
    "status" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_option_translations" (
    "option_id" TEXT NOT NULL,
    "language_code" VARCHAR(10) NOT NULL,
    "option_label" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_options" (
    "option_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "option_label" TEXT NOT NULL,
    "option_value" TEXT NOT NULL,
    "points" NUMERIC NOT NULL DEFAULT 0,
    "display_order" INTEGER NOT NULL DEFAULT 0,
    "status" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_question_translations" (
    "question_id" TEXT NOT NULL,
    "language_code" VARCHAR(10) NOT NULL,
    "question_text" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_questions" (
    "question_id" TEXT NOT NULL,
    "question_code" TEXT NOT NULL,
    "disease_id" TEXT,
    "scale_id" TEXT,
    "ds_map_id" TEXT,
    "question_text" TEXT NOT NULL,
    "answer_type" TEXT NOT NULL,
    "min_value" NUMERIC,
    "max_value" NUMERIC,
    "is_required" BOOLEAN NOT NULL DEFAULT true,
    "skip_logic" TEXT,
    "display_order" INTEGER NOT NULL DEFAULT 0,
    "is_common_scale" BOOLEAN NOT NULL DEFAULT false,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_scale_question_map" (
    "sq_map_id" TEXT NOT NULL,
    "scale_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "display_order" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_scales" (
    "scale_id" TEXT NOT NULL,
    "scale_code" TEXT NOT NULL,
    "scale_name" TEXT NOT NULL,
    "is_common_scale" BOOLEAN NOT NULL DEFAULT false,
    "num_diseases_used" INTEGER NOT NULL DEFAULT 1,
    "applicable_for" TEXT NOT NULL DEFAULT 'main_clinical'::text,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);



-- ###########################################################################
-- §5  LAYER 1 — Tables: compliance schema (existing, 3 tables)
-- ###########################################################################


CREATE TABLE compliance."activity_logs" (
    "log_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "actor_id" UUID NOT NULL,
    "actor_role" TEXT NOT NULL,
    "request_id" TEXT,
    "category" TEXT NOT NULL,
    "event_type" TEXT NOT NULL,
    "entity_type" TEXT,
    "entity_id" UUID,
    "clinic_id" UUID,
    "region_id" UUID,
    "metadata" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "ip_address" INET,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE ("created_at");
-- Partitioned by monthly range on created_at. Initial partitions below;
-- ongoing partition creation ahead of the current date is an operational job (Layer 7),
-- not a one-time setup step — see ops/PARTITION_MAINTENANCE.md.
CREATE TABLE compliance."activity_logs_y2025m01" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
CREATE TABLE compliance."activity_logs_y2025m02" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
CREATE TABLE compliance."activity_logs_y2025m03" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
CREATE TABLE compliance."activity_logs_y2025m04" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
CREATE TABLE compliance."activity_logs_y2025m05" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
CREATE TABLE compliance."activity_logs_y2025m06" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE compliance."activity_logs_y2025m07" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE compliance."activity_logs_y2025m08" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE compliance."activity_logs_y2025m09" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE compliance."activity_logs_y2025m10" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE compliance."activity_logs_y2025m11" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE compliance."activity_logs_y2025m12" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
CREATE TABLE compliance."activity_logs_y2026m01" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE compliance."activity_logs_y2026m02" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE compliance."activity_logs_y2026m03" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE compliance."activity_logs_y2026m04" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE compliance."activity_logs_y2026m05" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE compliance."activity_logs_y2026m06" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE compliance."activity_logs_y2026m07" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE compliance."activity_logs_y2026m08" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE compliance."activity_logs_y2026m09" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE compliance."activity_logs_y2026m10" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE compliance."activity_logs_y2026m11" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE compliance."activity_logs_y2026m12" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE compliance."activity_logs_y2027m01" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');
CREATE TABLE compliance."activity_logs_y2027m02" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');
CREATE TABLE compliance."activity_logs_y2027m03" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');
CREATE TABLE compliance."activity_logs_y2027m04" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-04-01') TO ('2027-05-01');
CREATE TABLE compliance."activity_logs_y2027m05" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-05-01') TO ('2027-06-01');
CREATE TABLE compliance."activity_logs_y2027m06" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-06-01') TO ('2027-07-01');
CREATE TABLE compliance."activity_logs_y2027m07" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-07-01') TO ('2027-08-01');
CREATE TABLE compliance."activity_logs_y2027m08" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-08-01') TO ('2027-09-01');
CREATE TABLE compliance."activity_logs_y2027m09" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-09-01') TO ('2027-10-01');
CREATE TABLE compliance."activity_logs_y2027m10" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-10-01') TO ('2027-11-01');
CREATE TABLE compliance."activity_logs_y2027m11" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-11-01') TO ('2027-12-01');
CREATE TABLE compliance."activity_logs_y2027m12" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-12-01') TO ('2028-01-01');
CREATE TABLE compliance."activity_logs_default" PARTITION OF compliance."activity_logs" DEFAULT;


CREATE TABLE compliance."audit_logs" (
    "log_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "table_name" TEXT NOT NULL,
    "operation" TEXT NOT NULL,
    "record_id" TEXT,
    "old_data" JSONB,
    "new_data" JSONB,
    "changed_by" UUID,
    "clinic_id" UUID,
    "ip_address" INET,
    "request_id" TEXT,
    "changed_at" TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE ("changed_at");
-- Partitioned by monthly range on changed_at. Initial partitions below;
-- ongoing partition creation ahead of the current date is an operational job (Layer 7),
-- not a one-time setup step — see ops/PARTITION_MAINTENANCE.md.
CREATE TABLE compliance."audit_logs_y2025m01" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
CREATE TABLE compliance."audit_logs_y2025m02" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
CREATE TABLE compliance."audit_logs_y2025m03" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
CREATE TABLE compliance."audit_logs_y2025m04" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
CREATE TABLE compliance."audit_logs_y2025m05" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
CREATE TABLE compliance."audit_logs_y2025m06" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE compliance."audit_logs_y2025m07" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE compliance."audit_logs_y2025m08" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE compliance."audit_logs_y2025m09" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE compliance."audit_logs_y2025m10" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE compliance."audit_logs_y2025m11" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE compliance."audit_logs_y2025m12" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
CREATE TABLE compliance."audit_logs_y2026m01" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE compliance."audit_logs_y2026m02" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE compliance."audit_logs_y2026m03" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE compliance."audit_logs_y2026m04" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE compliance."audit_logs_y2026m05" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE compliance."audit_logs_y2026m06" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE compliance."audit_logs_y2026m07" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE compliance."audit_logs_y2026m08" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE compliance."audit_logs_y2026m09" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE compliance."audit_logs_y2026m10" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE compliance."audit_logs_y2026m11" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE compliance."audit_logs_y2026m12" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE compliance."audit_logs_y2027m01" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');
CREATE TABLE compliance."audit_logs_y2027m02" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');
CREATE TABLE compliance."audit_logs_y2027m03" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');
CREATE TABLE compliance."audit_logs_y2027m04" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-04-01') TO ('2027-05-01');
CREATE TABLE compliance."audit_logs_y2027m05" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-05-01') TO ('2027-06-01');
CREATE TABLE compliance."audit_logs_y2027m06" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-06-01') TO ('2027-07-01');
CREATE TABLE compliance."audit_logs_y2027m07" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-07-01') TO ('2027-08-01');
CREATE TABLE compliance."audit_logs_y2027m08" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-08-01') TO ('2027-09-01');
CREATE TABLE compliance."audit_logs_y2027m09" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-09-01') TO ('2027-10-01');
CREATE TABLE compliance."audit_logs_y2027m10" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-10-01') TO ('2027-11-01');
CREATE TABLE compliance."audit_logs_y2027m11" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-11-01') TO ('2027-12-01');
CREATE TABLE compliance."audit_logs_y2027m12" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-12-01') TO ('2028-01-01');
CREATE TABLE compliance."audit_logs_default" PARTITION OF compliance."audit_logs" DEFAULT;


CREATE TABLE compliance."consent_records" (
    "consent_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "consent_type" TEXT NOT NULL,
    "template_id" UUID NOT NULL,
    "patient_id" UUID,
    "staff_id" UUID,
    "clinic_id" UUID,
    "status" TEXT NOT NULL DEFAULT 'pending'::text,
    "signed_at" TIMESTAMPTZ,
    "signed_by" UUID,
    "witness_id" UUID,
    "ip_address" INET,
    "signature_data" TEXT,
    "pdf_s3_key" TEXT,
    "content_hash_at_signing" TEXT,
    "revoked_at" TIMESTAMPTZ,
    "revoked_by" UUID,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "region_id" UUID
);



-- ###########################################################################
-- §6  LAYER 1 — Tables: ops schema (existing, 3 tables)
-- ###########################################################################


CREATE TABLE ops."alembic_version" (
    "version_num" VARCHAR(32) NOT NULL
);


CREATE TABLE ops."outbox_events" (
    "outbox_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "aggregate_type" TEXT NOT NULL,
    "aggregate_id" TEXT NOT NULL,
    "event_type" TEXT NOT NULL,
    "payload" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "published_at" TIMESTAMPTZ,
    "publish_attempts" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE ops."schema_migrations" (
    "version" TEXT NOT NULL,
    "applied_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);



-- ###########################################################################
-- §7  LAYER 1 — Tables: neuromodulation catalogue (new, reference schema)
-- ###########################################################################


CREATE TABLE reference."neuromod_devices" (
    "device_id"       UUID NOT NULL DEFAULT gen_random_uuid(),
    "device_code"     TEXT NOT NULL,
    "device_name"     TEXT NOT NULL,
    "modality"        TEXT NOT NULL,
    "phase"           SMALLINT NOT NULL DEFAULT 2,
    "is_active"       BOOLEAN NOT NULL DEFAULT true,
    "created_at"      TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_neuromod_devices_phase" CHECK ("phase" IN (1, 2)),
    -- v2 Layer 2: text + CHECK, not a native enum. Literal set pinned here.
    CONSTRAINT "chk_neuromod_devices_modality" CHECK ("modality" IN
        ('tDCS', 'HD-tDCS', 'taVNS', 'TPS', 'rTMS', 'other'))
);
COMMENT ON TABLE reference."neuromod_devices" IS 'Device registry. One row per stimulation modality the platform can prescribe.';
COMMENT ON COLUMN reference."neuromod_devices"."phase" IS 'Rollout gate: 1 = selectable now (tDCS, HD-tDCS), 2 = catalogued but not yet enabled in the UI. Switching a device on is an UPDATE, not a migration.';

CREATE TABLE reference."neuromod_conditions" (
    "condition_id"    UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_name"  TEXT NOT NULL,
    "display_order"   INTEGER NOT NULL DEFAULT 0,
    "is_active"       BOOLEAN NOT NULL DEFAULT true,
    "created_at"      TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"      TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE reference."neuromod_conditions" IS 'Top-level treatable conditions — Depression, Anxiety Disorders, Chronic Pain, ADHD, ASD.';

CREATE TABLE reference."neuromod_diagnoses" (
    "diagnosis_id"       UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"       UUID NOT NULL,
    "icd10_code"         TEXT NOT NULL,
    "icd10_description"  TEXT NOT NULL,
    "created_at"         TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE reference."neuromod_diagnoses" IS 'ICD-10 lookup. A doctor picks a diagnosis to narrow the placement/dosing options; the protocol row itself stores only the RESOLVED placement and dosing, not the diagnosis — hence no FK from treatment_protocols to here.';

CREATE TABLE reference."neuromod_scales" (
    "scale_id"     UUID NOT NULL DEFAULT gen_random_uuid(),
    "scale_code"   TEXT NOT NULL,
    "scale_name"   TEXT NOT NULL,
    "prs_scale_id" TEXT,
    "created_at"   TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE reference."neuromod_scales" IS 'Clinical rating scales recommended per condition (PHQ-9, GAD-7, NPRS, ...).';
COMMENT ON COLUMN reference."neuromod_scales"."prs_scale_id" IS 'Optional bridge to reference.prs_scales when the same instrument is also administered through the PRS questionnaire engine. Nullable: not every recommended scale is built as a PRS scale.';

CREATE TABLE reference."neuromod_condition_scales" (
    "cs_map_id"      UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"   UUID NOT NULL,
    "scale_id"       UUID NOT NULL,
    "display_order"  INTEGER NOT NULL DEFAULT 0,
    "created_at"     TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE reference."neuromod_condition_scales" IS 'Many-to-many: which scales are recommended for which condition. HADS, for example, appears under both Depression and Anxiety.';


CREATE TABLE reference."tdcs_placements" (
    "tdcs_placement_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"       UUID NOT NULL,
    "device_id"          UUID NOT NULL,
    "montage_label"      TEXT NOT NULL,
    "anode_site"         TEXT,
    "cathode_site"       TEXT,
    "is_active"          BOOLEAN NOT NULL DEFAULT true,
    "created_at"         TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Electrode rule, declaratively enforced: tDCS = exactly 1 anode + 1
    -- cathode. Both sites are either specified together or both left NULL
    -- ("Not specified" in the source sheet for Chronic Pain / ADHD / ASD —
    -- carried through as-is rather than invented).
    CONSTRAINT "chk_tdcs_placements_electrode_rule" CHECK (
        ("anode_site" IS NOT NULL AND "cathode_site" IS NOT NULL)
        OR ("anode_site" IS NULL AND "cathode_site" IS NULL)
    )
);
COMMENT ON TABLE reference."tdcs_placements" IS 'Conventional tDCS montage: exactly 1 anode + 1 cathode, 10-20 EEG sites.';

CREATE TABLE reference."hd_tdcs_placements" (
    "hd_tdcs_placement_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"          UUID NOT NULL,
    "device_id"             UUID NOT NULL,
    "montage_label"         TEXT NOT NULL,
    "anode_site"            TEXT,
    "return_sites"          TEXT[] NOT NULL DEFAULT '{}',
    "is_active"             BOOLEAN NOT NULL DEFAULT true,
    "created_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Electrode rule: HD-tDCS = 1 anode + up to 4 return cathodes (4x1 ring).
    -- Splitting per device is what lets this stay a plain CHECK instead of a
    -- trigger — the rule is a property of the table, not looked up elsewhere.
    CONSTRAINT "chk_hd_tdcs_placements_electrode_rule" CHECK (
        ("anode_site" IS NOT NULL AND array_length("return_sites", 1) BETWEEN 1 AND 4)
        OR ("anode_site" IS NULL AND "return_sites" = '{}')
    )
);
COMMENT ON TABLE reference."hd_tdcs_placements" IS 'HD-tDCS 4x1 ring montage: 1 anode + up to 4 return cathodes, 10-20 EEG sites.';

CREATE TABLE reference."tavns_placements" (
    "tavns_placement_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"        UUID NOT NULL,
    "device_id"           UUID NOT NULL,
    "montage_label"       TEXT NOT NULL,
    "ear_side"            TEXT,
    "auricular_site"      TEXT,
    "is_active"           BOOLEAN NOT NULL DEFAULT true,
    "created_at"          TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_tavns_placements_ear_side" CHECK ("ear_side" IS NULL OR "ear_side" IN
        ('left', 'right', 'bilateral'))
);
COMMENT ON TABLE reference."tavns_placements" IS 'taVNS auricular placement. Sites are ear landmarks (cymba conchae, tragus, earlobe), not 10-20 scalp positions.';

CREATE TABLE reference."tps_placements" (
    "tps_placement_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"      UUID NOT NULL,
    "device_id"         UUID NOT NULL,
    "montage_label"     TEXT NOT NULL,
    "target_region"     TEXT,
    "hemisphere"        TEXT,
    "is_active"         BOOLEAN NOT NULL DEFAULT true,
    "created_at"        TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_tps_placements_hemisphere" CHECK ("hemisphere" IS NULL OR "hemisphere" IN
        ('left', 'right', 'bilateral', 'midline'))
);
COMMENT ON TABLE reference."tps_placements" IS 'TPS transducer target. No electrodes at all — target_region is an anatomical region, not an electrode site.';

CREATE TABLE reference."rtms_placements" (
    "rtms_placement_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"       UUID NOT NULL,
    "device_id"          UUID NOT NULL,
    "montage_label"      TEXT NOT NULL,
    "coil_target"        TEXT,
    "coil_type"          TEXT,
    "hemisphere"         TEXT,
    "is_active"          BOOLEAN NOT NULL DEFAULT true,
    "created_at"         TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_rtms_placements_hemisphere" CHECK ("hemisphere" IS NULL OR "hemisphere" IN
        ('left', 'right', 'bilateral', 'midline'))
);
COMMENT ON TABLE reference."rtms_placements" IS 'rTMS coil position. coil_target may be a 10-20 site (F3) or an anatomical target; coil_type is figure-8, H-coil, etc.';

CREATE TABLE reference."other_placements" (
    "other_placement_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"        UUID NOT NULL,
    "device_id"           UUID NOT NULL,
    "montage_label"       TEXT NOT NULL,
    "placement_details"   JSONB NOT NULL DEFAULT '{}'::jsonb,
    "is_active"           BOOLEAN NOT NULL DEFAULT true,
    "created_at"          TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"          TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE reference."other_placements" IS 'Escape hatch for a device not yet modelled. jsonb here is deliberate — an unmodelled device has no known column set to type.';


CREATE TABLE reference."tdcs_dosing" (
    "tdcs_dosing_id"        UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"          UUID NOT NULL,
    "device_id"             UUID NOT NULL,
    "tdcs_placement_id"     UUID NOT NULL,
    "evidence_level"        TEXT NOT NULL,
    "current_ma_min"        NUMERIC(3,1),
    "current_ma_max"        NUMERIC(3,1),
    "session_duration_min"  INTEGER,
    "sessions_per_day"      INTEGER,
    "num_sessions_text"     TEXT,
    "notes"                 TEXT,
    "is_active"             BOOLEAN NOT NULL DEFAULT true,
    "created_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_tdcs_dosing_current_range" CHECK (
        "current_ma_min" IS NULL OR "current_ma_max" IS NULL
        OR "current_ma_min" <= "current_ma_max"
    ),
    CONSTRAINT "chk_tdcs_dosing_evidence_level" CHECK ("evidence_level" IN ('A', 'B', 'C'))
);
COMMENT ON COLUMN reference."tdcs_dosing"."num_sessions_text" IS 'Free text because the source sheet expresses this as a protocol phrase ("1 per day x 10 days (20-30 days attempted)"), not a single number. The actual prescribed count is per-patient and lives on treatment_protocols.session_count.';

CREATE TABLE reference."hd_tdcs_dosing" (
    "hd_tdcs_dosing_id"      UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"           UUID NOT NULL,
    "device_id"              UUID NOT NULL,
    "hd_tdcs_placement_id"   UUID NOT NULL,
    "evidence_level"         TEXT NOT NULL,
    "total_current_ma"       NUMERIC(3,1),
    "per_return_current_ma"  NUMERIC(3,1),
    "session_duration_min"   INTEGER,
    "sessions_per_day"       INTEGER,
    "num_sessions_text"      TEXT,
    "notes"                  TEXT,
    "is_active"              BOOLEAN NOT NULL DEFAULT true,
    "created_at"             TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_hd_tdcs_dosing_evidence_level" CHECK ("evidence_level" IN ('A', 'B', 'C'))
);
COMMENT ON COLUMN reference."hd_tdcs_dosing"."per_return_current_ma" IS 'Current at each return electrode. Distinct from total_current_ma because a 4x1 ring splits the anode current across its returns — a distinction conventional tDCS does not have.';

CREATE TABLE reference."tavns_dosing" (
    "tavns_dosing_id"       UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"          UUID NOT NULL,
    "device_id"             UUID NOT NULL,
    "tavns_placement_id"    UUID NOT NULL,
    "evidence_level"        TEXT NOT NULL,
    "intensity_ma"          NUMERIC(4,2),
    "pulse_width_us"        INTEGER,
    "frequency_hz"          NUMERIC(6,2),
    "duty_cycle_on_sec"     INTEGER,
    "duty_cycle_off_sec"    INTEGER,
    "session_duration_min"  INTEGER,
    "num_sessions_text"     TEXT,
    "notes"                 TEXT,
    "is_active"             BOOLEAN NOT NULL DEFAULT true,
    "created_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_tavns_dosing_evidence_level" CHECK ("evidence_level" IN ('A', 'B', 'C'))
);

CREATE TABLE reference."tps_dosing" (
    "tps_dosing_id"        UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"         UUID NOT NULL,
    "device_id"            UUID NOT NULL,
    "tps_placement_id"     UUID NOT NULL,
    "evidence_level"       TEXT NOT NULL,
    "energy_mj"            NUMERIC(6,3),
    "pulses_per_session"   INTEGER,
    "pulse_rate_hz"        NUMERIC(6,2),
    "num_sessions_text"    TEXT,
    "notes"                TEXT,
    "is_active"            BOOLEAN NOT NULL DEFAULT true,
    "created_at"           TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_tps_dosing_evidence_level" CHECK ("evidence_level" IN ('A', 'B', 'C'))
);

CREATE TABLE reference."rtms_dosing" (
    "rtms_dosing_id"           UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"             UUID NOT NULL,
    "device_id"                UUID NOT NULL,
    "rtms_placement_id"        UUID NOT NULL,
    "evidence_level"           TEXT NOT NULL,
    "frequency_hz"             NUMERIC(6,2),
    "pct_motor_threshold"      NUMERIC(5,2),
    "train_count"              INTEGER,
    "pulses_per_train"         INTEGER,
    "pulses_per_session"       INTEGER,
    "inter_train_interval_sec" NUMERIC(6,2),
    "num_sessions_text"        TEXT,
    "notes"                    TEXT,
    "is_active"                BOOLEAN NOT NULL DEFAULT true,
    "created_at"               TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_rtms_dosing_pct_motor_threshold" CHECK (
        "pct_motor_threshold" IS NULL
        OR ("pct_motor_threshold" > 0 AND "pct_motor_threshold" <= 200)
    ),
    CONSTRAINT "chk_rtms_dosing_evidence_level" CHECK ("evidence_level" IN ('A', 'B', 'C'))
);
COMMENT ON COLUMN reference."rtms_dosing"."pct_motor_threshold" IS 'rTMS intensity is expressed as a percentage of the patient''s resting motor threshold, not an absolute current. Values above 100 are clinically normal; the CHECK bounds it at 200 as a typo guard, not a clinical limit.';

CREATE TABLE reference."other_dosing" (
    "other_dosing_id"      UUID NOT NULL DEFAULT gen_random_uuid(),
    "condition_id"         UUID NOT NULL,
    "device_id"            UUID NOT NULL,
    "other_placement_id"   UUID NOT NULL,
    "evidence_level"       TEXT NOT NULL,
    "dose_details"         JSONB NOT NULL DEFAULT '{}'::jsonb,
    "num_sessions_text"    TEXT,
    "notes"                TEXT,
    "is_active"            BOOLEAN NOT NULL DEFAULT true,
    "created_at"           TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_other_dosing_evidence_level" CHECK ("evidence_level" IN ('A', 'B', 'C'))
);


-- ###########################################################################
-- §8  LAYER 1 — Tables: Treatment Protocol module (new, core schema)
-- ###########################################################################


CREATE TABLE core."treatment_protocols" (
    "protocol_id"            UUID NOT NULL DEFAULT gen_random_uuid(),
    "plan_id"                UUID NOT NULL,
    "device_id"              UUID NOT NULL,
    "set_by"                 UUID NOT NULL,

    -- Exactly one placement FK and one dosing FK is non-null. Enforced by the
    -- two CHECKs below. Twelve nullable columns is the price of keeping every
    -- reference a REAL foreign key: the alternative — a polymorphic
    -- (placement_type, placement_id) pair — cannot be constrained at all, and
    -- 11_foreign_keys.sql's header shows the project already treats each
    -- polymorphic column as an exception it had to justify one by one.
    "tdcs_placement_id"      UUID,
    "hd_tdcs_placement_id"   UUID,
    "tavns_placement_id"     UUID,
    "tps_placement_id"       UUID,
    "rtms_placement_id"      UUID,
    "other_placement_id"     UUID,

    "tdcs_dosing_id"         UUID,
    "hd_tdcs_dosing_id"      UUID,
    "tavns_dosing_id"        UUID,
    "tps_dosing_id"          UUID,
    "rtms_dosing_id"         UUID,
    "other_dosing_id"        UUID,

    -- The session plan. Setting these is what generates the appointments.
    "session_count"          INTEGER NOT NULL,
    "follow_up_every_n"      INTEGER,

    "status"                 TEXT NOT NULL DEFAULT 'draft',
    "device_settings"        JSONB NOT NULL DEFAULT '{}'::jsonb,
    "notes"                  TEXT,
    "activated_at"           TIMESTAMPTZ,
    "completed_at"           TIMESTAMPTZ,
    "created_at"             TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at"             TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT "chk_treatment_protocols_session_count" CHECK ("session_count" > 0),
    -- v2 Layer 2: text + CHECK, not a native enum.
    CONSTRAINT "chk_treatment_protocols_status" CHECK ("status" IN
        ('draft', 'active', 'completed', 'cancelled', 'superseded')),
    CONSTRAINT "chk_treatment_protocols_follow_up_every_n" CHECK (
        "follow_up_every_n" IS NULL
        OR ("follow_up_every_n" > 0 AND "follow_up_every_n" <= "session_count")
    ),
    CONSTRAINT "chk_treatment_protocols_one_placement" CHECK (
        ("tdcs_placement_id"    IS NOT NULL)::int +
        ("hd_tdcs_placement_id" IS NOT NULL)::int +
        ("tavns_placement_id"   IS NOT NULL)::int +
        ("tps_placement_id"     IS NOT NULL)::int +
        ("rtms_placement_id"    IS NOT NULL)::int +
        ("other_placement_id"   IS NOT NULL)::int = 1
    ),
    CONSTRAINT "chk_treatment_protocols_one_dosing" CHECK (
        ("tdcs_dosing_id"    IS NOT NULL)::int +
        ("hd_tdcs_dosing_id" IS NOT NULL)::int +
        ("tavns_dosing_id"   IS NOT NULL)::int +
        ("tps_dosing_id"     IS NOT NULL)::int +
        ("rtms_dosing_id"    IS NOT NULL)::int +
        ("other_dosing_id"   IS NOT NULL)::int = 1
    )
);
COMMENT ON TABLE core."treatment_protocols" IS 'Treatment Protocol — device + montage + dosing + session plan, set once by a doctor to start a course of treatment. A child of core.treatment_plans, which remains the superset (history + protocol + signature).';
COMMENT ON COLUMN core."treatment_protocols"."follow_up_every_n" IS 'Insert a follow-up appointment after every Nth device session. NULL = no scheduled follow-ups. Example: session_count=20, follow_up_every_n=5 generates 20 device-session appointments plus 4 follow-ups (after sessions 5, 10, 15, 20).';
COMMENT ON COLUMN core."treatment_protocols"."device_settings" IS 'Per-patient overrides on top of the catalogue dose (e.g. reduced current for tolerability). The catalogue row is the prescribed protocol; this is the deviation from it.';

CREATE TABLE core."protocol_sessions" (
    "protocol_session_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "protocol_id"          UUID NOT NULL,
    "session_number"       INTEGER NOT NULL,
    "planned_date"         DATE,
    "status"               TEXT NOT NULL DEFAULT 'planned',
    "created_at"           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_protocol_sessions_session_number" CHECK ("session_number" > 0),
    CONSTRAINT "chk_protocol_sessions_status" CHECK ("status" IN
        ('planned', 'scheduled', 'completed', 'cancelled'))
);
COMMENT ON TABLE core."protocol_sessions" IS 'One row per PRESCRIBED device session — the protocol''s intent, not its scheduling. Carries the ordinal (1..session_count) and a planned date. Deliberately holds NO foreign key to any scheduling table: architecture v2 makes appointments (doctor consultations) and treatment_sessions (tDCS, CA-administered) siblings, and the session schema is not finalised. Binding to either one now would be a guess that costs a data migration to undo; leaving it unbound costs one ALTER TABLE to fix. See the deferred-binding note in §8.';
COMMENT ON COLUMN core."protocol_sessions"."planned_date" IS 'Intended date from the protocol''s cadence. Advisory only — the real scheduled datetime lives on whichever table owns device sessions once that is settled.';
COMMENT ON COLUMN core."protocol_sessions"."status" IS 'planned = prescribed, not yet booked. scheduled = a real session row exists. Until the session schema lands, nothing advances past planned.';

CREATE TABLE core."protocol_followups" (
    "protocol_followup_id"  UUID NOT NULL DEFAULT gen_random_uuid(),
    "protocol_id"           UUID NOT NULL,
    "appointment_id"        UUID NOT NULL,
    "after_session_number"  INTEGER NOT NULL,
    "created_at"            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "chk_protocol_followups_after_session_number" CHECK ("after_session_number" > 0)
);
COMMENT ON TABLE core."protocol_followups" IS 'One row per generated follow-up appointment. after_session_number records which device session triggered it (5, 10, 15, 20 for follow_up_every_n = 5). UNLIKE protocol_sessions, this DOES foreign-key to core.appointments — and correctly so: a follow-up IS a doctor consultation, which is exactly what architecture v2 says an appointment is. Only the tDCS device sessions are unbound.';


CREATE TABLE core."device_session_prs_responses" (
    "ds_prs_id"            UUID NOT NULL DEFAULT gen_random_uuid(),
    "protocol_session_id"  UUID NOT NULL,
    "instance_id"          TEXT NOT NULL,
    "patient_id"           UUID NOT NULL,
    "recorded_at"          TIMESTAMPTZ NOT NULL DEFAULT now(),
    "created_at"           TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE core."device_session_prs_responses" IS 'PRS taken after a device session. FKs to protocol_sessions (not appointments) so every assessment is traceable to its exact session ordinal.';
COMMENT ON COLUMN core."device_session_prs_responses"."patient_id" IS 'References core.profiles(id), matching the convention confirmed in NOTES.md: patient_id-shaped FK columns resolve to profiles.id, not patients.patient_id.';

CREATE TABLE core."followup_prs_responses" (
    "fu_prs_id"             UUID NOT NULL DEFAULT gen_random_uuid(),
    "protocol_followup_id"  UUID NOT NULL,
    "instance_id"           TEXT NOT NULL,
    "patient_id"            UUID NOT NULL,
    "recorded_at"           TIMESTAMPTZ NOT NULL DEFAULT now(),
    "created_at"            TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE core."followup_prs_responses" IS 'PRS taken during a follow-up visit. Kept as a separate table from device_session_prs_responses by requirement.';


-- ###########################################################################
-- §9  LAYER 5 — Tables and columns: compliance workstream (existing)
-- ###########################################################################

-- Files 20-24, 27-29. Built 2026-07-21 against the Closure Schema Change
-- Requirements doc. Retention SEMANTICS remain gated on Blocker 2.
--
-- NOTE: files 27-29 are self-contained patches that add a column AND its
-- foreign key/index in one statement block. In this consolidated file the
-- column definitions stay here; their FK and index move to §12 and §13,
-- because a foreign key cannot be declared before the primary key it
-- references exists (§10).

-- Layer 5 — Compliance schema net-new objects. Maps to the 12 items in the
-- Closure Schema Change Requirements doc, sourced from the Account Closure,
-- Data Retention & Regulatory Compliance Policy (treated as final per approval
-- to proceed, 2026-07-21). Every table/column below cites the policy section
-- it implements.

-- ---------------------------------------------------------------------------
-- 1. erasure_requests + erasure_request_items  (P0 — Section 3.5)
-- ---------------------------------------------------------------------------
CREATE TABLE compliance."erasure_requests" (
    "request_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "requested_by" UUID NOT NULL,
    "requester_verification_method" TEXT,
    "status" TEXT NOT NULL DEFAULT 'received',
    "received_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "response_due_at" TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '30 days'),
    "responded_at" TIMESTAMPTZ,
    "response_summary" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE compliance."erasure_requests" IS 'Section 3.5 — a formal patient erasure request. Triggers a classification pass, never immediate bulk deletion.';
COMMENT ON COLUMN compliance."erasure_requests"."status" IS 'received | classified | partially_completed | completed';

CREATE TABLE compliance."erasure_request_items" (
    "item_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "request_id" UUID NOT NULL,
    "data_category" TEXT NOT NULL,
    "bucket" TEXT NOT NULL,
    "legal_basis" TEXT,
    "retention_expires_at" TIMESTAMPTZ,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE compliance."erasure_request_items" IS 'Section 3.5/7.1 — one row per data category evaluated in the classification pass.';
COMMENT ON COLUMN compliance."erasure_request_items"."bucket" IS 'delete_now | retain_locked | compliance_evidence';

-- ---------------------------------------------------------------------------
-- 2. data_portability_requests  (P0 — Section 3.6)
-- ---------------------------------------------------------------------------
CREATE TABLE compliance."data_portability_requests" (
    "request_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "requested_by" UUID NOT NULL,
    "format" TEXT NOT NULL DEFAULT 'json',
    "status" TEXT NOT NULL DEFAULT 'pending',
    "delivery_method" TEXT,
    "delivered_at" TIMESTAMPTZ,
    "download_expires_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE compliance."data_portability_requests" IS 'Section 3.6 — structured export request. format: json|pdf. status: pending|generating|delivered|expired.';

-- ---------------------------------------------------------------------------
-- 3. staff_termination_authorizations  (P1 — Section 4.3)
-- ---------------------------------------------------------------------------
CREATE TABLE compliance."staff_termination_authorizations" (
    "termination_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "staff_profile_id" UUID NOT NULL,
    "termination_type" TEXT NOT NULL,
    "reason" TEXT,
    "primary_authorizer_id" UUID NOT NULL,
    "secondary_authorizer_id" UUID,
    "authorized_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "effective_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE compliance."staff_termination_authorizations" IS 'Section 4.3 — termination_type: voluntary|no_cause|for_cause. secondary_authorizer_id required (app-enforced) only when termination_type=for_cause — two-person authorization.';

-- ---------------------------------------------------------------------------
-- 4. compliance_incidents  (P1 — Section 10)
-- ---------------------------------------------------------------------------
CREATE TABLE compliance."compliance_incidents" (
    "incident_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "detected_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "detected_by" UUID,
    "category" TEXT NOT NULL,
    "affected_data_categories" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "affected_patient_count" INTEGER,
    "severity" TEXT,
    "containment_actions" TEXT,
    "board_notified_at" TIMESTAMPTZ,
    "patients_notified_at" TIMESTAMPTZ,
    "eu_authority_notified_at" TIMESTAMPTZ,
    "remediation_summary" TEXT,
    "post_incident_review_at" TIMESTAMPTZ,
    "status" TEXT NOT NULL DEFAULT 'open',
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE compliance."compliance_incidents" IS 'Section 10 — breach/incident record. status: open|contained|notified|closed.';

-- ---------------------------------------------------------------------------
-- 5. manual_snapshots  (P2 — Section 7.3)
-- ---------------------------------------------------------------------------
CREATE TABLE compliance."manual_snapshots" (
    "snapshot_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "purpose" TEXT NOT NULL,
    "created_by" UUID,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "intended_deletion_at" TIMESTAMPTZ NOT NULL,
    "deleted_at" TIMESTAMPTZ
);
COMMENT ON TABLE compliance."manual_snapshots" IS 'Section 7.3 — on-demand RDS/S3 snapshots, tagged with intended deletion date at creation so none are left indefinitely.';

-- Layer 5 — new columns on existing tables. Cites policy section per column.

-- profiles: anonymisation idempotency marker (Sections 6, 7)
ALTER TABLE core."profiles" ADD COLUMN "is_anonymized" BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE core."profiles" ADD COLUMN "anonymized_at" TIMESTAMPTZ;
COMMENT ON COLUMN core."profiles"."is_anonymized" IS 'Bucket 2 anonymisation applied. Purge worker checks this to avoid double-processing.';

-- patients: retention clock, legal hold, closure-state tracking (Sections 3.1, 3.2, 6, 7)
ALTER TABLE core."patients" ADD COLUMN "retention_basis_cleared_at" TIMESTAMPTZ;
ALTER TABLE core."patients" ADD COLUMN "legal_hold" BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE core."patients" ADD COLUMN "closure_type" TEXT;
ALTER TABLE core."patients" ADD COLUMN "closure_reason" TEXT;
ALTER TABLE core."patients" ADD COLUMN "closed_at" TIMESTAMPTZ;
ALTER TABLE core."patients" ADD COLUMN "rejoin_deadline" TIMESTAMPTZ;
ALTER TABLE core."patients" ADD COLUMN "portal_access_mode" TEXT NOT NULL DEFAULT 'full';
ALTER TABLE core."patients" ADD COLUMN "last_clinical_contact_at" TIMESTAMPTZ;
COMMENT ON COLUMN core."patients"."retention_basis_cleared_at" IS 'Latest of every linked retention window (7yr clinical, 8yr financial). Anonymisation waits for this, not the earliest window.';
COMMENT ON COLUMN core."patients"."legal_hold" IS 'Active medico-legal case — overrides the normal retention clock (Bucket 2).';
COMMENT ON COLUMN core."patients"."closure_type" IS 'voluntary | dormant | transfer_terminated';
COMMENT ON COLUMN core."patients"."rejoin_deadline" IS 'closed_at + 9 months, set only when closure_type=voluntary. Dormant closures have no rejoin window.';
COMMENT ON COLUMN core."patients"."portal_access_mode" IS 'full | read_only | disabled';
COMMENT ON COLUMN core."patients"."last_clinical_contact_at" IS 'Feeds both the dormancy clock (3mo/1yr) and the 7-year clinical retention clock.';

-- doctors: legal hold (same Bucket 2 override, staff-side — active investigation/litigation)
ALTER TABLE core."doctors" ADD COLUMN "legal_hold" BOOLEAN NOT NULL DEFAULT false;

-- consent_records: guardian consent linkage (Section 3.4)
ALTER TABLE compliance."consent_records" ADD COLUMN "guardian_id" UUID;
COMMENT ON COLUMN compliance."consent_records"."guardian_id" IS 'Set when the signer is a minor patient''s parent/guardian, not the patient themselves.';

-- admins.admin_type already unconstrained TEXT (no CHECK constraint exists in this schema —
-- consistent with the rest of the schema's status columns). 'grievance_officer' is now a
-- documented valid value (Section 11) — no DDL required to "allow" it.

-- Receptionist-initiated patient registration (OTP channel-verification,
-- same Cognito wizard self-registration uses — see auth/router.py). Adds
-- who registered a staff-registered patient, alongside the existing
-- self_registered flag (which already captures self-service vs staff).
-- NULL when self_registered = true (the patient registered themselves,
-- nothing to attribute). Set to the receptionist's profiles.id otherwise.
-- audit_logs already records the INSERT itself via changed_by — this
-- column is for fast reads/reporting without joining the audit log.

ALTER TABLE core."patients" ADD COLUMN "registered_by" UUID;

-- Under-18 patient registration (self-service or receptionist): no separate
-- guardian identity/table — the patient's own email/phone columns simply
-- hold the guardian's contact (used for login) when the patient is a minor.
-- These two columns just record who that contact belongs to. One guardian
-- per patient only; a second child needs a different contact (accepted
-- tradeoff, not a bug — see PatientService._is_minor / register()).

ALTER TABLE core."patients" ADD COLUMN "guardian_name" TEXT;
ALTER TABLE core."patients" ADD COLUMN "guardian_relationship" TEXT;

-- Guardian details captured during demographics (self-registration and
-- receptionist registration, same shared wizard steps): name, relationship,
-- and now contact as its own field — separate from the patient's own
-- email/phone (the Cognito login channel), which for a minor already holds
-- the guardian's number by design (see 28_patient_guardian_fields.sql).
-- Allowed to duplicate that login contact; not the same column on purpose,
-- since a minor's login channel could be their own phone while the
-- guardian's contact is recorded independently for records/emergency use.

ALTER TABLE core."patients" ADD COLUMN "guardian_contact" TEXT;


-- ###########################################################################
-- §10  LAYER 2 — Primary keys (83 tables)
-- ###########################################################################

-- ---- existing (61) ----

ALTER TABLE compliance."activity_logs" ADD CONSTRAINT "activity_logs_pkey" PRIMARY KEY ("log_id", "created_at");
ALTER TABLE core."admins" ADD CONSTRAINT "admins_pkey" PRIMARY KEY ("admin_id");
ALTER TABLE ops."alembic_version" ADD CONSTRAINT "alembic_version_pkey" PRIMARY KEY ("version_num");
ALTER TABLE core."anamnesis_assessments" ADD CONSTRAINT "anamnesis_assessments_pkey" PRIMARY KEY ("anamnesis_id");
ALTER TABLE reference."anamnesis_options" ADD CONSTRAINT "anamnesis_options_pkey" PRIMARY KEY ("option_id");
ALTER TABLE reference."anamnesis_questions" ADD CONSTRAINT "anamnesis_questions_pkey" PRIMARY KEY ("question_id");
ALTER TABLE core."anamnesis_responses" ADD CONSTRAINT "anamnesis_responses_pkey" PRIMARY KEY ("response_id");
ALTER TABLE core."appointment_audit_logs" ADD CONSTRAINT "appointment_audit_logs_pkey" PRIMARY KEY ("audit_id", "changed_at");
ALTER TABLE core."appointment_requests" ADD CONSTRAINT "appointment_requests_pkey" PRIMARY KEY ("request_id");
ALTER TABLE core."appointments" ADD CONSTRAINT "appointments_pkey" PRIMARY KEY ("appointment_id");
ALTER TABLE core."assessment_protocol_requests" ADD CONSTRAINT "assessment_protocol_requests_pkey" PRIMARY KEY ("request_id");
ALTER TABLE compliance."audit_logs" ADD CONSTRAINT "audit_logs_pkey" PRIMARY KEY ("log_id", "changed_at");
ALTER TABLE core."ca_doctor_assignments" ADD CONSTRAINT "ca_doctor_assignments_pkey" PRIMARY KEY ("cda_id");
ALTER TABLE core."clinic_requests" ADD CONSTRAINT "clinic_requests_pkey" PRIMARY KEY ("request_id");
ALTER TABLE core."clinic_staff_assignments" ADD CONSTRAINT "clinic_staff_assignments_pkey" PRIMARY KEY ("assignment_id");
ALTER TABLE core."clinical_assistants" ADD CONSTRAINT "clinical_assistants_pkey" PRIMARY KEY ("ca_id");
ALTER TABLE core."clinics" ADD CONSTRAINT "clinics_pkey" PRIMARY KEY ("clinic_id");
ALTER TABLE compliance."consent_records" ADD CONSTRAINT "consent_records_pkey" PRIMARY KEY ("consent_id");
ALTER TABLE reference."consent_templates" ADD CONSTRAINT "consent_templates_pkey" PRIMARY KEY ("template_id");
ALTER TABLE core."device_assignments" ADD CONSTRAINT "device_assignments_pkey" PRIMARY KEY ("da_id");
ALTER TABLE core."doctor_patient_assignments" ADD CONSTRAINT "doctor_patient_assignments_pkey" PRIMARY KEY ("assignment_id");
ALTER TABLE core."doctor_schedule_overrides" ADD CONSTRAINT "doctor_schedule_overrides_pkey" PRIMARY KEY ("override_id");
ALTER TABLE core."doctor_session_notes" ADD CONSTRAINT "doctor_session_notes_pkey" PRIMARY KEY ("note_id");
ALTER TABLE core."doctor_weekly_schedules" ADD CONSTRAINT "doctor_weekly_schedules_pkey" PRIMARY KEY ("schedule_id");
ALTER TABLE core."doctors" ADD CONSTRAINT "doctors_pkey" PRIMARY KEY ("doctor_id");
ALTER TABLE core."inventory" ADD CONSTRAINT "inventory_pkey" PRIMARY KEY ("inventory_id");
ALTER TABLE core."notifications" ADD CONSTRAINT "notifications_pkey" PRIMARY KEY ("notification_id", "created_at");
ALTER TABLE core."order_items" ADD CONSTRAINT "order_items_pkey" PRIMARY KEY ("item_id");
ALTER TABLE ops."outbox_events" ADD CONSTRAINT "outbox_events_pkey" PRIMARY KEY ("outbox_id");
ALTER TABLE core."patient_clinic_transfers" ADD CONSTRAINT "patient_clinic_transfers_pkey" PRIMARY KEY ("pct_id");
ALTER TABLE core."patient_disease_selection" ADD CONSTRAINT "patient_disease_selection_pkey" PRIMARY KEY ("pds_id");
ALTER TABLE core."patient_eeg_files" ADD CONSTRAINT "patient_eeg_files_pkey" PRIMARY KEY ("eeg_id");
ALTER TABLE core."patient_medical_history_files" ADD CONSTRAINT "patient_medical_history_files_pkey" PRIMARY KEY ("mhf_id");
ALTER TABLE core."patient_scale_assignments" ADD CONSTRAINT "patient_scale_assignments_pkey" PRIMARY KEY ("psa_id");
ALTER TABLE core."patients" ADD CONSTRAINT "patients_pkey" PRIMARY KEY ("patient_id");
ALTER TABLE core."payments" ADD CONSTRAINT "payments_pkey" PRIMARY KEY ("payment_id");
ALTER TABLE reference."products" ADD CONSTRAINT "products_pkey" PRIMARY KEY ("product_id");
ALTER TABLE core."profiles" ADD CONSTRAINT "profiles_pkey" PRIMARY KEY ("id");
ALTER TABLE core."prs_assessment_instances" ADD CONSTRAINT "prs_assessment_instances_pkey" PRIMARY KEY ("instance_id");
ALTER TABLE reference."prs_disease_question_map" ADD CONSTRAINT "prs_disease_question_map_pkey" PRIMARY KEY ("dq_map_id");
ALTER TABLE reference."prs_disease_scale_map" ADD CONSTRAINT "prs_disease_scale_map_pkey" PRIMARY KEY ("ds_map_id");
ALTER TABLE reference."prs_diseases" ADD CONSTRAINT "prs_diseases_pkey" PRIMARY KEY ("disease_id");
ALTER TABLE core."prs_final_results" ADD CONSTRAINT "prs_final_results_pkey" PRIMARY KEY ("final_result_id");
ALTER TABLE reference."prs_option_translations" ADD CONSTRAINT "prs_option_translations_pkey" PRIMARY KEY ("option_id", "language_code");
ALTER TABLE reference."prs_options" ADD CONSTRAINT "prs_options_pkey" PRIMARY KEY ("option_id");
ALTER TABLE reference."prs_question_translations" ADD CONSTRAINT "prs_question_translations_pkey" PRIMARY KEY ("question_id", "language_code");
ALTER TABLE reference."prs_questions" ADD CONSTRAINT "prs_questions_pkey" PRIMARY KEY ("question_id");
ALTER TABLE core."prs_responses" ADD CONSTRAINT "prs_responses_pkey" PRIMARY KEY ("response_id");
ALTER TABLE reference."prs_scale_question_map" ADD CONSTRAINT "prs_scale_question_map_pkey" PRIMARY KEY ("sq_map_id");
ALTER TABLE core."prs_scale_results" ADD CONSTRAINT "prs_scale_results_pkey" PRIMARY KEY ("scale_result_id");
ALTER TABLE reference."prs_scales" ADD CONSTRAINT "prs_scales_pkey" PRIMARY KEY ("scale_id");
ALTER TABLE core."receptionists" ADD CONSTRAINT "receptionists_pkey" PRIMARY KEY ("receptionist_id");
ALTER TABLE core."regions" ADD CONSTRAINT "regions_pkey" PRIMARY KEY ("region_id");
ALTER TABLE ops."schema_migrations" ADD CONSTRAINT "schema_migrations_pkey" PRIMARY KEY ("version");
ALTER TABLE core."sessions" ADD CONSTRAINT "sessions_pkey" PRIMARY KEY ("session_id");
ALTER TABLE core."staff_requests" ADD CONSTRAINT "staff_requests_pkey" PRIMARY KEY ("request_id");
ALTER TABLE core."stock_transfers" ADD CONSTRAINT "stock_transfers_pkey" PRIMARY KEY ("st_id");
ALTER TABLE core."store_orders" ADD CONSTRAINT "store_orders_pkey" PRIMARY KEY ("order_id");
ALTER TABLE core."treatment_cycles" ADD CONSTRAINT "treatment_cycles_pkey" PRIMARY KEY ("cycle_id");
ALTER TABLE core."treatment_plans" ADD CONSTRAINT "treatment_plans_pkey" PRIMARY KEY ("plan_id");
ALTER TABLE core."treatment_sessions" ADD CONSTRAINT "treatment_sessions_pkey" PRIMARY KEY ("ts_id", "created_at");
-- ---- new: Treatment Protocol module (22) ----

ALTER TABLE reference."neuromod_devices"          ADD CONSTRAINT "neuromod_devices_pkey"          PRIMARY KEY ("device_id");
ALTER TABLE reference."neuromod_conditions"       ADD CONSTRAINT "neuromod_conditions_pkey"       PRIMARY KEY ("condition_id");
ALTER TABLE reference."neuromod_diagnoses"        ADD CONSTRAINT "neuromod_diagnoses_pkey"        PRIMARY KEY ("diagnosis_id");
ALTER TABLE reference."neuromod_scales"           ADD CONSTRAINT "neuromod_scales_pkey"           PRIMARY KEY ("scale_id");
ALTER TABLE reference."neuromod_condition_scales" ADD CONSTRAINT "neuromod_condition_scales_pkey" PRIMARY KEY ("cs_map_id");

ALTER TABLE reference."tdcs_placements"     ADD CONSTRAINT "tdcs_placements_pkey"     PRIMARY KEY ("tdcs_placement_id");
ALTER TABLE reference."hd_tdcs_placements"  ADD CONSTRAINT "hd_tdcs_placements_pkey"  PRIMARY KEY ("hd_tdcs_placement_id");
ALTER TABLE reference."tavns_placements"    ADD CONSTRAINT "tavns_placements_pkey"    PRIMARY KEY ("tavns_placement_id");
ALTER TABLE reference."tps_placements"      ADD CONSTRAINT "tps_placements_pkey"      PRIMARY KEY ("tps_placement_id");
ALTER TABLE reference."rtms_placements"     ADD CONSTRAINT "rtms_placements_pkey"     PRIMARY KEY ("rtms_placement_id");
ALTER TABLE reference."other_placements"    ADD CONSTRAINT "other_placements_pkey"    PRIMARY KEY ("other_placement_id");

ALTER TABLE reference."tdcs_dosing"     ADD CONSTRAINT "tdcs_dosing_pkey"     PRIMARY KEY ("tdcs_dosing_id");
ALTER TABLE reference."hd_tdcs_dosing"  ADD CONSTRAINT "hd_tdcs_dosing_pkey"  PRIMARY KEY ("hd_tdcs_dosing_id");
ALTER TABLE reference."tavns_dosing"    ADD CONSTRAINT "tavns_dosing_pkey"    PRIMARY KEY ("tavns_dosing_id");
ALTER TABLE reference."tps_dosing"      ADD CONSTRAINT "tps_dosing_pkey"      PRIMARY KEY ("tps_dosing_id");
ALTER TABLE reference."rtms_dosing"     ADD CONSTRAINT "rtms_dosing_pkey"     PRIMARY KEY ("rtms_dosing_id");
ALTER TABLE reference."other_dosing"    ADD CONSTRAINT "other_dosing_pkey"    PRIMARY KEY ("other_dosing_id");

ALTER TABLE core."treatment_protocols"          ADD CONSTRAINT "treatment_protocols_pkey"          PRIMARY KEY ("protocol_id");
ALTER TABLE core."protocol_sessions"            ADD CONSTRAINT "protocol_sessions_pkey"            PRIMARY KEY ("protocol_session_id");
ALTER TABLE core."protocol_followups"           ADD CONSTRAINT "protocol_followups_pkey"           PRIMARY KEY ("protocol_followup_id");
ALTER TABLE core."device_session_prs_responses" ADD CONSTRAINT "device_session_prs_responses_pkey" PRIMARY KEY ("ds_prs_id");
ALTER TABLE core."followup_prs_responses"       ADD CONSTRAINT "followup_prs_responses_pkey"       PRIMARY KEY ("fu_prs_id");


-- ###########################################################################
-- §11  LAYER 2 — Unique constraints
-- ###########################################################################

-- ---- existing ----

ALTER TABLE core."admins" ADD CONSTRAINT "admins_profile_id_key" UNIQUE ("profile_id");
ALTER TABLE core."anamnesis_assessments" ADD CONSTRAINT "anamnesis_assessments_patient_id_version_key" UNIQUE ("patient_id", "version");
ALTER TABLE reference."anamnesis_options" ADD CONSTRAINT "anamnesis_options_question_id_option_value_key" UNIQUE ("question_id", "option_value");
ALTER TABLE reference."anamnesis_questions" ADD CONSTRAINT "anamnesis_questions_question_code_key" UNIQUE ("question_code");
ALTER TABLE core."ca_doctor_assignments" ADD CONSTRAINT "ca_doctor_assignments_ca_id_doctor_id_key" UNIQUE ("ca_id", "doctor_id");
ALTER TABLE core."clinic_staff_assignments" ADD CONSTRAINT "clinic_staff_assignments_clinic_id_profile_id_key" UNIQUE ("clinic_id", "profile_id");
ALTER TABLE core."clinical_assistants" ADD CONSTRAINT "clinical_assistants_profile_id_key" UNIQUE ("profile_id");
ALTER TABLE core."clinics" ADD CONSTRAINT "clinics_clinic_code_key" UNIQUE ("clinic_code");
ALTER TABLE reference."consent_templates" ADD CONSTRAINT "uq_consent_templates_type_role_version" UNIQUE ("consent_type", "role", "version");
ALTER TABLE core."doctor_schedule_overrides" ADD CONSTRAINT "doctor_schedule_overrides_doctor_id_override_date_key" UNIQUE ("doctor_id", "override_date");
ALTER TABLE core."doctor_session_notes" ADD CONSTRAINT "doctor_session_notes_session_id_doctor_id_session_phase_key" UNIQUE ("session_id", "doctor_id", "session_phase");
ALTER TABLE core."doctor_weekly_schedules" ADD CONSTRAINT "doctor_weekly_schedules_doctor_id_clinic_id_day_of_week_key" UNIQUE ("doctor_id", "clinic_id", "day_of_week");
ALTER TABLE core."doctors" ADD CONSTRAINT "doctors_profile_id_key" UNIQUE ("profile_id");
ALTER TABLE core."inventory" ADD CONSTRAINT "inventory_product_id_clinic_id_key" UNIQUE ("product_id", "clinic_id");
ALTER TABLE core."patient_eeg_files" ADD CONSTRAINT "patient_eeg_files_raw_data_s3_key_key" UNIQUE ("raw_data_s3_key");
ALTER TABLE core."patient_eeg_files" ADD CONSTRAINT "patient_eeg_files_report_s3_key_key" UNIQUE ("report_s3_key");
ALTER TABLE core."patient_medical_history_files" ADD CONSTRAINT "patient_medical_history_files_s3_key_key" UNIQUE ("s3_key");
ALTER TABLE core."patients" ADD CONSTRAINT "patients_mrn_key" UNIQUE ("mrn");
ALTER TABLE core."patients" ADD CONSTRAINT "patients_profile_id_key" UNIQUE ("profile_id");
ALTER TABLE core."payments" ADD CONSTRAINT "payments_idempotency_key_key" UNIQUE ("idempotency_key");
ALTER TABLE core."payments" ADD CONSTRAINT "payments_razorpay_order_id_key" UNIQUE ("razorpay_order_id");
ALTER TABLE core."payments" ADD CONSTRAINT "payments_razorpay_payment_id_key" UNIQUE ("razorpay_payment_id");
ALTER TABLE reference."products" ADD CONSTRAINT "products_sku_key" UNIQUE ("sku");
ALTER TABLE core."profiles" ADD CONSTRAINT "profiles_cognito_sub_key" UNIQUE ("cognito_sub");
ALTER TABLE core."profiles" ADD CONSTRAINT "profiles_email_key" UNIQUE ("email");
ALTER TABLE reference."prs_disease_question_map" ADD CONSTRAINT "prs_disease_question_map_disease_id_question_id_key" UNIQUE ("disease_id", "question_id");
ALTER TABLE reference."prs_disease_scale_map" ADD CONSTRAINT "prs_disease_scale_map_disease_id_scale_id_key" UNIQUE ("disease_id", "scale_id");
ALTER TABLE reference."prs_diseases" ADD CONSTRAINT "prs_diseases_disease_code_key" UNIQUE ("disease_code");
ALTER TABLE core."prs_final_results" ADD CONSTRAINT "prs_final_results_instance_id_key" UNIQUE ("instance_id");
ALTER TABLE reference."prs_options" ADD CONSTRAINT "prs_options_question_id_option_value_key" UNIQUE ("question_id", "option_value");
ALTER TABLE reference."prs_questions" ADD CONSTRAINT "prs_questions_question_code_key" UNIQUE ("question_code");
ALTER TABLE core."prs_responses" ADD CONSTRAINT "prs_responses_instance_id_question_id_key" UNIQUE ("instance_id", "question_id");
ALTER TABLE reference."prs_scale_question_map" ADD CONSTRAINT "prs_scale_question_map_scale_id_question_id_key" UNIQUE ("scale_id", "question_id");
ALTER TABLE core."prs_scale_results" ADD CONSTRAINT "prs_scale_results_instance_id_scale_id_key" UNIQUE ("instance_id", "scale_id");
ALTER TABLE reference."prs_scales" ADD CONSTRAINT "prs_scales_scale_code_key" UNIQUE ("scale_code");
ALTER TABLE core."receptionists" ADD CONSTRAINT "receptionists_profile_id_key" UNIQUE ("profile_id");
ALTER TABLE core."regions" ADD CONSTRAINT "regions_country_state_key" UNIQUE ("country", "state");
-- ---- new ----

ALTER TABLE reference."neuromod_devices"          ADD CONSTRAINT "neuromod_devices_device_code_key"                UNIQUE ("device_code");
ALTER TABLE reference."neuromod_devices"          ADD CONSTRAINT "neuromod_devices_modality_key"                   UNIQUE ("modality");
ALTER TABLE reference."neuromod_conditions"       ADD CONSTRAINT "neuromod_conditions_condition_name_key"          UNIQUE ("condition_name");
ALTER TABLE reference."neuromod_diagnoses"        ADD CONSTRAINT "neuromod_diagnoses_icd10_code_key"               UNIQUE ("icd10_code");
ALTER TABLE reference."neuromod_scales"           ADD CONSTRAINT "neuromod_scales_scale_code_key"                  UNIQUE ("scale_code");
ALTER TABLE reference."neuromod_condition_scales" ADD CONSTRAINT "neuromod_condition_scales_condition_id_scale_id_key" UNIQUE ("condition_id", "scale_id");

-- One appointment is at most one protocol's Nth session or Kth follow-up, and
-- an ordinal occurs at most once per protocol.
-- protocol_sessions has no appointment_id to make unique (see §8). The ordinal
-- uniqueness is the real invariant: session 7 of a protocol exists exactly once.
ALTER TABLE core."protocol_sessions"  ADD CONSTRAINT "protocol_sessions_protocol_id_session_number_key"           UNIQUE ("protocol_id", "session_number");
ALTER TABLE core."protocol_followups" ADD CONSTRAINT "protocol_followups_appointment_id_key"                      UNIQUE ("appointment_id");
ALTER TABLE core."protocol_followups" ADD CONSTRAINT "protocol_followups_protocol_id_after_session_number_key"    UNIQUE ("protocol_id", "after_session_number");

-- One PRS instance belongs to exactly one visit, and a visit records at most
-- one PRS instance. If a patient can retake a PRS within the same session,
-- the protocol_session_id / protocol_followup_id uniqueness relaxes — flagged
-- as an assumption rather than silently allowed.
ALTER TABLE core."device_session_prs_responses" ADD CONSTRAINT "device_session_prs_responses_protocol_session_id_key" UNIQUE ("protocol_session_id");
ALTER TABLE core."device_session_prs_responses" ADD CONSTRAINT "device_session_prs_responses_instance_id_key"         UNIQUE ("instance_id");
ALTER TABLE core."followup_prs_responses"       ADD CONSTRAINT "followup_prs_responses_protocol_followup_id_key"      UNIQUE ("protocol_followup_id");
ALTER TABLE core."followup_prs_responses"       ADD CONSTRAINT "followup_prs_responses_instance_id_key"               UNIQUE ("instance_id");

-- Layer 5 constraints and indexes (file 22)
-- Layer 5 — primary keys, foreign keys, indexes for the new tables + the new
-- consent_records.guardian_id FK. ON DELETE RESTRICT throughout, matching the
-- rest of the schema's integrity rule (never cascade on clinical/legal data).

-- Primary keys
ALTER TABLE compliance."erasure_requests" ADD CONSTRAINT "erasure_requests_pkey" PRIMARY KEY ("request_id");
ALTER TABLE compliance."erasure_request_items" ADD CONSTRAINT "erasure_request_items_pkey" PRIMARY KEY ("item_id");
ALTER TABLE compliance."data_portability_requests" ADD CONSTRAINT "data_portability_requests_pkey" PRIMARY KEY ("request_id");
ALTER TABLE compliance."staff_termination_authorizations" ADD CONSTRAINT "staff_termination_authorizations_pkey" PRIMARY KEY ("termination_id");
ALTER TABLE compliance."compliance_incidents" ADD CONSTRAINT "compliance_incidents_pkey" PRIMARY KEY ("incident_id");
ALTER TABLE compliance."manual_snapshots" ADD CONSTRAINT "manual_snapshots_pkey" PRIMARY KEY ("snapshot_id");

-- Foreign keys
ALTER TABLE compliance."erasure_requests" ADD CONSTRAINT "fk_erasure_requests_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."erasure_requests" ADD CONSTRAINT "fk_erasure_requests_requested_by" FOREIGN KEY ("requested_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."erasure_request_items" ADD CONSTRAINT "fk_erasure_request_items_request_id" FOREIGN KEY ("request_id") REFERENCES compliance."erasure_requests" ("request_id") ON DELETE RESTRICT;
ALTER TABLE compliance."data_portability_requests" ADD CONSTRAINT "fk_data_portability_requests_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."data_portability_requests" ADD CONSTRAINT "fk_data_portability_requests_requested_by" FOREIGN KEY ("requested_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."staff_termination_authorizations" ADD CONSTRAINT "fk_staff_term_staff_profile_id" FOREIGN KEY ("staff_profile_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."staff_termination_authorizations" ADD CONSTRAINT "fk_staff_term_primary_authorizer" FOREIGN KEY ("primary_authorizer_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."staff_termination_authorizations" ADD CONSTRAINT "fk_staff_term_secondary_authorizer" FOREIGN KEY ("secondary_authorizer_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."compliance_incidents" ADD CONSTRAINT "fk_compliance_incidents_detected_by" FOREIGN KEY ("detected_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."manual_snapshots" ADD CONSTRAINT "fk_manual_snapshots_created_by" FOREIGN KEY ("created_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."consent_records" ADD CONSTRAINT "fk_consent_records_guardian_id" FOREIGN KEY ("guardian_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

-- Indexes
CREATE INDEX "idx_erasure_requests_patient_id" ON compliance."erasure_requests" USING btree ("patient_id");
CREATE INDEX "idx_erasure_requests_status" ON compliance."erasure_requests" USING btree ("status");
CREATE INDEX "idx_erasure_requests_response_due" ON compliance."erasure_requests" USING btree ("response_due_at") WHERE (status <> 'completed');
CREATE INDEX "idx_erasure_request_items_request_id" ON compliance."erasure_request_items" USING btree ("request_id");
CREATE INDEX "idx_erasure_request_items_bucket" ON compliance."erasure_request_items" USING btree ("bucket");
CREATE INDEX "idx_erasure_request_items_retention_expires" ON compliance."erasure_request_items" USING btree ("retention_expires_at") WHERE (deleted_at IS NULL);
CREATE INDEX "idx_dpr_patient_id" ON compliance."data_portability_requests" USING btree ("patient_id");
CREATE INDEX "idx_dpr_status" ON compliance."data_portability_requests" USING btree ("status");
CREATE INDEX "idx_staff_term_staff_profile_id" ON compliance."staff_termination_authorizations" USING btree ("staff_profile_id");
CREATE INDEX "idx_staff_term_type" ON compliance."staff_termination_authorizations" USING btree ("termination_type");
CREATE INDEX "idx_compliance_incidents_status" ON compliance."compliance_incidents" USING btree ("status");
CREATE INDEX "idx_compliance_incidents_detected_at" ON compliance."compliance_incidents" USING btree ("detected_at");
CREATE INDEX "idx_manual_snapshots_intended_deletion" ON compliance."manual_snapshots" USING btree ("intended_deletion_at") WHERE (deleted_at IS NULL);
CREATE INDEX "idx_consent_records_guardian_id" ON compliance."consent_records" USING btree ("guardian_id") WHERE (guardian_id IS NOT NULL);
CREATE INDEX "idx_patients_closure_type" ON core."patients" USING btree ("closure_type") WHERE (closure_type IS NOT NULL);
CREATE INDEX "idx_patients_retention_cleared" ON core."patients" USING btree ("retention_basis_cleared_at") WHERE (legal_hold = false);
CREATE INDEX "idx_profiles_anonymized" ON core."profiles" USING btree ("is_anonymized") WHERE (is_anonymized = false);


-- ###########################################################################
-- §12  LAYER 2 — Foreign keys (259 total, every one ON DELETE RESTRICT)
-- ###########################################################################
--
-- v2 Layer 6 states: "Every ADD CONSTRAINT lands NOT VALID, validated in a
-- later script — no ACCESS EXCLUSIVE lock on payments during clinic hours."
--
-- That rule is NOT applied here, deliberately. It exists to protect a POPULATED
-- production table from a validating lock during clinic hours. This file builds
-- an empty database from scratch (§header): every table it constrains has zero
-- rows at the moment the constraint lands, so validation is instantaneous and
-- NOT VALID would only leave the constraint permanently unvalidated — strictly
-- worse, and invisible until someone queries pg_constraint.convalidated.
--
-- The rule DOES apply when any of these constraints is instead applied to the
-- live database as a migration. In that case each ADD CONSTRAINT below must be
-- split into:
--     ALTER TABLE ... ADD CONSTRAINT ... NOT VALID;
--     -- (separate script, off-hours)
--     ALTER TABLE ... VALIDATE CONSTRAINT ...;
-- This is a property of the deployment path, not of the constraint, which is
-- why it is recorded here rather than baked into the DDL.

-- ---- existing (188) ----

-- 188 foreign keys. ON DELETE RESTRICT everywhere — never CASCADE on clinical/financial data.
-- 9 *_id columns deliberately excluded (polymorphic / external / correlation IDs):
--   activity_logs.entity_id — polymorphic: varies by entity_type
--   notifications.entity_id — polymorphic: varies by entity_type
--   audit_logs.record_id — polymorphic: text, varies by table_name (every audited table)
--   staff_requests.target_staff_id — polymorphic: varies by position_role (doctors/CAs/receptionists)
--   outbox_events.aggregate_id — polymorphic: text, varies by aggregate_type
--   activity_logs.request_id — not a reference: HTTP request correlation ID for tracing
--   audit_logs.request_id — not a reference: HTTP request correlation ID for tracing
--   payments.razorpay_order_id — not a reference: external Razorpay gateway ID, not an internal FK
--   payments.razorpay_payment_id — not a reference: external Razorpay gateway ID, not an internal FK

ALTER TABLE compliance."activity_logs" ADD CONSTRAINT "fk_activity_logs_actor_id" FOREIGN KEY ("actor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."activity_logs" ADD CONSTRAINT "fk_activity_logs_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE compliance."activity_logs" ADD CONSTRAINT "fk_activity_logs_region_id" FOREIGN KEY ("region_id") REFERENCES core."regions" ("region_id") ON DELETE RESTRICT;
ALTER TABLE core."admins" ADD CONSTRAINT "fk_admins_profile_id" FOREIGN KEY ("profile_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."admins" ADD CONSTRAINT "fk_admins_region_id" FOREIGN KEY ("region_id") REFERENCES core."regions" ("region_id") ON DELETE RESTRICT;
ALTER TABLE core."admins" ADD CONSTRAINT "fk_admins_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."anamnesis_assessments" ADD CONSTRAINT "fk_anamnesis_assessments_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."anamnesis_assessments" ADD CONSTRAINT "fk_anamnesis_assessments_submitted_by" FOREIGN KEY ("submitted_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."anamnesis_assessments" ADD CONSTRAINT "fk_anamnesis_assessments_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES core."treatment_cycles" ("cycle_id") ON DELETE RESTRICT;
ALTER TABLE reference."anamnesis_options" ADD CONSTRAINT "fk_anamnesis_options_question_id" FOREIGN KEY ("question_id") REFERENCES reference."anamnesis_questions" ("question_id") ON DELETE RESTRICT;
ALTER TABLE reference."anamnesis_questions" ADD CONSTRAINT "fk_anamnesis_questions_depends_on_question_id" FOREIGN KEY ("depends_on_question_id") REFERENCES reference."anamnesis_questions" ("question_id") ON DELETE RESTRICT;
ALTER TABLE core."anamnesis_responses" ADD CONSTRAINT "fk_anamnesis_responses_anamnesis_id" FOREIGN KEY ("anamnesis_id") REFERENCES core."anamnesis_assessments" ("anamnesis_id") ON DELETE RESTRICT;
ALTER TABLE core."anamnesis_responses" ADD CONSTRAINT "fk_anamnesis_responses_question_id" FOREIGN KEY ("question_id") REFERENCES reference."anamnesis_questions" ("question_id") ON DELETE RESTRICT;
ALTER TABLE core."appointment_audit_logs" ADD CONSTRAINT "fk_appointment_audit_logs_appointment_id" FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT;
ALTER TABLE core."appointment_audit_logs" ADD CONSTRAINT "fk_appointment_audit_logs_changed_by" FOREIGN KEY ("changed_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."appointment_requests" ADD CONSTRAINT "fk_appointment_requests_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."appointment_requests" ADD CONSTRAINT "fk_appointment_requests_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."appointment_requests" ADD CONSTRAINT "fk_appointment_requests_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."appointment_requests" ADD CONSTRAINT "fk_appointment_requests_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES core."treatment_cycles" ("cycle_id") ON DELETE RESTRICT;
ALTER TABLE core."appointment_requests" ADD CONSTRAINT "fk_appointment_requests_parent_appointment_id" FOREIGN KEY ("parent_appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT;
ALTER TABLE core."appointment_requests" ADD CONSTRAINT "fk_appointment_requests_approved_appointment_id" FOREIGN KEY ("approved_appointment_id") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT;
ALTER TABLE core."appointment_requests" ADD CONSTRAINT "fk_appointment_requests_submitted_by" FOREIGN KEY ("submitted_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."appointment_requests" ADD CONSTRAINT "fk_appointment_requests_reviewed_by" FOREIGN KEY ("reviewed_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."appointments" ADD CONSTRAINT "fk_appointments_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."appointments" ADD CONSTRAINT "fk_appointments_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."appointments" ADD CONSTRAINT "fk_appointments_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."appointments" ADD CONSTRAINT "fk_appointments_ca_id" FOREIGN KEY ("ca_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."appointments" ADD CONSTRAINT "fk_appointments_session_id" FOREIGN KEY ("session_id") REFERENCES core."sessions" ("session_id") ON DELETE RESTRICT;
ALTER TABLE core."appointments" ADD CONSTRAINT "fk_appointments_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES core."treatment_cycles" ("cycle_id") ON DELETE RESTRICT;
ALTER TABLE core."appointments" ADD CONSTRAINT "fk_appointments_appointment_request_id" FOREIGN KEY ("appointment_request_id") REFERENCES core."appointment_requests" ("request_id") ON DELETE RESTRICT;
ALTER TABLE core."appointments" ADD CONSTRAINT "fk_appointments_booked_by" FOREIGN KEY ("booked_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."appointments" ADD CONSTRAINT "fk_appointments_cancelled_by" FOREIGN KEY ("cancelled_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."appointments" ADD CONSTRAINT "fk_appointments_rescheduled_from" FOREIGN KEY ("rescheduled_from") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT;
ALTER TABLE core."appointments" ADD CONSTRAINT "fk_appointments_rescheduled_to" FOREIGN KEY ("rescheduled_to") REFERENCES core."appointments" ("appointment_id") ON DELETE RESTRICT;
ALTER TABLE core."assessment_protocol_requests" ADD CONSTRAINT "fk_assessment_protocol_requests_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."assessment_protocol_requests" ADD CONSTRAINT "fk_assessment_protocol_requests_clinical_assistant_id" FOREIGN KEY ("clinical_assistant_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."assessment_protocol_requests" ADD CONSTRAINT "fk_assessment_protocol_requests_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."assessment_protocol_requests" ADD CONSTRAINT "fk_assessment_protocol_requests_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."assessment_protocol_requests" ADD CONSTRAINT "fk_assessment_protocol_requests_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES core."treatment_cycles" ("cycle_id") ON DELETE RESTRICT;
ALTER TABLE compliance."audit_logs" ADD CONSTRAINT "fk_audit_logs_changed_by" FOREIGN KEY ("changed_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."audit_logs" ADD CONSTRAINT "fk_audit_logs_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."ca_doctor_assignments" ADD CONSTRAINT "fk_ca_doctor_assignments_ca_id" FOREIGN KEY ("ca_id") REFERENCES core."clinical_assistants" ("ca_id") ON DELETE RESTRICT;
ALTER TABLE core."ca_doctor_assignments" ADD CONSTRAINT "fk_ca_doctor_assignments_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES core."doctors" ("doctor_id") ON DELETE RESTRICT;
ALTER TABLE core."ca_doctor_assignments" ADD CONSTRAINT "fk_ca_doctor_assignments_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."clinic_requests" ADD CONSTRAINT "fk_clinic_requests_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."clinic_requests" ADD CONSTRAINT "fk_clinic_requests_region_id" FOREIGN KEY ("region_id") REFERENCES core."regions" ("region_id") ON DELETE RESTRICT;
ALTER TABLE core."clinic_requests" ADD CONSTRAINT "fk_clinic_requests_submitted_by" FOREIGN KEY ("submitted_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."clinic_requests" ADD CONSTRAINT "fk_clinic_requests_reviewed_by" FOREIGN KEY ("reviewed_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."clinic_staff_assignments" ADD CONSTRAINT "fk_clinic_staff_assignments_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."clinic_staff_assignments" ADD CONSTRAINT "fk_clinic_staff_assignments_profile_id" FOREIGN KEY ("profile_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."clinical_assistants" ADD CONSTRAINT "fk_clinical_assistants_profile_id" FOREIGN KEY ("profile_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."clinical_assistants" ADD CONSTRAINT "fk_clinical_assistants_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."clinical_assistants" ADD CONSTRAINT "fk_clinical_assistants_deleted_by" FOREIGN KEY ("deleted_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."clinics" ADD CONSTRAINT "fk_clinics_region_id" FOREIGN KEY ("region_id") REFERENCES core."regions" ("region_id") ON DELETE RESTRICT;
ALTER TABLE core."clinics" ADD CONSTRAINT "fk_clinics_clinic_admin_id" FOREIGN KEY ("clinic_admin_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."consent_records" ADD CONSTRAINT "fk_consent_records_template_id" FOREIGN KEY ("template_id") REFERENCES reference."consent_templates" ("template_id") ON DELETE RESTRICT;
ALTER TABLE compliance."consent_records" ADD CONSTRAINT "fk_consent_records_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."consent_records" ADD CONSTRAINT "fk_consent_records_staff_id" FOREIGN KEY ("staff_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."consent_records" ADD CONSTRAINT "fk_consent_records_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE compliance."consent_records" ADD CONSTRAINT "fk_consent_records_signed_by" FOREIGN KEY ("signed_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."consent_records" ADD CONSTRAINT "fk_consent_records_witness_id" FOREIGN KEY ("witness_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."consent_records" ADD CONSTRAINT "fk_consent_records_revoked_by" FOREIGN KEY ("revoked_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."consent_records" ADD CONSTRAINT "fk_consent_records_region_id" FOREIGN KEY ("region_id") REFERENCES core."regions" ("region_id") ON DELETE RESTRICT;
ALTER TABLE core."device_assignments" ADD CONSTRAINT "fk_device_assignments_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."device_assignments" ADD CONSTRAINT "fk_device_assignments_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."device_assignments" ADD CONSTRAINT "fk_device_assignments_plan_id" FOREIGN KEY ("plan_id") REFERENCES core."treatment_plans" ("plan_id") ON DELETE RESTRICT;
ALTER TABLE core."device_assignments" ADD CONSTRAINT "fk_device_assignments_assigned_by" FOREIGN KEY ("assigned_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."device_assignments" ADD CONSTRAINT "fk_device_assignments_order_id" FOREIGN KEY ("order_id") REFERENCES core."store_orders" ("order_id") ON DELETE RESTRICT;
ALTER TABLE core."device_assignments" ADD CONSTRAINT "fk_device_assignments_returned_by" FOREIGN KEY ("returned_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."doctor_patient_assignments" ADD CONSTRAINT "fk_doctor_patient_assignments_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."doctor_patient_assignments" ADD CONSTRAINT "fk_doctor_patient_assignments_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."doctor_patient_assignments" ADD CONSTRAINT "fk_doctor_patient_assignments_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."doctor_schedule_overrides" ADD CONSTRAINT "fk_doctor_schedule_overrides_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."doctor_schedule_overrides" ADD CONSTRAINT "fk_doctor_schedule_overrides_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."doctor_schedule_overrides" ADD CONSTRAINT "fk_doctor_schedule_overrides_created_by" FOREIGN KEY ("created_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."doctor_session_notes" ADD CONSTRAINT "fk_doctor_session_notes_session_id" FOREIGN KEY ("session_id") REFERENCES core."sessions" ("session_id") ON DELETE RESTRICT;
ALTER TABLE core."doctor_session_notes" ADD CONSTRAINT "fk_doctor_session_notes_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES core."treatment_cycles" ("cycle_id") ON DELETE RESTRICT;
ALTER TABLE core."doctor_session_notes" ADD CONSTRAINT "fk_doctor_session_notes_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."doctor_session_notes" ADD CONSTRAINT "fk_doctor_session_notes_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."doctor_weekly_schedules" ADD CONSTRAINT "fk_doctor_weekly_schedules_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."doctor_weekly_schedules" ADD CONSTRAINT "fk_doctor_weekly_schedules_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."doctor_weekly_schedules" ADD CONSTRAINT "fk_doctor_weekly_schedules_created_by" FOREIGN KEY ("created_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."doctors" ADD CONSTRAINT "fk_doctors_profile_id" FOREIGN KEY ("profile_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."doctors" ADD CONSTRAINT "fk_doctors_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."doctors" ADD CONSTRAINT "fk_doctors_deleted_by" FOREIGN KEY ("deleted_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."inventory" ADD CONSTRAINT "fk_inventory_product_id" FOREIGN KEY ("product_id") REFERENCES reference."products" ("product_id") ON DELETE RESTRICT;
ALTER TABLE core."inventory" ADD CONSTRAINT "fk_inventory_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."notifications" ADD CONSTRAINT "fk_notifications_recipient_id" FOREIGN KEY ("recipient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."notifications" ADD CONSTRAINT "fk_notifications_sender_id" FOREIGN KEY ("sender_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."notifications" ADD CONSTRAINT "fk_notifications_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."order_items" ADD CONSTRAINT "fk_order_items_order_id" FOREIGN KEY ("order_id") REFERENCES core."store_orders" ("order_id") ON DELETE RESTRICT;
ALTER TABLE core."order_items" ADD CONSTRAINT "fk_order_items_product_id" FOREIGN KEY ("product_id") REFERENCES reference."products" ("product_id") ON DELETE RESTRICT;
ALTER TABLE core."patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_from_clinic_id" FOREIGN KEY ("from_clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_to_clinic_id" FOREIGN KEY ("to_clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_from_doctor_id" FOREIGN KEY ("from_doctor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_to_doctor_id" FOREIGN KEY ("to_doctor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_active_cycle_id" FOREIGN KEY ("active_cycle_id") REFERENCES core."treatment_cycles" ("cycle_id") ON DELETE RESTRICT;
ALTER TABLE core."patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_consent_id" FOREIGN KEY ("consent_id") REFERENCES compliance."consent_records" ("consent_id") ON DELETE RESTRICT;
ALTER TABLE core."patient_clinic_transfers" ADD CONSTRAINT "fk_patient_clinic_transfers_initiated_by" FOREIGN KEY ("initiated_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patient_disease_selection" ADD CONSTRAINT "fk_patient_disease_selection_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patient_disease_selection" ADD CONSTRAINT "fk_patient_disease_selection_disease_id" FOREIGN KEY ("disease_id") REFERENCES reference."prs_diseases" ("disease_id") ON DELETE RESTRICT;
ALTER TABLE core."patient_eeg_files" ADD CONSTRAINT "fk_patient_eeg_files_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patient_eeg_files" ADD CONSTRAINT "fk_patient_eeg_files_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."patient_eeg_files" ADD CONSTRAINT "fk_patient_eeg_files_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES core."treatment_cycles" ("cycle_id") ON DELETE RESTRICT;
ALTER TABLE core."patient_eeg_files" ADD CONSTRAINT "fk_patient_eeg_files_session_id" FOREIGN KEY ("session_id") REFERENCES core."sessions" ("session_id") ON DELETE RESTRICT;
ALTER TABLE core."patient_eeg_files" ADD CONSTRAINT "fk_patient_eeg_files_performed_by" FOREIGN KEY ("performed_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patient_eeg_files" ADD CONSTRAINT "fk_patient_eeg_files_reviewed_by" FOREIGN KEY ("reviewed_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patient_eeg_files" ADD CONSTRAINT "fk_patient_eeg_files_superseded_by" FOREIGN KEY ("superseded_by") REFERENCES core."patient_eeg_files" ("eeg_id") ON DELETE RESTRICT;
ALTER TABLE core."patient_medical_history_files" ADD CONSTRAINT "fk_patient_medical_history_files_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patient_medical_history_files" ADD CONSTRAINT "fk_patient_medical_history_files_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."patient_medical_history_files" ADD CONSTRAINT "fk_patient_medical_history_files_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES core."treatment_cycles" ("cycle_id") ON DELETE RESTRICT;
ALTER TABLE core."patient_medical_history_files" ADD CONSTRAINT "fk_patient_medical_history_files_uploaded_by" FOREIGN KEY ("uploaded_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patient_medical_history_files" ADD CONSTRAINT "fk_patient_medical_history_files_deleted_by" FOREIGN KEY ("deleted_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patient_scale_assignments" ADD CONSTRAINT "fk_patient_scale_assignments_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patient_scale_assignments" ADD CONSTRAINT "fk_patient_scale_assignments_scale_id" FOREIGN KEY ("scale_id") REFERENCES reference."prs_scales" ("scale_id") ON DELETE RESTRICT;
ALTER TABLE core."patient_scale_assignments" ADD CONSTRAINT "fk_patient_scale_assignments_assigned_by" FOREIGN KEY ("assigned_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patient_scale_assignments" ADD CONSTRAINT "fk_patient_scale_assignments_deactivated_by" FOREIGN KEY ("deactivated_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patient_scale_assignments" ADD CONSTRAINT "fk_patient_scale_assignments_disease_id" FOREIGN KEY ("disease_id") REFERENCES reference."prs_diseases" ("disease_id") ON DELETE RESTRICT;
ALTER TABLE core."patients" ADD CONSTRAINT "fk_patients_profile_id" FOREIGN KEY ("profile_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patients" ADD CONSTRAINT "fk_patients_primary_clinic_id" FOREIGN KEY ("primary_clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."patients" ADD CONSTRAINT "fk_patients_primary_doctor_id" FOREIGN KEY ("primary_doctor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patients" ADD CONSTRAINT "fk_patients_deleted_by" FOREIGN KEY ("deleted_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."patients" ADD CONSTRAINT "fk_patients_approved_by" FOREIGN KEY ("approved_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."payments" ADD CONSTRAINT "fk_payments_session_id" FOREIGN KEY ("session_id") REFERENCES core."sessions" ("session_id") ON DELETE RESTRICT;
ALTER TABLE core."payments" ADD CONSTRAINT "fk_payments_order_id" FOREIGN KEY ("order_id") REFERENCES core."store_orders" ("order_id") ON DELETE RESTRICT;
ALTER TABLE core."payments" ADD CONSTRAINT "fk_payments_waived_by" FOREIGN KEY ("waived_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."profiles" ADD CONSTRAINT "fk_profiles_deleted_by" FOREIGN KEY ("deleted_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."prs_assessment_instances" ADD CONSTRAINT "fk_prs_assessment_instances_disease_id" FOREIGN KEY ("disease_id") REFERENCES reference."prs_diseases" ("disease_id") ON DELETE RESTRICT;
ALTER TABLE core."prs_assessment_instances" ADD CONSTRAINT "fk_prs_assessment_instances_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."prs_assessment_instances" ADD CONSTRAINT "fk_prs_assessment_instances_session_id" FOREIGN KEY ("session_id") REFERENCES core."sessions" ("session_id") ON DELETE RESTRICT;
ALTER TABLE core."prs_assessment_instances" ADD CONSTRAINT "fk_prs_assessment_instances_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES core."treatment_cycles" ("cycle_id") ON DELETE RESTRICT;
ALTER TABLE core."prs_assessment_instances" ADD CONSTRAINT "fk_prs_assessment_instances_administered_by" FOREIGN KEY ("administered_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE reference."prs_disease_question_map" ADD CONSTRAINT "fk_prs_disease_question_map_disease_id" FOREIGN KEY ("disease_id") REFERENCES reference."prs_diseases" ("disease_id") ON DELETE RESTRICT;
ALTER TABLE reference."prs_disease_question_map" ADD CONSTRAINT "fk_prs_disease_question_map_question_id" FOREIGN KEY ("question_id") REFERENCES reference."prs_questions" ("question_id") ON DELETE RESTRICT;
ALTER TABLE reference."prs_disease_scale_map" ADD CONSTRAINT "fk_prs_disease_scale_map_disease_id" FOREIGN KEY ("disease_id") REFERENCES reference."prs_diseases" ("disease_id") ON DELETE RESTRICT;
ALTER TABLE reference."prs_disease_scale_map" ADD CONSTRAINT "fk_prs_disease_scale_map_scale_id" FOREIGN KEY ("scale_id") REFERENCES reference."prs_scales" ("scale_id") ON DELETE RESTRICT;
ALTER TABLE core."prs_final_results" ADD CONSTRAINT "fk_prs_final_results_instance_id" FOREIGN KEY ("instance_id") REFERENCES core."prs_assessment_instances" ("instance_id") ON DELETE RESTRICT;
ALTER TABLE reference."prs_option_translations" ADD CONSTRAINT "fk_prs_option_translations_option_id" FOREIGN KEY ("option_id") REFERENCES reference."prs_options" ("option_id") ON DELETE RESTRICT;
ALTER TABLE reference."prs_options" ADD CONSTRAINT "fk_prs_options_question_id" FOREIGN KEY ("question_id") REFERENCES reference."prs_questions" ("question_id") ON DELETE RESTRICT;
ALTER TABLE reference."prs_question_translations" ADD CONSTRAINT "fk_prs_question_translations_question_id" FOREIGN KEY ("question_id") REFERENCES reference."prs_questions" ("question_id") ON DELETE RESTRICT;
ALTER TABLE reference."prs_questions" ADD CONSTRAINT "fk_prs_questions_disease_id" FOREIGN KEY ("disease_id") REFERENCES reference."prs_diseases" ("disease_id") ON DELETE RESTRICT;
ALTER TABLE reference."prs_questions" ADD CONSTRAINT "fk_prs_questions_scale_id" FOREIGN KEY ("scale_id") REFERENCES reference."prs_scales" ("scale_id") ON DELETE RESTRICT;
ALTER TABLE reference."prs_questions" ADD CONSTRAINT "fk_prs_questions_ds_map_id" FOREIGN KEY ("ds_map_id") REFERENCES reference."prs_disease_scale_map" ("ds_map_id") ON DELETE RESTRICT;
ALTER TABLE core."prs_responses" ADD CONSTRAINT "fk_prs_responses_instance_id" FOREIGN KEY ("instance_id") REFERENCES core."prs_assessment_instances" ("instance_id") ON DELETE RESTRICT;
ALTER TABLE core."prs_responses" ADD CONSTRAINT "fk_prs_responses_question_id" FOREIGN KEY ("question_id") REFERENCES reference."prs_questions" ("question_id") ON DELETE RESTRICT;
ALTER TABLE reference."prs_scale_question_map" ADD CONSTRAINT "fk_prs_scale_question_map_scale_id" FOREIGN KEY ("scale_id") REFERENCES reference."prs_scales" ("scale_id") ON DELETE RESTRICT;
ALTER TABLE reference."prs_scale_question_map" ADD CONSTRAINT "fk_prs_scale_question_map_question_id" FOREIGN KEY ("question_id") REFERENCES reference."prs_questions" ("question_id") ON DELETE RESTRICT;
ALTER TABLE core."prs_scale_results" ADD CONSTRAINT "fk_prs_scale_results_instance_id" FOREIGN KEY ("instance_id") REFERENCES core."prs_assessment_instances" ("instance_id") ON DELETE RESTRICT;
ALTER TABLE core."prs_scale_results" ADD CONSTRAINT "fk_prs_scale_results_scale_id" FOREIGN KEY ("scale_id") REFERENCES reference."prs_scales" ("scale_id") ON DELETE RESTRICT;
ALTER TABLE core."receptionists" ADD CONSTRAINT "fk_receptionists_profile_id" FOREIGN KEY ("profile_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."receptionists" ADD CONSTRAINT "fk_receptionists_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."receptionists" ADD CONSTRAINT "fk_receptionists_deleted_by" FOREIGN KEY ("deleted_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."regions" ADD CONSTRAINT "fk_regions_regional_admin_id" FOREIGN KEY ("regional_admin_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."sessions" ADD CONSTRAINT "fk_sessions_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."sessions" ADD CONSTRAINT "fk_sessions_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."sessions" ADD CONSTRAINT "fk_sessions_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES core."treatment_cycles" ("cycle_id") ON DELETE RESTRICT;
ALTER TABLE core."sessions" ADD CONSTRAINT "fk_sessions_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."sessions" ADD CONSTRAINT "fk_sessions_ca_id" FOREIGN KEY ("ca_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."staff_requests" ADD CONSTRAINT "fk_staff_requests_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."staff_requests" ADD CONSTRAINT "fk_staff_requests_regional_admin_id" FOREIGN KEY ("regional_admin_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."staff_requests" ADD CONSTRAINT "fk_staff_requests_submitted_by" FOREIGN KEY ("submitted_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."staff_requests" ADD CONSTRAINT "fk_staff_requests_reviewed_by" FOREIGN KEY ("reviewed_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."staff_requests" ADD CONSTRAINT "fk_staff_requests_fulfilled_profile_id" FOREIGN KEY ("fulfilled_profile_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."stock_transfers" ADD CONSTRAINT "fk_stock_transfers_product_id" FOREIGN KEY ("product_id") REFERENCES reference."products" ("product_id") ON DELETE RESTRICT;
ALTER TABLE core."stock_transfers" ADD CONSTRAINT "fk_stock_transfers_from_clinic_id" FOREIGN KEY ("from_clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."stock_transfers" ADD CONSTRAINT "fk_stock_transfers_to_clinic_id" FOREIGN KEY ("to_clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."stock_transfers" ADD CONSTRAINT "fk_stock_transfers_order_id" FOREIGN KEY ("order_id") REFERENCES core."store_orders" ("order_id") ON DELETE RESTRICT;
ALTER TABLE core."stock_transfers" ADD CONSTRAINT "fk_stock_transfers_initiated_by" FOREIGN KEY ("initiated_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."stock_transfers" ADD CONSTRAINT "fk_stock_transfers_received_by" FOREIGN KEY ("received_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."store_orders" ADD CONSTRAINT "fk_store_orders_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."store_orders" ADD CONSTRAINT "fk_store_orders_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."store_orders" ADD CONSTRAINT "fk_store_orders_initiated_by" FOREIGN KEY ("initiated_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."store_orders" ADD CONSTRAINT "fk_store_orders_approved_by" FOREIGN KEY ("approved_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."store_orders" ADD CONSTRAINT "fk_store_orders_treatment_plan_id" FOREIGN KEY ("treatment_plan_id") REFERENCES core."treatment_plans" ("plan_id") ON DELETE RESTRICT;
ALTER TABLE core."store_orders" ADD CONSTRAINT "fk_store_orders_cancelled_by" FOREIGN KEY ("cancelled_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_cycles" ADD CONSTRAINT "fk_treatment_cycles_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_cycles" ADD CONSTRAINT "fk_treatment_cycles_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_cycles" ADD CONSTRAINT "fk_treatment_cycles_ca_id" FOREIGN KEY ("ca_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_cycles" ADD CONSTRAINT "fk_treatment_cycles_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_plans" ADD CONSTRAINT "fk_treatment_plans_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_plans" ADD CONSTRAINT "fk_treatment_plans_doctor_id" FOREIGN KEY ("doctor_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_plans" ADD CONSTRAINT "fk_treatment_plans_cycle_id" FOREIGN KEY ("cycle_id") REFERENCES core."treatment_cycles" ("cycle_id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_plans" ADD CONSTRAINT "fk_treatment_plans_parent_plan_id" FOREIGN KEY ("parent_plan_id") REFERENCES core."treatment_plans" ("plan_id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_sessions" ADD CONSTRAINT "fk_treatment_sessions_plan_id" FOREIGN KEY ("plan_id") REFERENCES core."treatment_plans" ("plan_id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_sessions" ADD CONSTRAINT "fk_treatment_sessions_session_id" FOREIGN KEY ("session_id") REFERENCES core."sessions" ("session_id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_sessions" ADD CONSTRAINT "fk_treatment_sessions_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_sessions" ADD CONSTRAINT "fk_treatment_sessions_ca_id" FOREIGN KEY ("ca_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

-- From file 27 — see the note in §9 for why it lands here.
ALTER TABLE core."patients" ADD CONSTRAINT "fk_patients_registered_by" FOREIGN KEY ("registered_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
-- ---- new: Treatment Protocol module (59) ----

-- reference: shared catalogue
ALTER TABLE reference."neuromod_diagnoses"        ADD CONSTRAINT "fk_neuromod_diagnoses_condition_id"        FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;
ALTER TABLE reference."neuromod_scales"           ADD CONSTRAINT "fk_neuromod_scales_prs_scale_id"           FOREIGN KEY ("prs_scale_id") REFERENCES reference."prs_scales" ("scale_id") ON DELETE RESTRICT;
ALTER TABLE reference."neuromod_condition_scales" ADD CONSTRAINT "fk_neuromod_condition_scales_condition_id" FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;
ALTER TABLE reference."neuromod_condition_scales" ADD CONSTRAINT "fk_neuromod_condition_scales_scale_id"     FOREIGN KEY ("scale_id")     REFERENCES reference."neuromod_scales" ("scale_id")         ON DELETE RESTRICT;

-- reference: per-device placements -> condition + device
ALTER TABLE reference."tdcs_placements"    ADD CONSTRAINT "fk_tdcs_placements_condition_id"    FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;
ALTER TABLE reference."tdcs_placements"    ADD CONSTRAINT "fk_tdcs_placements_device_id"       FOREIGN KEY ("device_id")    REFERENCES reference."neuromod_devices" ("device_id")       ON DELETE RESTRICT;
ALTER TABLE reference."hd_tdcs_placements" ADD CONSTRAINT "fk_hd_tdcs_placements_condition_id" FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;
ALTER TABLE reference."hd_tdcs_placements" ADD CONSTRAINT "fk_hd_tdcs_placements_device_id"    FOREIGN KEY ("device_id")    REFERENCES reference."neuromod_devices" ("device_id")       ON DELETE RESTRICT;
ALTER TABLE reference."tavns_placements"   ADD CONSTRAINT "fk_tavns_placements_condition_id"   FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;
ALTER TABLE reference."tavns_placements"   ADD CONSTRAINT "fk_tavns_placements_device_id"      FOREIGN KEY ("device_id")    REFERENCES reference."neuromod_devices" ("device_id")       ON DELETE RESTRICT;
ALTER TABLE reference."tps_placements"     ADD CONSTRAINT "fk_tps_placements_condition_id"     FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;
ALTER TABLE reference."tps_placements"     ADD CONSTRAINT "fk_tps_placements_device_id"        FOREIGN KEY ("device_id")    REFERENCES reference."neuromod_devices" ("device_id")       ON DELETE RESTRICT;
ALTER TABLE reference."rtms_placements"    ADD CONSTRAINT "fk_rtms_placements_condition_id"    FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;
ALTER TABLE reference."rtms_placements"    ADD CONSTRAINT "fk_rtms_placements_device_id"       FOREIGN KEY ("device_id")    REFERENCES reference."neuromod_devices" ("device_id")       ON DELETE RESTRICT;
ALTER TABLE reference."other_placements"   ADD CONSTRAINT "fk_other_placements_condition_id"   FOREIGN KEY ("condition_id") REFERENCES reference."neuromod_conditions" ("condition_id") ON DELETE RESTRICT;
ALTER TABLE reference."other_placements"   ADD CONSTRAINT "fk_other_placements_device_id"      FOREIGN KEY ("device_id")    REFERENCES reference."neuromod_devices" ("device_id")       ON DELETE RESTRICT;

-- reference: per-device dosing -> condition + device + its own placement table
ALTER TABLE reference."tdcs_dosing"    ADD CONSTRAINT "fk_tdcs_dosing_condition_id"        FOREIGN KEY ("condition_id")         REFERENCES reference."neuromod_conditions" ("condition_id")             ON DELETE RESTRICT;
ALTER TABLE reference."tdcs_dosing"    ADD CONSTRAINT "fk_tdcs_dosing_device_id"           FOREIGN KEY ("device_id")            REFERENCES reference."neuromod_devices" ("device_id")                   ON DELETE RESTRICT;
ALTER TABLE reference."tdcs_dosing"    ADD CONSTRAINT "fk_tdcs_dosing_tdcs_placement_id"   FOREIGN KEY ("tdcs_placement_id")    REFERENCES reference."tdcs_placements" ("tdcs_placement_id")           ON DELETE RESTRICT;
ALTER TABLE reference."hd_tdcs_dosing" ADD CONSTRAINT "fk_hd_tdcs_dosing_condition_id"     FOREIGN KEY ("condition_id")         REFERENCES reference."neuromod_conditions" ("condition_id")             ON DELETE RESTRICT;
ALTER TABLE reference."hd_tdcs_dosing" ADD CONSTRAINT "fk_hd_tdcs_dosing_device_id"        FOREIGN KEY ("device_id")            REFERENCES reference."neuromod_devices" ("device_id")                   ON DELETE RESTRICT;
ALTER TABLE reference."hd_tdcs_dosing" ADD CONSTRAINT "fk_hd_tdcs_dosing_placement_id"     FOREIGN KEY ("hd_tdcs_placement_id") REFERENCES reference."hd_tdcs_placements" ("hd_tdcs_placement_id")     ON DELETE RESTRICT;
ALTER TABLE reference."tavns_dosing"   ADD CONSTRAINT "fk_tavns_dosing_condition_id"       FOREIGN KEY ("condition_id")         REFERENCES reference."neuromod_conditions" ("condition_id")             ON DELETE RESTRICT;
ALTER TABLE reference."tavns_dosing"   ADD CONSTRAINT "fk_tavns_dosing_device_id"          FOREIGN KEY ("device_id")            REFERENCES reference."neuromod_devices" ("device_id")                   ON DELETE RESTRICT;
ALTER TABLE reference."tavns_dosing"   ADD CONSTRAINT "fk_tavns_dosing_placement_id"       FOREIGN KEY ("tavns_placement_id")   REFERENCES reference."tavns_placements" ("tavns_placement_id")         ON DELETE RESTRICT;
ALTER TABLE reference."tps_dosing"     ADD CONSTRAINT "fk_tps_dosing_condition_id"         FOREIGN KEY ("condition_id")         REFERENCES reference."neuromod_conditions" ("condition_id")             ON DELETE RESTRICT;
ALTER TABLE reference."tps_dosing"     ADD CONSTRAINT "fk_tps_dosing_device_id"            FOREIGN KEY ("device_id")            REFERENCES reference."neuromod_devices" ("device_id")                   ON DELETE RESTRICT;
ALTER TABLE reference."tps_dosing"     ADD CONSTRAINT "fk_tps_dosing_placement_id"         FOREIGN KEY ("tps_placement_id")     REFERENCES reference."tps_placements" ("tps_placement_id")             ON DELETE RESTRICT;
ALTER TABLE reference."rtms_dosing"    ADD CONSTRAINT "fk_rtms_dosing_condition_id"        FOREIGN KEY ("condition_id")         REFERENCES reference."neuromod_conditions" ("condition_id")             ON DELETE RESTRICT;
ALTER TABLE reference."rtms_dosing"    ADD CONSTRAINT "fk_rtms_dosing_device_id"           FOREIGN KEY ("device_id")            REFERENCES reference."neuromod_devices" ("device_id")                   ON DELETE RESTRICT;
ALTER TABLE reference."rtms_dosing"    ADD CONSTRAINT "fk_rtms_dosing_placement_id"        FOREIGN KEY ("rtms_placement_id")    REFERENCES reference."rtms_placements" ("rtms_placement_id")           ON DELETE RESTRICT;
ALTER TABLE reference."other_dosing"   ADD CONSTRAINT "fk_other_dosing_condition_id"       FOREIGN KEY ("condition_id")         REFERENCES reference."neuromod_conditions" ("condition_id")             ON DELETE RESTRICT;
ALTER TABLE reference."other_dosing"   ADD CONSTRAINT "fk_other_dosing_device_id"          FOREIGN KEY ("device_id")            REFERENCES reference."neuromod_devices" ("device_id")                   ON DELETE RESTRICT;
ALTER TABLE reference."other_dosing"   ADD CONSTRAINT "fk_other_dosing_placement_id"       FOREIGN KEY ("other_placement_id")   REFERENCES reference."other_placements" ("other_placement_id")         ON DELETE RESTRICT;

-- core: treatment_protocols -> existing tables
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_plan_id"   FOREIGN KEY ("plan_id")   REFERENCES core."treatment_plans" ("plan_id")         ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_device_id" FOREIGN KEY ("device_id") REFERENCES reference."neuromod_devices" ("device_id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_set_by"    FOREIGN KEY ("set_by")    REFERENCES core."profiles" ("id")                      ON DELETE RESTRICT;

-- core: treatment_protocols -> the six placement tables
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tdcs_placement_id"    FOREIGN KEY ("tdcs_placement_id")    REFERENCES reference."tdcs_placements" ("tdcs_placement_id")       ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_hd_tdcs_placement_id" FOREIGN KEY ("hd_tdcs_placement_id") REFERENCES reference."hd_tdcs_placements" ("hd_tdcs_placement_id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tavns_placement_id"   FOREIGN KEY ("tavns_placement_id")   REFERENCES reference."tavns_placements" ("tavns_placement_id")     ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tps_placement_id"     FOREIGN KEY ("tps_placement_id")     REFERENCES reference."tps_placements" ("tps_placement_id")         ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_rtms_placement_id"    FOREIGN KEY ("rtms_placement_id")    REFERENCES reference."rtms_placements" ("rtms_placement_id")       ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_other_placement_id"   FOREIGN KEY ("other_placement_id")   REFERENCES reference."other_placements" ("other_placement_id")     ON DELETE RESTRICT;

-- core: treatment_protocols -> the six dosing tables
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tdcs_dosing_id"    FOREIGN KEY ("tdcs_dosing_id")    REFERENCES reference."tdcs_dosing" ("tdcs_dosing_id")       ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_hd_tdcs_dosing_id" FOREIGN KEY ("hd_tdcs_dosing_id") REFERENCES reference."hd_tdcs_dosing" ("hd_tdcs_dosing_id") ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tavns_dosing_id"   FOREIGN KEY ("tavns_dosing_id")   REFERENCES reference."tavns_dosing" ("tavns_dosing_id")     ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_tps_dosing_id"     FOREIGN KEY ("tps_dosing_id")     REFERENCES reference."tps_dosing" ("tps_dosing_id")         ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_rtms_dosing_id"    FOREIGN KEY ("rtms_dosing_id")    REFERENCES reference."rtms_dosing" ("rtms_dosing_id")       ON DELETE RESTRICT;
ALTER TABLE core."treatment_protocols" ADD CONSTRAINT "fk_treatment_protocols_other_dosing_id"   FOREIGN KEY ("other_dosing_id")   REFERENCES reference."other_dosing" ("other_dosing_id")     ON DELETE RESTRICT;

-- core: generated sessions and follow-ups
ALTER TABLE core."protocol_sessions"  ADD CONSTRAINT "fk_protocol_sessions_protocol_id"     FOREIGN KEY ("protocol_id")    REFERENCES core."treatment_protocols" ("protocol_id") ON DELETE RESTRICT;
-- NOTE: protocol_sessions deliberately has NO foreign key to a scheduling table.
-- Architecture v2 separates appointments (doctor consultations) from
-- treatment_sessions (tDCS), and the session schema is not finalised. When it
-- is, the binding is one column plus one constraint:
--     ALTER TABLE core."protocol_sessions" ADD COLUMN "<session_ref>" ...;
--     ALTER TABLE core."protocol_sessions" ADD CONSTRAINT
--         "fk_protocol_sessions_session" FOREIGN KEY (...) REFERENCES ... ON DELETE RESTRICT;
-- (If it binds to the partitioned treatment_sessions, that FK is composite —
-- (ts_id, created_at) — which is precisely why guessing it now was the wrong move.)
ALTER TABLE core."protocol_followups" ADD CONSTRAINT "fk_protocol_followups_protocol_id"    FOREIGN KEY ("protocol_id")    REFERENCES core."treatment_protocols" ("protocol_id") ON DELETE RESTRICT;
ALTER TABLE core."protocol_followups" ADD CONSTRAINT "fk_protocol_followups_appointment_id" FOREIGN KEY ("appointment_id") REFERENCES core."appointments" ("appointment_id")     ON DELETE RESTRICT;

-- core: PRS capture
ALTER TABLE core."device_session_prs_responses" ADD CONSTRAINT "fk_ds_prs_protocol_session_id"  FOREIGN KEY ("protocol_session_id")  REFERENCES core."protocol_sessions" ("protocol_session_id")         ON DELETE RESTRICT;
ALTER TABLE core."device_session_prs_responses" ADD CONSTRAINT "fk_ds_prs_instance_id"          FOREIGN KEY ("instance_id")          REFERENCES core."prs_assessment_instances" ("instance_id")        ON DELETE RESTRICT;
ALTER TABLE core."device_session_prs_responses" ADD CONSTRAINT "fk_ds_prs_patient_id"           FOREIGN KEY ("patient_id")           REFERENCES core."profiles" ("id")                                 ON DELETE RESTRICT;
ALTER TABLE core."followup_prs_responses"       ADD CONSTRAINT "fk_fu_prs_protocol_followup_id" FOREIGN KEY ("protocol_followup_id") REFERENCES core."protocol_followups" ("protocol_followup_id")      ON DELETE RESTRICT;
ALTER TABLE core."followup_prs_responses"       ADD CONSTRAINT "fk_fu_prs_instance_id"          FOREIGN KEY ("instance_id")          REFERENCES core."prs_assessment_instances" ("instance_id")        ON DELETE RESTRICT;
ALTER TABLE core."followup_prs_responses"       ADD CONSTRAINT "fk_fu_prs_patient_id"           FOREIGN KEY ("patient_id")           REFERENCES core."profiles" ("id")                                 ON DELETE RESTRICT;


-- ###########################################################################
-- §13  LAYER 2 — Indexes
-- ###########################################################################

-- ---- existing ----

CREATE INDEX idx_actlog_actor_id ON compliance."activity_logs" USING btree (actor_id);
CREATE INDEX idx_actlog_category ON compliance."activity_logs" USING btree (category);
CREATE INDEX idx_actlog_clinic_id ON compliance."activity_logs" USING btree (clinic_id);
CREATE INDEX idx_actlog_created_at_brin ON compliance."activity_logs" USING brin (created_at);
CREATE INDEX idx_actlog_entity_id ON compliance."activity_logs" USING btree (entity_id);
CREATE INDEX idx_actlog_event_type ON compliance."activity_logs" USING btree (event_type);
CREATE INDEX idx_actlog_metadata_gin ON compliance."activity_logs" USING gin (metadata);
CREATE INDEX idx_actlog_region_id ON compliance."activity_logs" USING btree (region_id);
CREATE INDEX idx_actlog_request_id ON compliance."activity_logs" USING btree (request_id);
CREATE INDEX idx_admins_admin_type ON core."admins" USING btree (admin_type);
CREATE INDEX idx_admins_clinic_id ON core."admins" USING btree (clinic_id);
CREATE INDEX idx_admins_region_id ON core."admins" USING btree (region_id);
CREATE UNIQUE INDEX alembic_version_pkc ON ops."alembic_version" USING btree (version_num);
CREATE INDEX idx_anamnesis_cycle_id ON core."anamnesis_assessments" USING btree (cycle_id);
CREATE INDEX idx_anamnesis_patient_id ON core."anamnesis_assessments" USING btree (patient_id);
CREATE INDEX idx_anamnesis_status ON core."anamnesis_assessments" USING btree (status);
CREATE INDEX idx_anamnesis_submitted_by ON core."anamnesis_assessments" USING btree (submitted_by);
CREATE INDEX idx_anao_question_id ON reference."anamnesis_options" USING btree (question_id);
CREATE INDEX idx_anaq_depends_on ON reference."anamnesis_questions" USING btree (depends_on_question_id);
CREATE INDEX idx_anaq_section_number ON reference."anamnesis_questions" USING btree (section_number);
CREATE INDEX idx_anaq_status ON reference."anamnesis_questions" USING btree (status);
CREATE INDEX idx_anar_anamnesis_id ON core."anamnesis_responses" USING btree (anamnesis_id);
CREATE INDEX idx_anar_question_id ON core."anamnesis_responses" USING btree (question_id);
CREATE INDEX idx_apal_appointment_id ON core."appointment_audit_logs" USING btree (appointment_id);
CREATE INDEX idx_apal_changed_at_brin ON core."appointment_audit_logs" USING brin (changed_at);
CREATE INDEX idx_areq_clinic_status ON core."appointment_requests" USING btree (clinic_id, status);
CREATE INDEX idx_areq_doctor_status ON core."appointment_requests" USING btree (doctor_id, status);
CREATE INDEX idx_areq_patient_status ON core."appointment_requests" USING btree (patient_id, status);
CREATE INDEX idx_areq_pref_date1 ON core."appointment_requests" USING btree (preferred_date_1);
CREATE INDEX idx_areq_urgency ON core."appointment_requests" USING btree (urgency) WHERE (urgency = ANY (ARRAY['urgent'::text, 'emergency'::text]));
CREATE INDEX idx_appt_clinic_date_status ON core."appointments" USING btree (clinic_id, appointment_date, status);
CREATE INDEX idx_appt_cycle_id ON core."appointments" USING btree (cycle_id);
CREATE INDEX idx_appt_doctor_date_status ON core."appointments" USING btree (doctor_id, appointment_date, status);
CREATE INDEX idx_appt_patient_date ON core."appointments" USING btree (patient_id, appointment_date DESC);
CREATE INDEX idx_appt_request_id ON core."appointments" USING btree (appointment_request_id);
CREATE INDEX idx_appt_session_id ON core."appointments" USING btree (session_id);
CREATE INDEX idx_appt_status ON core."appointments" USING btree (status);
CREATE INDEX idx_apr_ca_id ON core."assessment_protocol_requests" USING btree (clinical_assistant_id);
CREATE INDEX idx_apr_cycle_id ON core."assessment_protocol_requests" USING btree (cycle_id);
CREATE INDEX idx_apr_doctor_id ON core."assessment_protocol_requests" USING btree (doctor_id);
CREATE INDEX idx_apr_patient_id ON core."assessment_protocol_requests" USING btree (patient_id);
CREATE INDEX idx_apr_status ON core."assessment_protocol_requests" USING btree (status);
CREATE INDEX idx_al_changed_at_brin ON compliance."audit_logs" USING brin (changed_at);
CREATE INDEX idx_al_changed_by ON compliance."audit_logs" USING btree (changed_by);
CREATE INDEX idx_al_operation ON compliance."audit_logs" USING btree (operation);
CREATE INDEX idx_al_record_id ON compliance."audit_logs" USING btree (record_id);
CREATE INDEX idx_al_table_name ON compliance."audit_logs" USING btree (table_name);
CREATE INDEX idx_cda_ca_id ON core."ca_doctor_assignments" USING btree (ca_id);
CREATE INDEX idx_cda_clinic_id ON core."ca_doctor_assignments" USING btree (clinic_id);
CREATE INDEX idx_cda_doctor_id ON core."ca_doctor_assignments" USING btree (doctor_id);
CREATE INDEX idx_clinic_req_clinic_id ON core."clinic_requests" USING btree (clinic_id);
CREATE INDEX idx_clinic_req_payload_gin ON core."clinic_requests" USING gin (payload);
CREATE INDEX idx_clinic_req_pending ON core."clinic_requests" USING btree (region_id, created_at DESC) WHERE (status = 'pending'::text);
CREATE INDEX idx_clinic_req_region_id ON core."clinic_requests" USING btree (region_id);
CREATE INDEX idx_clinic_req_status ON core."clinic_requests" USING btree (status);
CREATE INDEX idx_clinic_req_submitted_by ON core."clinic_requests" USING btree (submitted_by);
CREATE INDEX idx_csa_active ON core."clinic_staff_assignments" USING btree (clinic_id, profile_id) WHERE (is_active = true);
CREATE INDEX idx_csa_clinic_id ON core."clinic_staff_assignments" USING btree (clinic_id);
CREATE INDEX idx_csa_is_active ON core."clinic_staff_assignments" USING btree (is_active);
CREATE INDEX idx_csa_profile_id ON core."clinic_staff_assignments" USING btree (profile_id);
CREATE INDEX idx_csa_staff_role ON core."clinic_staff_assignments" USING btree (staff_role);
CREATE INDEX idx_ca_profile_id ON core."clinical_assistants" USING btree (profile_id);
CREATE INDEX idx_clinics_clinic_admin ON core."clinics" USING btree (clinic_admin_id);
CREATE INDEX idx_clinics_is_main_branch ON core."clinics" USING btree (is_main_branch);
CREATE UNIQUE INDEX idx_clinics_one_main_branch ON core."clinics" USING btree (region_id) WHERE (is_main_branch = true);
CREATE INDEX idx_clinics_region_id ON core."clinics" USING btree (region_id);
CREATE INDEX idx_clinics_status ON core."clinics" USING btree (status);
CREATE INDEX idx_cr_clinic_id ON compliance."consent_records" USING btree (clinic_id);
CREATE INDEX idx_cr_consent_type ON compliance."consent_records" USING btree (consent_type);
CREATE INDEX idx_cr_patient_id ON compliance."consent_records" USING btree (patient_id);
CREATE INDEX idx_cr_staff_id ON compliance."consent_records" USING btree (staff_id);
CREATE INDEX idx_cr_status ON compliance."consent_records" USING btree (status);
CREATE INDEX idx_cr_template_id ON compliance."consent_records" USING btree (template_id);
CREATE INDEX idx_ct_consent_type ON reference."consent_templates" USING btree (consent_type);
CREATE INDEX idx_ct_is_active ON reference."consent_templates" USING btree (is_active);
CREATE INDEX idx_da_clinic_id ON core."device_assignments" USING btree (clinic_id);
CREATE INDEX idx_da_order_id ON core."device_assignments" USING btree (order_id);
CREATE INDEX idx_da_patient_id ON core."device_assignments" USING btree (patient_id);
CREATE INDEX idx_da_plan_id ON core."device_assignments" USING btree (plan_id);
CREATE INDEX idx_da_purchase_status ON core."device_assignments" USING btree (purchase_status);
CREATE INDEX idx_dpa_active_doctor ON core."doctor_patient_assignments" USING btree (doctor_id) WHERE (status = 'active'::text);
CREATE UNIQUE INDEX idx_dpa_active_unique ON core."doctor_patient_assignments" USING btree (doctor_id, patient_id) WHERE (status = 'active'::text);
CREATE INDEX idx_dpa_clinic_id ON core."doctor_patient_assignments" USING btree (clinic_id);
CREATE INDEX idx_dpa_doctor_id ON core."doctor_patient_assignments" USING btree (doctor_id);
CREATE INDEX idx_dpa_patient_id ON core."doctor_patient_assignments" USING btree (patient_id);
CREATE INDEX idx_dpa_status ON core."doctor_patient_assignments" USING btree (status);
CREATE INDEX idx_dso_clinic ON core."doctor_schedule_overrides" USING btree (clinic_id);
CREATE INDEX idx_dso_date ON core."doctor_schedule_overrides" USING btree (override_date);
CREATE INDEX idx_dso_doctor_date ON core."doctor_schedule_overrides" USING btree (doctor_id, override_date);
CREATE INDEX idx_dsn_cycle_id ON core."doctor_session_notes" USING btree (cycle_id);
CREATE INDEX idx_dsn_doctor_id ON core."doctor_session_notes" USING btree (doctor_id);
CREATE INDEX idx_dsn_patient_id ON core."doctor_session_notes" USING btree (patient_id);
CREATE INDEX idx_dsn_patient_session ON core."doctor_session_notes" USING btree (patient_id, session_number);
CREATE INDEX idx_dsn_session_id ON core."doctor_session_notes" USING btree (session_id);
CREATE INDEX idx_dsn_session_phase ON core."doctor_session_notes" USING btree (session_phase);
CREATE INDEX idx_dws_clinic_dow ON core."doctor_weekly_schedules" USING btree (clinic_id, day_of_week);
CREATE INDEX idx_dws_doctor_clinic ON core."doctor_weekly_schedules" USING btree (doctor_id, clinic_id);
CREATE INDEX idx_dws_is_active ON core."doctor_weekly_schedules" USING btree (is_active);
CREATE INDEX idx_doctors_availability_status ON core."doctors" USING btree (availability_status);
CREATE INDEX idx_doctors_clinic_id ON core."doctors" USING btree (clinic_id);
CREATE INDEX idx_doctors_profile_id ON core."doctors" USING btree (profile_id);
CREATE INDEX idx_inventory_clinic_id ON core."inventory" USING btree (clinic_id);
CREATE INDEX idx_inventory_product_id ON core."inventory" USING btree (product_id);
CREATE INDEX idx_notif_clinic_id ON core."notifications" USING btree (clinic_id);
CREATE INDEX idx_notif_created_at ON core."notifications" USING btree (created_at DESC);
CREATE INDEX idx_notif_recipient_id ON core."notifications" USING btree (recipient_id);
CREATE INDEX idx_notif_sender_id ON core."notifications" USING btree (sender_id);
CREATE INDEX idx_notif_type ON core."notifications" USING btree (type);
CREATE INDEX idx_notif_unread ON core."notifications" USING btree (recipient_id, created_at DESC) WHERE (is_read = false);
CREATE INDEX idx_oi_order_id ON core."order_items" USING btree (order_id);
CREATE INDEX idx_oi_product_id ON core."order_items" USING btree (product_id);
CREATE INDEX idx_outbox_unpublished ON ops."outbox_events" USING btree (created_at) WHERE (published_at IS NULL);
CREATE INDEX idx_pct_active_cycle_id ON core."patient_clinic_transfers" USING btree (active_cycle_id);
CREATE INDEX idx_pct_from_clinic_id ON core."patient_clinic_transfers" USING btree (from_clinic_id);
CREATE UNIQUE INDEX idx_pct_no_dup_pending ON core."patient_clinic_transfers" USING btree (patient_id, from_clinic_id, to_clinic_id) WHERE (status = ANY (ARRAY['pending'::text, 'consented'::text]));
CREATE INDEX idx_pct_patient_id ON core."patient_clinic_transfers" USING btree (patient_id);
CREATE INDEX idx_pct_status ON core."patient_clinic_transfers" USING btree (status);
CREATE INDEX idx_pct_to_clinic_id ON core."patient_clinic_transfers" USING btree (to_clinic_id);
CREATE INDEX idx_pct_transfer_reason ON core."patient_clinic_transfers" USING btree (transfer_reason);
CREATE INDEX idx_pds_disease_id ON core."patient_disease_selection" USING btree (disease_id);
CREATE UNIQUE INDEX idx_pds_patient_disease_unique ON core."patient_disease_selection" USING btree (patient_id, disease_id) WHERE (disease_id IS NOT NULL);
CREATE INDEX idx_pds_patient_id ON core."patient_disease_selection" USING btree (patient_id);
CREATE INDEX idx_eeg_clinic_id ON core."patient_eeg_files" USING btree (clinic_id);
CREATE INDEX idx_eeg_cycle_id ON core."patient_eeg_files" USING btree (cycle_id);
CREATE INDEX idx_eeg_eeg_type ON core."patient_eeg_files" USING btree (eeg_type);
CREATE INDEX idx_eeg_is_abnormal ON core."patient_eeg_files" USING btree (is_abnormal);
CREATE INDEX idx_eeg_patient_id ON core."patient_eeg_files" USING btree (patient_id);
CREATE INDEX idx_eeg_performed_at ON core."patient_eeg_files" USING btree (performed_at DESC);
CREATE INDEX idx_eeg_performed_by ON core."patient_eeg_files" USING btree (performed_by);
CREATE INDEX idx_eeg_reviewed_by ON core."patient_eeg_files" USING btree (reviewed_by);
CREATE INDEX idx_eeg_session_id ON core."patient_eeg_files" USING btree (session_id);
CREATE INDEX idx_eeg_status ON core."patient_eeg_files" USING btree (status);
CREATE INDEX idx_mhf_clinic_id ON core."patient_medical_history_files" USING btree (clinic_id);
CREATE INDEX idx_mhf_cycle_id ON core."patient_medical_history_files" USING btree (cycle_id);
CREATE INDEX idx_mhf_document_date ON core."patient_medical_history_files" USING btree (document_date DESC);
CREATE INDEX idx_mhf_document_type ON core."patient_medical_history_files" USING btree (document_type);
CREATE INDEX idx_mhf_is_deleted ON core."patient_medical_history_files" USING btree (is_deleted);
CREATE INDEX idx_mhf_patient_id ON core."patient_medical_history_files" USING btree (patient_id);
CREATE INDEX idx_mhf_uploaded_by ON core."patient_medical_history_files" USING btree (uploaded_by);
CREATE INDEX idx_psa_assessment_stage ON core."patient_scale_assignments" USING btree (assessment_stage);
CREATE INDEX idx_psa_patient_id ON core."patient_scale_assignments" USING btree (patient_id);
CREATE INDEX idx_psa_scale_id ON core."patient_scale_assignments" USING btree (scale_id);
CREATE INDEX idx_patients_primary_clinic_id ON core."patients" USING btree (primary_clinic_id);
CREATE INDEX idx_patients_primary_doctor_id ON core."patients" USING btree (primary_doctor_id);
CREATE INDEX idx_patients_profile_id ON core."patients" USING btree (profile_id);
CREATE INDEX idx_patients_registration_status ON core."patients" USING btree (registration_status);
CREATE INDEX idx_payments_order_id ON core."payments" USING btree (order_id);
CREATE INDEX idx_payments_session_id ON core."payments" USING btree (session_id);
CREATE INDEX idx_payments_status ON core."payments" USING btree (status);
CREATE INDEX idx_products_category ON reference."products" USING btree (category);
CREATE INDEX idx_products_is_active ON reference."products" USING btree (is_active);
CREATE INDEX idx_profiles_is_active ON core."profiles" USING btree (is_active);
CREATE INDEX idx_profiles_role ON core."profiles" USING btree (role);
CREATE INDEX idx_pai_assessment_stage ON core."prs_assessment_instances" USING btree (assessment_stage);
CREATE INDEX idx_pai_cycle_id ON core."prs_assessment_instances" USING btree (cycle_id);
CREATE INDEX idx_pai_disease_id ON core."prs_assessment_instances" USING btree (disease_id);
CREATE INDEX idx_pai_patient_id ON core."prs_assessment_instances" USING btree (patient_id);
CREATE INDEX idx_pai_session_id ON core."prs_assessment_instances" USING btree (session_id);
CREATE INDEX idx_pai_status ON core."prs_assessment_instances" USING btree (status);
CREATE INDEX idx_ot_lang ON reference."prs_option_translations" USING btree (language_code);
CREATE INDEX idx_qt_lang ON reference."prs_question_translations" USING btree (language_code);
CREATE INDEX idx_prs_questions_scale_id ON reference."prs_questions" USING btree (scale_id);
CREATE INDEX idx_prs_resp_instance_id ON core."prs_responses" USING btree (instance_id);
CREATE INDEX idx_prs_resp_question_id ON core."prs_responses" USING btree (question_id);
CREATE INDEX idx_prs_scales_applicable_for ON reference."prs_scales" USING btree (applicable_for);
CREATE INDEX idx_prs_scales_is_common ON reference."prs_scales" USING btree (is_common_scale);
CREATE INDEX idx_regions_is_active ON core."regions" USING btree (is_active);
CREATE INDEX idx_regions_regional_admin ON core."regions" USING btree (regional_admin_id);
CREATE INDEX idx_sessions_ca_id ON core."sessions" USING btree (ca_id);
CREATE INDEX idx_sessions_clinic_id ON core."sessions" USING btree (clinic_id);
CREATE INDEX idx_sessions_cycle_id ON core."sessions" USING btree (cycle_id);
CREATE INDEX idx_sessions_doctor_id ON core."sessions" USING btree (doctor_id);
CREATE INDEX idx_sessions_patient_date ON core."sessions" USING btree (patient_id, session_date DESC);
CREATE INDEX idx_sessions_patient_id ON core."sessions" USING btree (patient_id);
CREATE INDEX idx_sessions_payment_status ON core."sessions" USING btree (payment_status);
CREATE INDEX idx_sessions_session_date ON core."sessions" USING btree (session_date);
CREATE INDEX idx_sessions_session_phase ON core."sessions" USING btree (session_phase);
CREATE INDEX idx_sessions_session_type ON core."sessions" USING btree (session_type);
CREATE INDEX idx_sessions_status ON core."sessions" USING btree (status);
CREATE INDEX idx_staff_req_clinic_id ON core."staff_requests" USING btree (clinic_id);
CREATE INDEX idx_staff_req_cred_gin ON core."staff_requests" USING gin (candidate_credentials);
CREATE INDEX idx_staff_req_position_role ON core."staff_requests" USING btree (position_role);
CREATE INDEX idx_staff_req_status ON core."staff_requests" USING btree (status);
CREATE INDEX idx_staff_req_submitted_by ON core."staff_requests" USING btree (submitted_by);
CREATE INDEX idx_st_from_clinic_id ON core."stock_transfers" USING btree (from_clinic_id);
CREATE INDEX idx_st_order_id ON core."stock_transfers" USING btree (order_id);
CREATE INDEX idx_st_product_id ON core."stock_transfers" USING btree (product_id);
CREATE INDEX idx_st_status ON core."stock_transfers" USING btree (status);
CREATE INDEX idx_st_to_clinic_id ON core."stock_transfers" USING btree (to_clinic_id);
CREATE INDEX idx_so_clinic_id ON core."store_orders" USING btree (clinic_id);
CREATE INDEX idx_so_initiated_by ON core."store_orders" USING btree (initiated_by);
CREATE INDEX idx_so_order_type ON core."store_orders" USING btree (order_type);
CREATE INDEX idx_so_patient_id ON core."store_orders" USING btree (patient_id);
CREATE INDEX idx_so_plan_id ON core."store_orders" USING btree (treatment_plan_id);
CREATE INDEX idx_so_status ON core."store_orders" USING btree (status);
CREATE INDEX idx_cycles_clinic_id ON core."treatment_cycles" USING btree (clinic_id);
CREATE INDEX idx_cycles_cycle_type ON core."treatment_cycles" USING btree (cycle_type);
CREATE INDEX idx_cycles_doctor_id ON core."treatment_cycles" USING btree (doctor_id);
CREATE INDEX idx_cycles_patient_id ON core."treatment_cycles" USING btree (patient_id);
CREATE INDEX idx_cycles_status ON core."treatment_cycles" USING btree (status);
CREATE INDEX idx_tp_cycle_id ON core."treatment_plans" USING btree (cycle_id);
CREATE INDEX idx_tp_doctor_id ON core."treatment_plans" USING btree (doctor_id);
CREATE INDEX idx_tp_parent_plan_id ON core."treatment_plans" USING btree (parent_plan_id);
CREATE INDEX idx_tp_patient_id ON core."treatment_plans" USING btree (patient_id);
CREATE INDEX idx_tp_protocol_gin ON core."treatment_plans" USING gin (protocol_details);
CREATE INDEX idx_tp_status ON core."treatment_plans" USING btree (status);
CREATE INDEX idx_ts_billing_type ON core."treatment_sessions" USING btree (billing_type);
CREATE INDEX idx_ts_ca_id ON core."treatment_sessions" USING btree (ca_id);
CREATE INDEX idx_ts_patient_id ON core."treatment_sessions" USING btree (patient_id);
CREATE INDEX idx_ts_payment_status ON core."treatment_sessions" USING btree (payment_status);
CREATE INDEX idx_ts_plan_id ON core."treatment_sessions" USING btree (plan_id);
CREATE INDEX idx_ts_session_id ON core."treatment_sessions" USING btree (session_id);
CREATE INDEX idx_ts_status ON core."treatment_sessions" USING btree (status);

-- From file 27 — see the note in §9.
CREATE INDEX "idx_patients_registered_by" ON core."patients" USING btree ("registered_by") WHERE (registered_by IS NOT NULL);


-- btree_gist extension (01_extensions.sql) makes the doctor_id equality term possible in a GIST exclusion.
ALTER TABLE core."appointments" ADD CONSTRAINT "excl_doctor_overlap" EXCLUDE USING gist (doctor_id WITH =, tsrange((appointment_date + start_time), (appointment_date + end_time)) WITH &&) WHERE (status <> ALL (ARRAY['cancelled'::text, 'rescheduled'::text]));
-- ---- new ----

CREATE INDEX idx_neuromod_devices_modality        ON reference."neuromod_devices"          USING btree (modality);
CREATE INDEX idx_neuromod_devices_phase           ON reference."neuromod_devices"          USING btree (phase) WHERE (is_active = true);
CREATE INDEX idx_neuromod_diagnoses_condition_id  ON reference."neuromod_diagnoses"        USING btree (condition_id);
CREATE INDEX idx_neuromod_scales_prs_scale_id     ON reference."neuromod_scales"           USING btree (prs_scale_id);
CREATE INDEX idx_neuromod_cs_condition_id         ON reference."neuromod_condition_scales" USING btree (condition_id);
CREATE INDEX idx_neuromod_cs_scale_id             ON reference."neuromod_condition_scales" USING btree (scale_id);

CREATE INDEX idx_tdcs_placements_condition_id     ON reference."tdcs_placements"    USING btree (condition_id);
CREATE INDEX idx_tdcs_placements_device_id        ON reference."tdcs_placements"    USING btree (device_id);
CREATE INDEX idx_hd_tdcs_placements_condition_id  ON reference."hd_tdcs_placements" USING btree (condition_id);
CREATE INDEX idx_hd_tdcs_placements_device_id     ON reference."hd_tdcs_placements" USING btree (device_id);
CREATE INDEX idx_tavns_placements_condition_id    ON reference."tavns_placements"   USING btree (condition_id);
CREATE INDEX idx_tavns_placements_device_id       ON reference."tavns_placements"   USING btree (device_id);
CREATE INDEX idx_tps_placements_condition_id      ON reference."tps_placements"     USING btree (condition_id);
CREATE INDEX idx_tps_placements_device_id         ON reference."tps_placements"     USING btree (device_id);
CREATE INDEX idx_rtms_placements_condition_id     ON reference."rtms_placements"    USING btree (condition_id);
CREATE INDEX idx_rtms_placements_device_id        ON reference."rtms_placements"    USING btree (device_id);
CREATE INDEX idx_other_placements_condition_id    ON reference."other_placements"   USING btree (condition_id);
CREATE INDEX idx_other_placements_device_id       ON reference."other_placements"   USING btree (device_id);

CREATE INDEX idx_tdcs_dosing_condition_id     ON reference."tdcs_dosing"    USING btree (condition_id);
CREATE INDEX idx_tdcs_dosing_placement_id     ON reference."tdcs_dosing"    USING btree (tdcs_placement_id);
CREATE INDEX idx_hd_tdcs_dosing_condition_id  ON reference."hd_tdcs_dosing" USING btree (condition_id);
CREATE INDEX idx_hd_tdcs_dosing_placement_id  ON reference."hd_tdcs_dosing" USING btree (hd_tdcs_placement_id);
CREATE INDEX idx_tavns_dosing_condition_id    ON reference."tavns_dosing"   USING btree (condition_id);
CREATE INDEX idx_tavns_dosing_placement_id    ON reference."tavns_dosing"   USING btree (tavns_placement_id);
CREATE INDEX idx_tps_dosing_condition_id      ON reference."tps_dosing"     USING btree (condition_id);
CREATE INDEX idx_tps_dosing_placement_id      ON reference."tps_dosing"     USING btree (tps_placement_id);
CREATE INDEX idx_rtms_dosing_condition_id     ON reference."rtms_dosing"    USING btree (condition_id);
CREATE INDEX idx_rtms_dosing_placement_id     ON reference."rtms_dosing"    USING btree (rtms_placement_id);
CREATE INDEX idx_other_dosing_condition_id    ON reference."other_dosing"   USING btree (condition_id);
CREATE INDEX idx_other_dosing_placement_id    ON reference."other_dosing"   USING btree (other_placement_id);

CREATE INDEX idx_treatment_protocols_plan_id   ON core."treatment_protocols" USING btree (plan_id);
CREATE INDEX idx_treatment_protocols_device_id ON core."treatment_protocols" USING btree (device_id);
CREATE INDEX idx_treatment_protocols_set_by    ON core."treatment_protocols" USING btree (set_by);
CREATE INDEX idx_treatment_protocols_status    ON core."treatment_protocols" USING btree (status);

CREATE INDEX idx_protocol_sessions_protocol_id  ON core."protocol_sessions"  USING btree (protocol_id);
CREATE INDEX idx_protocol_followups_protocol_id ON core."protocol_followups" USING btree (protocol_id);

CREATE INDEX idx_ds_prs_patient_id ON core."device_session_prs_responses" USING btree (patient_id);
CREATE INDEX idx_fu_prs_patient_id ON core."followup_prs_responses"       USING btree (patient_id);


-- ###########################################################################
-- §14  LAYER 2 — Views
-- ###########################################################################


CREATE VIEW core."v_doctor_active_patient_counts" AS
    SELECT doctor_id, count(*) AS active_patient_count FROM core.doctor_patient_assignments WHERE status = 'active'::text GROUP BY doctor_id;


-- ###########################################################################
-- §15  LAYER 2 — Functions
-- ###########################################################################

-- ---- existing (11) ----

CREATE OR REPLACE FUNCTION ops.fn_audit_trigger()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
DECLARE
    v_pk_col    TEXT := TG_ARGV[0];
    v_record_id TEXT;
    v_old_data  JSONB;
    v_new_data  JSONB;
    v_user_id   UUID;
BEGIN
    -- Read actor from session context (set by FastAPI middleware)
    BEGIN
        v_user_id := NULLIF(current_setting('app.current_user_id', TRUE), '')::UUID;
    EXCEPTION WHEN others THEN
        v_user_id := NULL;
    END;

    IF TG_OP = 'DELETE' THEN
        v_old_data  := to_jsonb(OLD);
        v_new_data  := NULL;
        v_record_id := v_old_data ->> v_pk_col;   -- TEXT, no ::UUID cast
    ELSIF TG_OP = 'INSERT' THEN
        v_old_data  := NULL;
        v_new_data  := to_jsonb(NEW);
        v_record_id := v_new_data ->> v_pk_col;   -- TEXT, no ::UUID cast
    ELSE  -- UPDATE
        v_old_data  := to_jsonb(OLD);
        v_new_data  := to_jsonb(NEW);
        v_record_id := v_new_data ->> v_pk_col;   -- TEXT, no ::UUID cast
    END IF;

    INSERT INTO audit_logs (table_name, operation, record_id, old_data, new_data, changed_by)
    VALUES (TG_TABLE_NAME, TG_OP, v_record_id, v_old_data, v_new_data, v_user_id);

    RETURN NULL;  -- AFTER trigger; return value ignored
END;
$function$;


CREATE OR REPLACE FUNCTION core.fn_generate_mrn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.mrn IS NULL THEN
        NEW.mrn := 'ANV-' || LPAD(nextval('core.mrn_seq')::TEXT, 8, '0');
    END IF;
    RETURN NEW;
END;
$function$;


CREATE OR REPLACE FUNCTION ops.fn_notify_outbox_event()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    PERFORM pg_notify('outbox_new_event', NEW.outbox_id::TEXT);
    RETURN NEW;
END;
$function$;


CREATE OR REPLACE FUNCTION ops.fn_set_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$function$;


CREATE OR REPLACE FUNCTION core.recalculate_final_result()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_instance      prs_assessment_instances%ROWTYPE;
    v_total         NUMERIC := 0;
    v_max           NUMERIC := 0;
    v_completed     INTEGER := 0;
    v_total_scales  INTEGER := 0;
    v_worst_sev     TEXT    := NULL;
    v_worst_label   TEXT    := NULL;
    v_summaries     JSONB   := '[]'::JSONB;
    v_all_flags     JSONB   := '[]'::JSONB;
    sev_order       INTEGER;
    worst_order     INTEGER := -1;
    r               RECORD;
BEGIN
    SELECT * INTO v_instance
    FROM prs_assessment_instances
    WHERE instance_id = NEW.instance_id;

    -- Count only scales that apply to THIS instance's assessment_stage.
    SELECT COUNT(*) INTO v_total_scales
    FROM prs_disease_scale_map m
    JOIN prs_scales sc ON sc.scale_id = m.scale_id
    WHERE m.disease_id = v_instance.disease_id
      AND sc.applicable_for IN (v_instance.assessment_stage, 'all');

    -- Aggregate all scale results for this instance
    FOR r IN
        SELECT sr.*, sc.scale_code, sc.scale_name
        FROM prs_scale_results sr
        JOIN prs_scales sc ON sc.scale_id = sr.scale_id
        WHERE sr.instance_id = NEW.instance_id
    LOOP
        v_total     := v_total + COALESCE(r.calculated_value, 0);
        v_max       := v_max   + COALESCE(r.max_possible, 0);
        v_completed := v_completed + 1;

        sev_order := CASE r.severity_level
            WHEN 'severe'            THEN 4
            WHEN 'moderately-severe' THEN 3
            WHEN 'moderate'          THEN 2
            WHEN 'mild'              THEN 1
            ELSE 0
        END;
        IF sev_order > worst_order THEN
            worst_order   := sev_order;
            v_worst_sev   := r.severity_level;
            v_worst_label := r.severity_label;
        END IF;

        v_summaries := v_summaries || jsonb_build_object(
            'scale_code',     r.scale_code,
            'scale_name',     r.scale_name,
            'score',          r.calculated_value,
            'max_possible',   r.max_possible,
            'percentage',     CASE WHEN r.max_possible > 0
                                   THEN ROUND((r.calculated_value / r.max_possible) * 100, 2)
                                   ELSE NULL END,
            'severity_level', r.severity_level,
            'severity_label', r.severity_label
        );

        IF r.risk_flags IS NOT NULL AND jsonb_array_length(r.risk_flags) > 0 THEN
            v_all_flags := v_all_flags || r.risk_flags;
        END IF;
    END LOOP;

    INSERT INTO prs_final_results (
        final_result_id,
        instance_id,
        calculated_value,
        max_possible,
        scales_completed,
        scales_total,
        overall_severity,
        overall_severity_label,
        scale_summaries,
        all_risk_flags,
        time_stamp
    ) VALUES (
        NEW.instance_id || '/' || v_instance.disease_id,
        NEW.instance_id,
        v_total,
        v_max,
        v_completed,
        v_total_scales,
        v_worst_sev,
        v_worst_label,
        v_summaries,
        v_all_flags,
        NOW()
    )
    ON CONFLICT (instance_id) DO UPDATE SET
        calculated_value        = EXCLUDED.calculated_value,
        max_possible            = EXCLUDED.max_possible,
        scales_completed        = EXCLUDED.scales_completed,
        scales_total            = EXCLUDED.scales_total,
        overall_severity        = EXCLUDED.overall_severity,
        overall_severity_label  = EXCLUDED.overall_severity_label,
        scale_summaries         = EXCLUDED.scale_summaries,
        all_risk_flags          = EXCLUDED.all_risk_flags,
        time_stamp              = EXCLUDED.time_stamp;

    IF v_completed >= v_total_scales THEN
        UPDATE prs_assessment_instances
        SET
            status       = 'completed',
            completed_at = NOW(),
            final_result = (
                SELECT final_result_id
                FROM prs_final_results
                WHERE instance_id = NEW.instance_id
            )
        WHERE instance_id = NEW.instance_id
          AND status != 'completed';
    END IF;

    RETURN NEW;
END;
$function$;


CREATE OR REPLACE FUNCTION ops.rls_user_role()
 RETURNS text
 LANGUAGE sql
 STABLE
AS $function$
    SELECT NULLIF(current_setting('app.current_user_role', TRUE), '');
$function$;


CREATE OR REPLACE FUNCTION ops.rls_user_id()
 RETURNS uuid
 LANGUAGE sql
 STABLE
AS $function$
    SELECT NULLIF(current_setting('app.current_user_id', TRUE), '')::UUID;
$function$;


CREATE OR REPLACE FUNCTION ops.rls_region_id()
 RETURNS uuid
 LANGUAGE sql
 STABLE
AS $function$
    SELECT NULLIF(current_setting('app.current_region_id', TRUE), '')::UUID;
$function$;


CREATE OR REPLACE FUNCTION ops.rls_email()
 RETURNS text
 LANGUAGE sql
 STABLE
AS $function$
    SELECT NULLIF(current_setting('app.current_email', TRUE), '');
$function$;


CREATE OR REPLACE FUNCTION ops.rls_cognito_sub()
 RETURNS text
 LANGUAGE sql
 STABLE
AS $function$
    SELECT NULLIF(current_setting('app.current_cognito_sub', TRUE), '');
$function$;


CREATE OR REPLACE FUNCTION ops.rls_clinic_id()
 RETURNS uuid
 LANGUAGE sql
 STABLE
AS $function$
    SELECT NULLIF(current_setting('app.current_clinic_id', TRUE), '')::UUID;
$function$;

-- ---- new (2) ----

CREATE OR REPLACE FUNCTION core.fn_check_protocol_device_consistency()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_placement_device UUID;
    v_dosing_device    UUID;
BEGIN
    SELECT device_id INTO v_placement_device FROM reference.tdcs_placements    WHERE tdcs_placement_id    = NEW.tdcs_placement_id;
    IF v_placement_device IS NULL THEN
        SELECT device_id INTO v_placement_device FROM reference.hd_tdcs_placements WHERE hd_tdcs_placement_id = NEW.hd_tdcs_placement_id;
    END IF;
    IF v_placement_device IS NULL THEN
        SELECT device_id INTO v_placement_device FROM reference.tavns_placements   WHERE tavns_placement_id   = NEW.tavns_placement_id;
    END IF;
    IF v_placement_device IS NULL THEN
        SELECT device_id INTO v_placement_device FROM reference.tps_placements     WHERE tps_placement_id     = NEW.tps_placement_id;
    END IF;
    IF v_placement_device IS NULL THEN
        SELECT device_id INTO v_placement_device FROM reference.rtms_placements    WHERE rtms_placement_id    = NEW.rtms_placement_id;
    END IF;
    IF v_placement_device IS NULL THEN
        SELECT device_id INTO v_placement_device FROM reference.other_placements   WHERE other_placement_id   = NEW.other_placement_id;
    END IF;

    SELECT device_id INTO v_dosing_device FROM reference.tdcs_dosing    WHERE tdcs_dosing_id    = NEW.tdcs_dosing_id;
    IF v_dosing_device IS NULL THEN
        SELECT device_id INTO v_dosing_device FROM reference.hd_tdcs_dosing WHERE hd_tdcs_dosing_id = NEW.hd_tdcs_dosing_id;
    END IF;
    IF v_dosing_device IS NULL THEN
        SELECT device_id INTO v_dosing_device FROM reference.tavns_dosing   WHERE tavns_dosing_id   = NEW.tavns_dosing_id;
    END IF;
    IF v_dosing_device IS NULL THEN
        SELECT device_id INTO v_dosing_device FROM reference.tps_dosing     WHERE tps_dosing_id     = NEW.tps_dosing_id;
    END IF;
    IF v_dosing_device IS NULL THEN
        SELECT device_id INTO v_dosing_device FROM reference.rtms_dosing    WHERE rtms_dosing_id    = NEW.rtms_dosing_id;
    END IF;
    IF v_dosing_device IS NULL THEN
        SELECT device_id INTO v_dosing_device FROM reference.other_dosing   WHERE other_dosing_id   = NEW.other_dosing_id;
    END IF;

    IF v_placement_device IS DISTINCT FROM NEW.device_id THEN
        RAISE EXCEPTION 'Protocol device_id % does not match the placement''s device %', NEW.device_id, v_placement_device;
    END IF;
    IF v_dosing_device IS DISTINCT FROM NEW.device_id THEN
        RAISE EXCEPTION 'Protocol device_id % does not match the dosing''s device %', NEW.device_id, v_dosing_device;
    END IF;

    RETURN NEW;
END;
$function$;

CREATE TRIGGER trg_check_protocol_device_consistency
    BEFORE INSERT OR UPDATE ON core."treatment_protocols"
    FOR EACH ROW EXECUTE FUNCTION core.fn_check_protocol_device_consistency();


CREATE OR REPLACE FUNCTION core.fn_generate_protocol_sessions(
    p_protocol_id   UUID,
    p_start_date    DATE,
    p_slot_days     INTEGER DEFAULT 2,
    p_session_time  TIME DEFAULT '09:00',
    p_followup_time TIME DEFAULT '10:00',
    p_slot_minutes  INTEGER DEFAULT 30
)
 RETURNS INTEGER
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_protocol   core.treatment_protocols%ROWTYPE;
    v_plan       core.treatment_plans%ROWTYPE;
    v_clinic_id  UUID;
    v_appt_id    UUID;
    v_i          INTEGER;
    v_appt_date  DATE;
    v_created    INTEGER := 0;
BEGIN
    SELECT * INTO v_protocol FROM core.treatment_protocols WHERE protocol_id = p_protocol_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Protocol % not found', p_protocol_id;
    END IF;

    SELECT * INTO v_plan FROM core.treatment_plans WHERE plan_id = v_protocol.plan_id;

    -- treatment_plans has no clinic_id of its own; it comes from the cycle.
    SELECT clinic_id INTO v_clinic_id FROM core.treatment_cycles WHERE cycle_id = v_plan.cycle_id;
    IF v_clinic_id IS NULL THEN
        RAISE EXCEPTION 'Cannot resolve clinic for plan % (cycle %)', v_plan.plan_id, v_plan.cycle_id;
    END IF;

    -- Refuse to double-generate rather than silently appending a second run.
    IF EXISTS (SELECT 1 FROM core.protocol_sessions WHERE protocol_id = p_protocol_id) THEN
        RAISE EXCEPTION 'Protocol % already has generated sessions', p_protocol_id;
    END IF;

    FOR v_i IN 1..v_protocol.session_count LOOP
        v_appt_date := p_start_date + ((v_i - 1) * p_slot_days);

        -- Device sessions do NOT create an appointment row. Architecture v2:
        -- an appointment is a DOCTOR CONSULTATION; a tDCS device session is
        -- CA-administered and belongs to the session table, which is not
        -- finalised. Booking an appointment per device session would
        -- double-book the doctor 20 times per protocol and fire the
        -- consultation-fee rail 24 times instead of 4.
        INSERT INTO core.protocol_sessions (protocol_id, session_number, planned_date)
        VALUES (p_protocol_id, v_i, v_appt_date);

        v_created := v_created + 1;

        IF v_protocol.follow_up_every_n IS NOT NULL
           AND v_i % v_protocol.follow_up_every_n = 0 THEN

            INSERT INTO core.appointments (
                clinic_id, patient_id, doctor_id, appointment_date,
                start_time, end_time, slot_duration_minutes,
                appointment_type, status, booked_by, booked_by_role
            ) VALUES (
                v_clinic_id, v_plan.patient_id, v_plan.doctor_id, v_appt_date + 1,
                p_followup_time, p_followup_time + (p_slot_minutes || ' minutes')::interval, p_slot_minutes,
                'follow_up', 'scheduled', v_protocol.set_by, 'doctor'
            )
            RETURNING appointment_id INTO v_appt_id;

            INSERT INTO core.protocol_followups (protocol_id, appointment_id, after_session_number)
            VALUES (p_protocol_id, v_appt_id, v_i);

            v_created := v_created + 1;
        END IF;
    END LOOP;

    RETURN v_created;
END;
$function$;

COMMENT ON FUNCTION core.fn_generate_protocol_sessions IS 'Generates the device-session and follow-up appointments for a protocol. Returns the number of appointment rows created. Calendar placement is a placeholder — see the header comment.';


-- ###########################################################################
-- §16  LAYER 2 — Triggers
-- ###########################################################################

-- ---- existing (62) ----

CREATE TRIGGER trg_audit_admins AFTER INSERT OR DELETE OR UPDATE ON core."admins" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('admin_id');
CREATE TRIGGER trg_audit_anamnesis_assessments AFTER INSERT OR DELETE OR UPDATE ON core."anamnesis_assessments" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('anamnesis_id');
CREATE TRIGGER trg_updated_at_anamnesis_assessments BEFORE UPDATE ON core."anamnesis_assessments" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_updated_at_anamnesis_responses BEFORE UPDATE ON core."anamnesis_responses" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_appointment_requests AFTER INSERT OR DELETE OR UPDATE ON core."appointment_requests" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('request_id');
CREATE TRIGGER trg_updated_at_appointment_requests BEFORE UPDATE ON core."appointment_requests" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_appointments AFTER INSERT OR DELETE OR UPDATE ON core."appointments" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('appointment_id');
CREATE TRIGGER trg_updated_at_appointments BEFORE UPDATE ON core."appointments" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_assessment_protocol_requests AFTER INSERT OR DELETE OR UPDATE ON core."assessment_protocol_requests" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('request_id');
CREATE TRIGGER trg_updated_at_assessment_protocol_requests BEFORE UPDATE ON core."assessment_protocol_requests" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_ca_doctor_assignments AFTER INSERT OR DELETE OR UPDATE ON core."ca_doctor_assignments" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('cda_id');
CREATE TRIGGER trg_audit_clinic_requests AFTER INSERT OR DELETE OR UPDATE ON core."clinic_requests" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('request_id');
CREATE TRIGGER trg_updated_at_clinic_requests BEFORE UPDATE ON core."clinic_requests" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_clinic_staff_assignments AFTER INSERT OR DELETE OR UPDATE ON core."clinic_staff_assignments" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('assignment_id');
CREATE TRIGGER trg_audit_clinics AFTER INSERT OR DELETE OR UPDATE ON core."clinics" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('clinic_id');
CREATE TRIGGER trg_updated_at_clinics BEFORE UPDATE ON core."clinics" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_consent_records AFTER INSERT OR DELETE OR UPDATE ON compliance."consent_records" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('consent_id');
CREATE TRIGGER trg_audit_consent_templates AFTER INSERT OR DELETE OR UPDATE ON reference."consent_templates" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('template_id');
CREATE TRIGGER trg_audit_device_assignments AFTER INSERT OR DELETE OR UPDATE ON core."device_assignments" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('da_id');
CREATE TRIGGER trg_audit_doctor_patient_assignments AFTER INSERT OR DELETE OR UPDATE ON core."doctor_patient_assignments" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('assignment_id');
CREATE TRIGGER trg_audit_doctor_schedule_overrides AFTER INSERT OR DELETE OR UPDATE ON core."doctor_schedule_overrides" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('override_id');
CREATE TRIGGER trg_audit_doctor_session_notes AFTER INSERT OR DELETE OR UPDATE ON core."doctor_session_notes" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('note_id');
CREATE TRIGGER trg_updated_at_doctor_session_notes BEFORE UPDATE ON core."doctor_session_notes" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_doctor_weekly_schedules AFTER INSERT OR DELETE OR UPDATE ON core."doctor_weekly_schedules" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('schedule_id');
CREATE TRIGGER trg_updated_at_doctor_weekly_schedules BEFORE UPDATE ON core."doctor_weekly_schedules" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_doctors AFTER INSERT OR DELETE OR UPDATE ON core."doctors" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('doctor_id');
CREATE TRIGGER trg_updated_at_inventory BEFORE UPDATE ON core."inventory" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_notify_outbox_event AFTER INSERT ON ops."outbox_events" FOR EACH ROW EXECUTE FUNCTION ops.fn_notify_outbox_event();
CREATE TRIGGER trg_audit_patient_clinic_transfers AFTER INSERT OR DELETE OR UPDATE ON core."patient_clinic_transfers" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('pct_id');
CREATE TRIGGER trg_updated_at_patient_clinic_transfers BEFORE UPDATE ON core."patient_clinic_transfers" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_patient_disease_selection AFTER INSERT OR DELETE OR UPDATE ON core."patient_disease_selection" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('pds_id');
CREATE TRIGGER trg_updated_at_patient_disease_selection BEFORE UPDATE ON core."patient_disease_selection" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_patient_eeg_files AFTER INSERT OR DELETE OR UPDATE ON core."patient_eeg_files" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('eeg_id');
CREATE TRIGGER trg_updated_at_patient_eeg_files BEFORE UPDATE ON core."patient_eeg_files" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_patient_medical_history_files AFTER INSERT OR DELETE OR UPDATE ON core."patient_medical_history_files" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('mhf_id');
CREATE TRIGGER trg_updated_at_patient_medical_history_files BEFORE UPDATE ON core."patient_medical_history_files" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_patient_scale_assignments AFTER INSERT OR DELETE OR UPDATE ON core."patient_scale_assignments" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('psa_id');
CREATE TRIGGER trg_audit_patients AFTER INSERT OR DELETE OR UPDATE ON core."patients" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('patient_id');
CREATE TRIGGER trg_generate_mrn BEFORE INSERT ON core."patients" FOR EACH ROW EXECUTE FUNCTION core.fn_generate_mrn();
CREATE TRIGGER trg_updated_at_patients BEFORE UPDATE ON core."patients" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_payments AFTER INSERT OR DELETE OR UPDATE ON core."payments" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('payment_id');
CREATE TRIGGER trg_updated_at_payments BEFORE UPDATE ON core."payments" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_updated_at_products BEFORE UPDATE ON reference."products" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_profiles AFTER INSERT OR DELETE OR UPDATE ON core."profiles" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('id');
CREATE TRIGGER trg_updated_at_profiles BEFORE UPDATE ON core."profiles" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_prs_assessment_instances AFTER INSERT OR DELETE OR UPDATE ON core."prs_assessment_instances" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('instance_id');
CREATE TRIGGER trg_updated_at_prs_diseases BEFORE UPDATE ON reference."prs_diseases" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE CONSTRAINT TRIGGER trg_recalculate_final_result AFTER INSERT OR UPDATE ON core."prs_scale_results" DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION core.recalculate_final_result();
CREATE TRIGGER trg_updated_at_prs_scales BEFORE UPDATE ON reference."prs_scales" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_regions AFTER INSERT OR DELETE OR UPDATE ON core."regions" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('region_id');
CREATE TRIGGER trg_updated_at_regions BEFORE UPDATE ON core."regions" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_sessions AFTER INSERT OR DELETE OR UPDATE ON core."sessions" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('session_id');
CREATE TRIGGER trg_updated_at_sessions BEFORE UPDATE ON core."sessions" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_staff_requests AFTER INSERT OR DELETE OR UPDATE ON core."staff_requests" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('request_id');
CREATE TRIGGER trg_updated_at_staff_requests BEFORE UPDATE ON core."staff_requests" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_stock_transfers AFTER INSERT OR DELETE OR UPDATE ON core."stock_transfers" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('st_id');
CREATE TRIGGER trg_audit_store_orders AFTER INSERT OR DELETE OR UPDATE ON core."store_orders" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('order_id');
CREATE TRIGGER trg_updated_at_store_orders BEFORE UPDATE ON core."store_orders" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_treatment_cycles AFTER INSERT OR DELETE OR UPDATE ON core."treatment_cycles" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('cycle_id');
CREATE TRIGGER trg_updated_at_treatment_cycles BEFORE UPDATE ON core."treatment_cycles" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_treatment_plans AFTER INSERT OR DELETE OR UPDATE ON core."treatment_plans" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('plan_id');
CREATE TRIGGER trg_updated_at_treatment_plans BEFORE UPDATE ON core."treatment_plans" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_audit_treatment_sessions AFTER INSERT OR DELETE OR UPDATE ON core."treatment_sessions" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('ts_id');;
-- ---- new (21) ----

CREATE TRIGGER trg_updated_at_neuromod_devices    BEFORE UPDATE ON reference."neuromod_devices"    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_updated_at_neuromod_conditions BEFORE UPDATE ON reference."neuromod_conditions" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_updated_at_tdcs_placements     BEFORE UPDATE ON reference."tdcs_placements"     FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_updated_at_hd_tdcs_placements  BEFORE UPDATE ON reference."hd_tdcs_placements"  FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_updated_at_tavns_placements    BEFORE UPDATE ON reference."tavns_placements"    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_updated_at_tps_placements      BEFORE UPDATE ON reference."tps_placements"      FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_updated_at_rtms_placements     BEFORE UPDATE ON reference."rtms_placements"     FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_updated_at_other_placements    BEFORE UPDATE ON reference."other_placements"    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_updated_at_tdcs_dosing         BEFORE UPDATE ON reference."tdcs_dosing"         FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_updated_at_hd_tdcs_dosing      BEFORE UPDATE ON reference."hd_tdcs_dosing"      FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_updated_at_tavns_dosing        BEFORE UPDATE ON reference."tavns_dosing"        FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_updated_at_tps_dosing          BEFORE UPDATE ON reference."tps_dosing"          FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_updated_at_rtms_dosing         BEFORE UPDATE ON reference."rtms_dosing"         FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();
CREATE TRIGGER trg_updated_at_other_dosing        BEFORE UPDATE ON reference."other_dosing"        FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();

CREATE TRIGGER trg_updated_at_treatment_protocols BEFORE UPDATE ON core."treatment_protocols" FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();

CREATE TRIGGER trg_audit_treatment_protocols          AFTER INSERT OR DELETE OR UPDATE ON core."treatment_protocols"          FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('protocol_id');
CREATE TRIGGER trg_audit_protocol_sessions            AFTER INSERT OR DELETE OR UPDATE ON core."protocol_sessions"            FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('protocol_session_id');
CREATE TRIGGER trg_audit_protocol_followups           AFTER INSERT OR DELETE OR UPDATE ON core."protocol_followups"           FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('protocol_followup_id');
CREATE TRIGGER trg_audit_device_session_prs_responses AFTER INSERT OR DELETE OR UPDATE ON core."device_session_prs_responses" FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('ds_prs_id');
CREATE TRIGGER trg_audit_followup_prs_responses       AFTER INSERT OR DELETE OR UPDATE ON core."followup_prs_responses"       FOR EACH ROW EXECUTE FUNCTION ops.fn_audit_trigger('fu_prs_id');


-- ###########################################################################
-- §17  LAYER 3 — Row-level security: ENABLE + FORCE
-- ###########################################################################

-- CAUTION (26_rls_lockout_fixes.sql): FORCE RLS with no policy for a given
-- command silently matches ZERO rows — it is not an error. Four tables were
-- locked out this way during the original build. Every table enabled below
-- has explicit policies in the next section.

-- ---- existing ----

-- ca_doctor_assignments gets RLS here (it did NOT have it live in production — see notes).
ALTER TABLE compliance."activity_logs" ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance."activity_logs" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."admins" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."admins" FORCE ROW LEVEL SECURITY;
ALTER TABLE ops."alembic_version" ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops."alembic_version" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."anamnesis_assessments" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."anamnesis_assessments" FORCE ROW LEVEL SECURITY;
ALTER TABLE reference."anamnesis_options" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."anamnesis_options" FORCE ROW LEVEL SECURITY;
ALTER TABLE reference."anamnesis_questions" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."anamnesis_questions" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."anamnesis_responses" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."anamnesis_responses" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."appointment_audit_logs" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."appointment_audit_logs" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."appointment_requests" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."appointment_requests" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."appointments" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."appointments" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."assessment_protocol_requests" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."assessment_protocol_requests" FORCE ROW LEVEL SECURITY;
ALTER TABLE compliance."audit_logs" ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance."audit_logs" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."ca_doctor_assignments" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."ca_doctor_assignments" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."clinic_requests" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."clinic_requests" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."clinic_staff_assignments" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."clinic_staff_assignments" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."clinical_assistants" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."clinical_assistants" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."clinics" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."clinics" FORCE ROW LEVEL SECURITY;
ALTER TABLE compliance."consent_records" ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance."consent_records" FORCE ROW LEVEL SECURITY;
ALTER TABLE reference."consent_templates" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."consent_templates" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."device_assignments" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."device_assignments" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."doctor_patient_assignments" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."doctor_patient_assignments" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."doctor_schedule_overrides" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."doctor_schedule_overrides" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."doctor_session_notes" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."doctor_session_notes" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."doctor_weekly_schedules" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."doctor_weekly_schedules" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."doctors" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."doctors" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."inventory" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."inventory" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."notifications" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."notifications" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."order_items" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."order_items" FORCE ROW LEVEL SECURITY;
ALTER TABLE ops."outbox_events" ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops."outbox_events" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."patient_clinic_transfers" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."patient_clinic_transfers" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."patient_disease_selection" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."patient_disease_selection" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."patient_eeg_files" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."patient_eeg_files" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."patient_medical_history_files" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."patient_medical_history_files" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."patient_scale_assignments" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."patient_scale_assignments" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."patients" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."patients" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."payments" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."payments" FORCE ROW LEVEL SECURITY;
ALTER TABLE reference."products" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."products" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."profiles" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."profiles" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."prs_assessment_instances" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."prs_assessment_instances" FORCE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_disease_question_map" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_disease_question_map" FORCE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_disease_scale_map" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_disease_scale_map" FORCE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_diseases" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_diseases" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."prs_final_results" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."prs_final_results" FORCE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_option_translations" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_option_translations" FORCE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_options" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_options" FORCE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_question_translations" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_question_translations" FORCE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_questions" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_questions" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."prs_responses" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."prs_responses" FORCE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_scale_question_map" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_scale_question_map" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."prs_scale_results" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."prs_scale_results" FORCE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_scales" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."prs_scales" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."receptionists" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."receptionists" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."regions" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."regions" FORCE ROW LEVEL SECURITY;
ALTER TABLE ops."schema_migrations" ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops."schema_migrations" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."sessions" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."sessions" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."staff_requests" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."staff_requests" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."stock_transfers" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."stock_transfers" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."store_orders" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."store_orders" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."treatment_cycles" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."treatment_cycles" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."treatment_plans" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."treatment_plans" FORCE ROW LEVEL SECURITY;
ALTER TABLE core."treatment_sessions" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."treatment_sessions" FORCE ROW LEVEL SECURITY;
-- ---- new ----
ALTER TABLE reference."neuromod_devices"          ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_devices"          FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_conditions"       ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_conditions"       FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_diagnoses"        ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_diagnoses"        FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_scales"           ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_scales"           FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_condition_scales" ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."neuromod_condition_scales" FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."tdcs_placements"           ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."tdcs_placements"           FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."hd_tdcs_placements"        ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."hd_tdcs_placements"        FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."tavns_placements"          ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."tavns_placements"          FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."tps_placements"            ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."tps_placements"            FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."rtms_placements"           ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."rtms_placements"           FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."other_placements"          ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."other_placements"          FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."tdcs_dosing"               ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."tdcs_dosing"               FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."hd_tdcs_dosing"            ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."hd_tdcs_dosing"            FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."tavns_dosing"              ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."tavns_dosing"              FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."tps_dosing"                ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."tps_dosing"                FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."rtms_dosing"               ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."rtms_dosing"               FORCE  ROW LEVEL SECURITY;
ALTER TABLE reference."other_dosing"              ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference."other_dosing"              FORCE  ROW LEVEL SECURITY;

ALTER TABLE core."treatment_protocols"          ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."treatment_protocols"          FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."protocol_sessions"            ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."protocol_sessions"            FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."protocol_followups"           ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."protocol_followups"           FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."device_session_prs_responses" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."device_session_prs_responses" FORCE  ROW LEVEL SECURITY;
ALTER TABLE core."followup_prs_responses"       ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."followup_prs_responses"       FORCE  ROW LEVEL SECURITY;


-- ###########################################################################
-- §18  LAYER 3 — Row-level security: policies
-- ###########################################################################

-- ---- existing ----

CREATE POLICY "rls_actlog_insert" ON compliance."activity_logs" FOR INSERT TO public
    WITH CHECK (true);

CREATE POLICY "rls_actlog_select" ON compliance."activity_logs" FOR SELECT TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_admins_insert" ON core."admins" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_admins_select" ON core."admins" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (region_id = rls_region_id())) OR ((rls_user_role() = 'regional_admin'::text) AND (clinic_id IN ( SELECT clinics.clinic_id
   FROM clinics
  WHERE (clinics.region_id = rls_region_id())))) OR (profile_id = rls_user_id())));

CREATE POLICY "rls_admins_update" ON core."admins" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_anamnesis_insert" ON core."anamnesis_assessments" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'receptionist'::text, 'clinical_assistant'::text])) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_anamnesis_select" ON core."anamnesis_assessments" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (patient_id = rls_user_id()) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (patient_id IN ( SELECT patients.profile_id
   FROM patients
  WHERE (patients.primary_clinic_id = rls_clinic_id()))))));

CREATE POLICY "rls_anamnesis_update" ON core."anamnesis_assessments" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_anao_select" ON reference."anamnesis_options" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_anao_write" ON reference."anamnesis_options" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_anaq_select" ON reference."anamnesis_questions" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_anaq_write" ON reference."anamnesis_questions" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_anar_insert" ON core."anamnesis_responses" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (anamnesis_id IN ( SELECT anamnesis_assessments.anamnesis_id
   FROM anamnesis_assessments
  WHERE (anamnesis_assessments.patient_id = rls_user_id())))));

CREATE POLICY "rls_anar_select" ON core."anamnesis_responses" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (anamnesis_id IN ( SELECT anamnesis_assessments.anamnesis_id
   FROM anamnesis_assessments
  WHERE (anamnesis_assessments.patient_id = rls_user_id()))) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (anamnesis_id IN ( SELECT anamnesis_assessments.anamnesis_id
   FROM anamnesis_assessments
  WHERE (anamnesis_assessments.patient_id IN ( SELECT patients.profile_id
           FROM patients
          WHERE (patients.primary_clinic_id = rls_clinic_id()))))))));

CREATE POLICY "rls_anar_update" ON core."anamnesis_responses" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (anamnesis_id IN ( SELECT anamnesis_assessments.anamnesis_id
   FROM anamnesis_assessments
  WHERE (anamnesis_assessments.patient_id = rls_user_id())))));

CREATE POLICY "rls_apal_insert" ON core."appointment_audit_logs" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])));

CREATE POLICY "rls_apal_select" ON core."appointment_audit_logs" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (appointment_id IN ( SELECT appointments.appointment_id
   FROM appointments
  WHERE ((appointments.clinic_id = rls_clinic_id()) OR (appointments.patient_id = rls_user_id()))))));

CREATE POLICY "rls_areq_insert" ON core."appointment_requests" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text, 'patient'::text])) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_areq_select" ON core."appointment_requests" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_areq_update" ON core."appointment_requests" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'receptionist'::text])) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_appt_insert" ON core."appointments" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) OR (clinic_id = rls_clinic_id())));

CREATE POLICY "rls_appt_select" ON core."appointments" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id()) OR (doctor_id = rls_user_id()) OR (ca_id = rls_user_id())));

CREATE POLICY "rls_appt_update" ON core."appointments" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) OR (clinic_id = rls_clinic_id())));

CREATE POLICY "rls_apr_insert" ON core."assessment_protocol_requests" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text])) OR (clinical_assistant_id = rls_user_id())));

CREATE POLICY "rls_apr_select" ON core."assessment_protocol_requests" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinical_assistant_id = rls_user_id()) OR (doctor_id = rls_user_id()) OR (patient_id = rls_user_id()) OR (cycle_id IN ( SELECT treatment_cycles.cycle_id
   FROM treatment_cycles
  WHERE (treatment_cycles.clinic_id = rls_clinic_id())))));

CREATE POLICY "rls_apr_update" ON core."assessment_protocol_requests" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text])) OR (clinical_assistant_id = rls_user_id()) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_audit_select" ON compliance."audit_logs" FOR SELECT TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_creq_insert" ON core."clinic_requests" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_creq_select" ON core."clinic_requests" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (region_id = rls_region_id())) OR (submitted_by = rls_user_id())));

CREATE POLICY "rls_creq_update" ON core."clinic_requests" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])));

CREATE POLICY "rls_csa_insert" ON core."clinic_staff_assignments" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (clinic_id IN ( SELECT clinics.clinic_id
   FROM clinics
  WHERE (clinics.region_id = rls_region_id())))) OR ((rls_user_role() = 'clinic_admin'::text) AND (clinic_id = rls_clinic_id()))));

CREATE POLICY "rls_csa_select" ON core."clinic_staff_assignments" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (clinic_id IN ( SELECT clinics.clinic_id
   FROM clinics
  WHERE (clinics.region_id = rls_region_id())))) OR (clinic_id = rls_clinic_id()) OR (profile_id = rls_user_id())));

CREATE POLICY "rls_csa_update" ON core."clinic_staff_assignments" FOR UPDATE TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (clinic_id IN ( SELECT clinics.clinic_id
   FROM clinics
  WHERE (clinics.region_id = rls_region_id())))) OR ((rls_user_role() = 'clinic_admin'::text) AND (clinic_id = rls_clinic_id()))));

CREATE POLICY "rls_ca_insert" ON core."clinical_assistants" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_ca_select" ON core."clinical_assistants" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (profile_id = rls_user_id()) OR (profile_id IN ( SELECT clinic_staff_assignments.profile_id
   FROM clinic_staff_assignments
  WHERE ((clinic_staff_assignments.clinic_id = rls_clinic_id()) AND (clinic_staff_assignments.is_active = true))))));

CREATE POLICY "rls_ca_update" ON core."clinical_assistants" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])) OR (profile_id = rls_user_id())));

CREATE POLICY "rls_clinics_insert" ON core."clinics" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_clinics_select" ON core."clinics" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (region_id = rls_region_id())) OR (clinic_id = rls_clinic_id()) OR (status <> ALL (ARRAY['pending_closure'::text, 'closed'::text]))));

CREATE POLICY "rls_clinics_update" ON core."clinics" FOR UPDATE TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (region_id = rls_region_id())) OR ((rls_user_role() = 'clinic_admin'::text) AND (clinic_id = rls_clinic_id()))));

CREATE POLICY "rls_cr_insert" ON compliance."consent_records" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text, 'receptionist'::text])) OR (rls_user_role() IS NULL)));

CREATE POLICY "rls_cr_select" ON compliance."consent_records" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND ((region_id = rls_region_id()) OR (clinic_id IN ( SELECT clinics.clinic_id
   FROM clinics
  WHERE (clinics.region_id = rls_region_id()))))) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (clinic_id = rls_clinic_id())) OR (patient_id = rls_user_id()) OR (staff_id = rls_user_id())));

CREATE POLICY "rls_cr_update" ON compliance."consent_records" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])) OR (patient_id = rls_user_id()) OR (staff_id = rls_user_id())));

CREATE POLICY "rls_ct_insert" ON reference."consent_templates" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_ct_select" ON reference."consent_templates" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_ct_update" ON reference."consent_templates" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_da_insert" ON core."device_assignments" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])));

CREATE POLICY "rls_da_select" ON core."device_assignments" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_da_update" ON core."device_assignments" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])));

CREATE POLICY "rls_dpa_insert" ON core."doctor_patient_assignments" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_dpa_select" ON core."doctor_patient_assignments" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (doctor_id = rls_user_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_dpa_update" ON core."doctor_patient_assignments" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])));

CREATE POLICY "rls_dso_delete" ON core."doctor_schedule_overrides" FOR DELETE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_dso_insert" ON core."doctor_schedule_overrides" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_dso_select" ON core."doctor_schedule_overrides" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_dso_update" ON core."doctor_schedule_overrides" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_dsn_insert" ON core."doctor_session_notes" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'doctor'::text])) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_dsn_select" ON core."doctor_session_notes" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (doctor_id = rls_user_id()) OR (patient_id = rls_user_id()) OR (cycle_id IN ( SELECT treatment_cycles.cycle_id
   FROM treatment_cycles
  WHERE (treatment_cycles.clinic_id = rls_clinic_id())))));

CREATE POLICY "rls_dsn_update" ON core."doctor_session_notes" FOR UPDATE TO public
    USING (((doctor_id = rls_user_id()) OR (rls_user_role() = 'super_admin'::text)));

CREATE POLICY "rls_dws_delete" ON core."doctor_weekly_schedules" FOR DELETE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_dws_insert" ON core."doctor_weekly_schedules" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_dws_select" ON core."doctor_weekly_schedules" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_dws_update" ON core."doctor_weekly_schedules" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])) OR (doctor_id = rls_user_id())));

CREATE POLICY "rls_doctors_insert" ON core."doctors" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_doctors_select" ON core."doctors" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (profile_id = rls_user_id()) OR (profile_id IN ( SELECT clinic_staff_assignments.profile_id
   FROM clinic_staff_assignments
  WHERE ((clinic_staff_assignments.clinic_id = rls_clinic_id()) AND (clinic_staff_assignments.is_active = true))))));

CREATE POLICY "rls_doctors_update" ON core."doctors" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])) OR (profile_id = rls_user_id())));

CREATE POLICY "rls_inventory_insert" ON core."inventory" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_inventory_select" ON core."inventory" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id())));

CREATE POLICY "rls_inventory_update" ON core."inventory" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_notif_insert" ON core."notifications" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])));

CREATE POLICY "rls_notif_select" ON core."notifications" FOR SELECT TO public
    USING (((recipient_id = rls_user_id()) OR (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR ((rls_user_role() = 'clinic_admin'::text) AND (clinic_id = rls_clinic_id()))));

CREATE POLICY "rls_notif_update" ON core."notifications" FOR UPDATE TO public
    USING (((recipient_id = rls_user_id()) OR (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text]))));

CREATE POLICY "rls_oi_select" ON core."order_items" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (order_id IN ( SELECT store_orders.order_id
   FROM store_orders
  WHERE (store_orders.clinic_id = rls_clinic_id())))));

CREATE POLICY "rls_pct_insert" ON core."patient_clinic_transfers" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_pct_select" ON core."patient_clinic_transfers" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (from_clinic_id = rls_clinic_id()) OR (to_clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_pct_update" ON core."patient_clinic_transfers" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_pds_insert" ON core."patient_disease_selection" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_pds_select" ON core."patient_disease_selection" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (patient_id = rls_user_id()) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (patient_id IN ( SELECT patients.profile_id
   FROM patients
  WHERE (patients.primary_clinic_id = rls_clinic_id()))))));

CREATE POLICY "rls_pds_update" ON core."patient_disease_selection" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_eeg_insert" ON core."patient_eeg_files" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text])) OR (clinic_id = rls_clinic_id())));

CREATE POLICY "rls_eeg_select" ON core."patient_eeg_files" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_eeg_update" ON core."patient_eeg_files" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (clinic_id = rls_clinic_id())));

CREATE POLICY "rls_mhf_insert" ON core."patient_medical_history_files" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text, 'clinical_assistant'::text])) OR (patient_id = rls_user_id()) OR (clinic_id = rls_clinic_id())));

CREATE POLICY "rls_mhf_select" ON core."patient_medical_history_files" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_mhf_update" ON core."patient_medical_history_files" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])) OR (clinic_id = rls_clinic_id())));

CREATE POLICY "rls_psa_insert" ON core."patient_scale_assignments" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'patient'::text])));

CREATE POLICY "rls_psa_select" ON core."patient_scale_assignments" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (patient_id = rls_user_id()) OR (assigned_by = rls_user_id()) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (patient_id IN ( SELECT patients.profile_id
   FROM patients
  WHERE (patients.primary_clinic_id = rls_clinic_id()))))));

CREATE POLICY "rls_psa_update" ON core."patient_scale_assignments" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text])));

CREATE POLICY "rls_patients_insert" ON core."patients" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])) OR (rls_user_role() IS NULL)));

CREATE POLICY "rls_patients_select" ON core."patients" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (primary_clinic_id IN ( SELECT clinics.clinic_id
   FROM clinics
  WHERE (clinics.region_id = rls_region_id())))) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (primary_clinic_id = rls_clinic_id())) OR (profile_id = rls_user_id()) OR ((rls_user_role() = 'doctor'::text) AND (primary_doctor_id = rls_user_id()))));

CREATE POLICY "rls_patients_update" ON core."patients" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text, 'clinical_assistant'::text])) OR (profile_id = rls_user_id())));

CREATE POLICY "rls_payments_insert" ON core."payments" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])));

CREATE POLICY "rls_payments_select" ON core."payments" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])) OR (session_id IN ( SELECT sessions.session_id
   FROM sessions
  WHERE (sessions.clinic_id = rls_clinic_id()))) OR (order_id IN ( SELECT store_orders.order_id
   FROM store_orders
  WHERE (store_orders.clinic_id = rls_clinic_id())))));

CREATE POLICY "rls_payments_update" ON core."payments" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_products_insert" ON reference."products" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_products_select" ON reference."products" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_products_update" ON reference."products" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_profiles_insert" ON core."profiles" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text, 'receptionist'::text, 'patient'::text])) OR ((rls_user_role() IS NULL) AND (role = 'patient'::text))));

CREATE POLICY "rls_profiles_select" ON core."profiles" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR (rls_user_role() = 'regional_admin'::text) OR (id = rls_user_id()) OR (cognito_sub = rls_cognito_sub()) OR (email = rls_email()) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (id IN ( SELECT clinic_staff_assignments.profile_id
   FROM clinic_staff_assignments
  WHERE ((clinic_staff_assignments.clinic_id = rls_clinic_id()) AND (clinic_staff_assignments.is_active = true))
UNION
 SELECT patients.profile_id
   FROM patients
  WHERE (patients.primary_clinic_id = rls_clinic_id())))) OR ((rls_user_role() = 'patient'::text) AND (id IN ( SELECT clinic_staff_assignments.profile_id
   FROM clinic_staff_assignments
  WHERE ((clinic_staff_assignments.clinic_id = rls_clinic_id()) AND (clinic_staff_assignments.is_active = true)))))));

CREATE POLICY "rls_profiles_update" ON core."profiles" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])) OR (id = rls_user_id()) OR ((rls_user_role() = ANY (ARRAY['receptionist'::text, 'clinical_assistant'::text])) AND (id IN ( SELECT patients.profile_id
   FROM patients
  WHERE (patients.primary_clinic_id = rls_clinic_id()))))));

CREATE POLICY "rls_pai_insert" ON core."prs_assessment_instances" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'receptionist'::text, 'doctor'::text])) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_pai_select" ON core."prs_assessment_instances" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (patient_id = rls_user_id()) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND ((patient_id IN ( SELECT patients.profile_id
   FROM patients
  WHERE (patients.primary_clinic_id = rls_clinic_id()))) OR (cycle_id IN ( SELECT treatment_cycles.cycle_id
   FROM treatment_cycles
  WHERE (treatment_cycles.clinic_id = rls_clinic_id())))))));

CREATE POLICY "rls_pai_update" ON core."prs_assessment_instances" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_prs_dqmap_select" ON reference."prs_disease_question_map" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_prs_dqmap_write" ON reference."prs_disease_question_map" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_prs_dsmap_select" ON reference."prs_disease_scale_map" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_prs_dsmap_write" ON reference."prs_disease_scale_map" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_prs_diseases_select" ON reference."prs_diseases" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_prs_diseases_write" ON reference."prs_diseases" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_pfr_insert" ON core."prs_final_results" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id())))));

CREATE POLICY "rls_pfr_select" ON core."prs_final_results" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id()))) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE ((prs_assessment_instances.patient_id IN ( SELECT patients.profile_id
           FROM patients
          WHERE (patients.primary_clinic_id = rls_clinic_id()))) OR (prs_assessment_instances.cycle_id IN ( SELECT treatment_cycles.cycle_id
           FROM treatment_cycles
          WHERE (treatment_cycles.clinic_id = rls_clinic_id())))))))));

CREATE POLICY "rls_pfr_update" ON core."prs_final_results" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id())))));

CREATE POLICY "rls_prs_opts_select" ON reference."prs_options" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_prs_opts_write" ON reference."prs_options" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_prs_questions_select" ON reference."prs_questions" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_prs_questions_write" ON reference."prs_questions" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_prs_resp_insert" ON core."prs_responses" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id())))));

CREATE POLICY "rls_prs_resp_select" ON core."prs_responses" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id()))) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE ((prs_assessment_instances.patient_id IN ( SELECT patients.profile_id
           FROM patients
          WHERE (patients.primary_clinic_id = rls_clinic_id()))) OR (prs_assessment_instances.cycle_id IN ( SELECT treatment_cycles.cycle_id
           FROM treatment_cycles
          WHERE (treatment_cycles.clinic_id = rls_clinic_id())))))))));

CREATE POLICY "rls_prs_resp_update" ON core."prs_responses" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id())))));

CREATE POLICY "rls_prs_sqmap_select" ON reference."prs_scale_question_map" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_prs_sqmap_write" ON reference."prs_scale_question_map" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_psr_insert" ON core."prs_scale_results" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'clinical_assistant'::text, 'doctor'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id())))));

CREATE POLICY "rls_psr_select" ON core."prs_scale_results" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id()))) OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text])) AND (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE ((prs_assessment_instances.patient_id IN ( SELECT patients.profile_id
           FROM patients
          WHERE (patients.primary_clinic_id = rls_clinic_id()))) OR (prs_assessment_instances.cycle_id IN ( SELECT treatment_cycles.cycle_id
           FROM treatment_cycles
          WHERE (treatment_cycles.clinic_id = rls_clinic_id())))))))));

CREATE POLICY "rls_psr_update" ON core."prs_scale_results" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'doctor'::text])) OR (instance_id IN ( SELECT prs_assessment_instances.instance_id
   FROM prs_assessment_instances
  WHERE (prs_assessment_instances.patient_id = rls_user_id())))));

CREATE POLICY "rls_prs_scales_select" ON reference."prs_scales" FOR SELECT TO public
    USING (true);

CREATE POLICY "rls_prs_scales_write" ON reference."prs_scales" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_recep_insert" ON core."receptionists" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_recep_select" ON core."receptionists" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (profile_id = rls_user_id()) OR (profile_id IN ( SELECT clinic_staff_assignments.profile_id
   FROM clinic_staff_assignments
  WHERE ((clinic_staff_assignments.clinic_id = rls_clinic_id()) AND (clinic_staff_assignments.is_active = true))))));

CREATE POLICY "rls_recep_update" ON core."receptionists" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])) OR (profile_id = rls_user_id())));

CREATE POLICY "rls_regions_insert" ON core."regions" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));

CREATE POLICY "rls_regions_select" ON core."regions" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR (region_id = rls_region_id()) OR (is_active = true)));

CREATE POLICY "rls_regions_update" ON core."regions" FOR UPDATE TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (region_id = rls_region_id()))));

CREATE POLICY "rls_sessions_insert" ON core."sessions" FOR INSERT TO public
    WITH CHECK (((clinic_id = rls_clinic_id()) OR (rls_user_role() = 'super_admin'::text)));

CREATE POLICY "rls_sessions_select" ON core."sessions" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (clinic_id IN ( SELECT clinics.clinic_id
   FROM clinics
  WHERE (clinics.region_id = rls_region_id())))) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_sessions_update" ON core."sessions" FOR UPDATE TO public
    USING (((clinic_id = rls_clinic_id()) OR (rls_user_role() = 'super_admin'::text)));

CREATE POLICY "rls_sreq_insert" ON core."staff_requests" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_sreq_select" ON core."staff_requests" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (submitted_by = rls_user_id())));

CREATE POLICY "rls_sreq_update" ON core."staff_requests" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_st_insert" ON core."stock_transfers" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_st_select" ON core."stock_transfers" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (from_clinic_id = rls_clinic_id()) OR (to_clinic_id = rls_clinic_id())));

CREATE POLICY "rls_st_update" ON core."stock_transfers" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text])));

CREATE POLICY "rls_so_insert" ON core."store_orders" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'receptionist'::text])));

CREATE POLICY "rls_so_select" ON core."store_orders" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_so_update" ON core."store_orders" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'receptionist'::text])));

CREATE POLICY "rls_cycles_insert" ON core."treatment_cycles" FOR INSERT TO public
    WITH CHECK (((clinic_id = rls_clinic_id()) OR (rls_user_role() = 'super_admin'::text)));

CREATE POLICY "rls_cycles_select" ON core."treatment_cycles" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'regional_admin'::text) AND (clinic_id IN ( SELECT clinics.clinic_id
   FROM clinics
  WHERE (clinics.region_id = rls_region_id())))) OR (clinic_id = rls_clinic_id()) OR (patient_id = rls_user_id())));

CREATE POLICY "rls_cycles_update" ON core."treatment_cycles" FOR UPDATE TO public
    USING (((clinic_id = rls_clinic_id()) OR (rls_user_role() = 'super_admin'::text)));

CREATE POLICY "rls_tp_insert" ON core."treatment_plans" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'doctor'::text) AND (doctor_id = rls_user_id()) AND (cycle_id IN ( SELECT treatment_cycles.cycle_id
   FROM treatment_cycles
  WHERE (treatment_cycles.clinic_id = rls_clinic_id()))))));

CREATE POLICY "rls_tp_select" ON core."treatment_plans" FOR SELECT TO public
    USING (((rls_user_role() = 'super_admin'::text) OR (patient_id = rls_user_id()) OR (doctor_id = rls_user_id()) OR (cycle_id IN ( SELECT treatment_cycles.cycle_id
   FROM treatment_cycles
  WHERE (treatment_cycles.clinic_id = rls_clinic_id())))));

CREATE POLICY "rls_tp_update" ON core."treatment_plans" FOR UPDATE TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'doctor'::text) AND (doctor_id = rls_user_id()))));

CREATE POLICY "rls_ts_insert" ON core."treatment_sessions" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'clinical_assistant'::text) AND (ca_id = rls_user_id())) OR ((rls_user_role() = 'clinic_admin'::text) AND (plan_id IN ( SELECT tp.plan_id
   FROM (treatment_plans tp
     JOIN treatment_cycles tc ON ((tc.cycle_id = tp.cycle_id)))
  WHERE (tc.clinic_id = rls_clinic_id()))))));

CREATE POLICY "rls_ts_select" ON core."treatment_sessions" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])) OR (ca_id = rls_user_id()) OR (patient_id = rls_user_id()) OR (plan_id IN ( SELECT treatment_plans.plan_id
   FROM treatment_plans
  WHERE (treatment_plans.doctor_id = rls_user_id())))));

CREATE POLICY "rls_ts_update" ON core."treatment_sessions" FOR UPDATE TO public
    USING (((rls_user_role() = 'super_admin'::text) OR ((rls_user_role() = 'clinical_assistant'::text) AND (ca_id = rls_user_id())) OR ((rls_user_role() = 'clinic_admin'::text) AND (plan_id IN ( SELECT tp.plan_id
   FROM (treatment_plans tp
     JOIN treatment_cycles tc ON ((tc.cycle_id = tp.cycle_id)))
  WHERE (tc.clinic_id = rls_clinic_id()))))));

-- Layer 5 policies (file 23)
-- Layer 5 — RLS. Verified against source: profiles.role and admins.admin_type
-- mirror each other for admin identities (both hold 'super_admin' etc.
-- consistently). Provisioning a Grievance Officer (Section 11) means setting
-- BOTH profiles.role='grievance_officer' AND admins.admin_type='grievance_officer',
-- matching the existing pattern — rls_user_role() reads from profiles.role.

ALTER TABLE compliance."erasure_requests" ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance."erasure_requests" FORCE ROW LEVEL SECURITY;
ALTER TABLE compliance."erasure_request_items" ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance."erasure_request_items" FORCE ROW LEVEL SECURITY;
ALTER TABLE compliance."data_portability_requests" ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance."data_portability_requests" FORCE ROW LEVEL SECURITY;
ALTER TABLE compliance."staff_termination_authorizations" ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance."staff_termination_authorizations" FORCE ROW LEVEL SECURITY;
ALTER TABLE compliance."compliance_incidents" ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance."compliance_incidents" FORCE ROW LEVEL SECURITY;
ALTER TABLE compliance."manual_snapshots" ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance."manual_snapshots" FORCE ROW LEVEL SECURITY;

-- erasure_requests: patient can file + see their own; super_admin and the
-- grievance officer (the named accountable role for these requests, Section 11)
-- see and process all.
CREATE POLICY "rls_erasure_req_select" ON compliance."erasure_requests" FOR SELECT TO public
    USING ((patient_id = rls_user_id()) OR (rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text])));
CREATE POLICY "rls_erasure_req_insert" ON compliance."erasure_requests" FOR INSERT TO public
    WITH CHECK ((patient_id = rls_user_id()) OR (rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text])));
CREATE POLICY "rls_erasure_req_update" ON compliance."erasure_requests" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text])));

-- erasure_request_items: classification-pass detail. Patient can see (transparency —
-- "patient is told what is retained, why, and when it will be deleted", Section 3.5),
-- but only the grievance officer / super_admin classify and write.
CREATE POLICY "rls_erasure_items_select" ON compliance."erasure_request_items" FOR SELECT TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text]))
        OR (EXISTS (SELECT 1 FROM compliance.erasure_requests er WHERE er.request_id = erasure_request_items.request_id AND er.patient_id = rls_user_id())));
CREATE POLICY "rls_erasure_items_insert" ON compliance."erasure_request_items" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text])));
CREATE POLICY "rls_erasure_items_update" ON compliance."erasure_request_items" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text])));

-- data_portability_requests: same self-service + grievance-officer pattern as erasure.
CREATE POLICY "rls_dpr_select" ON compliance."data_portability_requests" FOR SELECT TO public
    USING ((patient_id = rls_user_id()) OR (rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text])));
CREATE POLICY "rls_dpr_insert" ON compliance."data_portability_requests" FOR INSERT TO public
    WITH CHECK ((patient_id = rls_user_id()) OR (rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text])));
CREATE POLICY "rls_dpr_update" ON compliance."data_portability_requests" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'grievance_officer'::text])));

-- staff_termination_authorizations: admin-tier only, never patient-visible.
-- clinic_admin included for insert (initiates voluntary/no-cause) but not select-all —
-- kept simple/conservative: only super_admin and regional_admin see the full log.
CREATE POLICY "rls_staff_term_select" ON compliance."staff_termination_authorizations" FOR SELECT TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])));
CREATE POLICY "rls_staff_term_insert" ON compliance."staff_termination_authorizations" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));
CREATE POLICY "rls_staff_term_update" ON compliance."staff_termination_authorizations" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])));

-- compliance_incidents: super_admin only — breach handling is the most sensitive
-- record type in this schema, deliberately narrow.
CREATE POLICY "rls_incidents_select" ON compliance."compliance_incidents" FOR SELECT TO public
    USING ((rls_user_role() = 'super_admin'::text));
CREATE POLICY "rls_incidents_insert" ON compliance."compliance_incidents" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = 'super_admin'::text));
CREATE POLICY "rls_incidents_update" ON compliance."compliance_incidents" FOR UPDATE TO public
    USING ((rls_user_role() = 'super_admin'::text));

-- manual_snapshots: infra operation, admin-tier.
CREATE POLICY "rls_snapshots_select" ON compliance."manual_snapshots" FOR SELECT TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])));
CREATE POLICY "rls_snapshots_insert" ON compliance."manual_snapshots" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])));
CREATE POLICY "rls_snapshots_update" ON compliance."manual_snapshots" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text])));

-- Webhook system-role fix (file 25) and lockout fixes (file 26)
-- Fix: Razorpay webhook has no logged-in user, so app.current_user_role was
-- never set on its DB session — rls_user_role() returned NULL, and neither
-- payments nor treatment_sessions had a policy matching NULL, so the
-- webhook's own UPDATE silently affected 0 rows (FORCE RLS + no matching
-- policy = 0 rows, not an error). Fix: a dedicated 'system' role value,
-- distinct from 'super_admin' so the audit trail honestly shows an
-- unattended system write, not a human admin action. Paired with a
-- SET LOCAL app.current_user_role = 'system' in payments/service.py's
-- handle_webhook() — this policy change alone does nothing without that.

-- SELECT too — the webhook looks the payment up by razorpay_order_id
-- (get_by_razorpay_order_id) before it can update it. 'system' needs both.
ALTER POLICY "rls_payments_select" ON core."payments"
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text, 'system'::text]))
        OR (session_id IN (SELECT sessions.session_id FROM sessions WHERE sessions.clinic_id = rls_clinic_id()))
        OR (order_id IN (SELECT store_orders.order_id FROM store_orders WHERE store_orders.clinic_id = rls_clinic_id()))
    );

ALTER POLICY "rls_payments_update" ON core."payments"
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'system'::text])));

ALTER POLICY "rls_ts_update" ON core."treatment_sessions"
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'system'::text]))
        OR ((rls_user_role() = 'clinical_assistant'::text) AND (ca_id = rls_user_id()))
        OR ((rls_user_role() = 'clinic_admin'::text) AND (plan_id IN (
            SELECT tp.plan_id FROM treatment_plans tp
            JOIN treatment_cycles tc ON tc.cycle_id = tp.cycle_id
            WHERE tc.clinic_id = rls_clinic_id()
        )))
    );

-- Fix: 16_rls_enable.sql blanket-enabled FORCE ROW LEVEL SECURITY on all 61
-- tables in SCHEMA_MAP without checking which ones source deliberately left
-- RLS off for. Found via testing the payments webhook fix, which cascaded
-- through emit_event() into outbox_events and surfaced this. 4 tables ended
-- up with RLS forced and ZERO policies — completely locked out for anava_app
-- (only the postgres/bypass role could touch them, which is exactly why the
-- earlier data migration into these tables succeeded despite this bug: it
-- ran as postgres, never as anava_app). alembic_version/schema_migrations
-- also hit this but are unused in v1 (excluded from migration) — not fixed,
-- not a real gap.

-- outbox_events: pure internal event-bus plumbing, written by every module's
-- emit_event() call on behalf of whichever actor is currently acting
-- (including 'system', post-webhook-fix). Not patient-sensitive data itself —
-- no need to scope WHO wrote an event, only that something did.
CREATE POLICY "rls_outbox_insert" ON ops."outbox_events" FOR INSERT TO public
    WITH CHECK (rls_user_role() IS NOT NULL);
CREATE POLICY "rls_outbox_select" ON ops."outbox_events" FOR SELECT TO public
    USING (rls_user_role() = ANY (ARRAY['super_admin'::text, 'system'::text]));

-- prs_option_translations / prs_question_translations: mirrors the exact
-- pattern already used by their non-translation siblings (prs_options,
-- prs_questions) — public read (i18n catalogue data, not sensitive),
-- super_admin-only write.
CREATE POLICY "rls_pot_select" ON reference."prs_option_translations" FOR SELECT TO public
    USING (true);
CREATE POLICY "rls_pot_write" ON reference."prs_option_translations" FOR INSERT TO public
    WITH CHECK (rls_user_role() = 'super_admin'::text);
CREATE POLICY "rls_pqt_select" ON reference."prs_question_translations" FOR SELECT TO public
    USING (true);
CREATE POLICY "rls_pqt_write" ON reference."prs_question_translations" FOR INSERT TO public
    WITH CHECK (rls_user_role() = 'super_admin'::text);

-- ca_doctor_assignments: this was a STATED design decision (NOTES.md — "gets
-- RLS here, it did NOT have RLS in production... uses the same policy pattern
-- as clinic_staff_assignments/doctor_patient_assignments") that was only
-- half-implemented — RLS got enabled but the actual policies were never
-- written. Mirrors doctor_patient_assignments, the closer sibling in shape.
CREATE POLICY "rls_cda_select" ON core."ca_doctor_assignments" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (clinic_id = rls_clinic_id())
        OR (ca_id = rls_user_id())
        OR (doctor_id = rls_user_id())
    );
CREATE POLICY "rls_cda_insert" ON core."ca_doctor_assignments" FOR INSERT TO public
    WITH CHECK (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text]));
CREATE POLICY "rls_cda_update" ON core."ca_doctor_assignments" FOR UPDATE TO public
    USING (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text]));
-- ---- new ----
-- --- reference catalogue: public read, super_admin write ---------------------
-- Generated in a loop rather than 51 hand-written statements: the policy body
-- is identical for all 17 catalogue tables, and repeating it by hand is how
-- one table quietly ends up with a different rule.
DO $$
DECLARE
    t TEXT;
    short TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'neuromod_devices', 'neuromod_conditions', 'neuromod_diagnoses',
        'neuromod_scales', 'neuromod_condition_scales',
        'tdcs_placements', 'hd_tdcs_placements', 'tavns_placements',
        'tps_placements', 'rtms_placements', 'other_placements',
        'tdcs_dosing', 'hd_tdcs_dosing', 'tavns_dosing',
        'tps_dosing', 'rtms_dosing', 'other_dosing'
    ] LOOP
        short := replace(replace(t, 'neuromod_', 'nm_'), 'placements', 'pl');
        EXECUTE format(
            'CREATE POLICY %I ON reference.%I FOR SELECT TO public USING (true)',
            'rls_' || short || '_select', t);
        EXECUTE format(
            'CREATE POLICY %I ON reference.%I FOR INSERT TO public WITH CHECK (rls_user_role() = ''super_admin''::text)',
            'rls_' || short || '_insert', t);
        EXECUTE format(
            'CREATE POLICY %I ON reference.%I FOR UPDATE TO public USING (rls_user_role() = ''super_admin''::text)',
            'rls_' || short || '_update', t);
    END LOOP;
END
$$;

-- --- core: clinical access pattern -------------------------------------------
-- treatment_plans has no clinic_id, so clinic scoping resolves through the
-- plan's patient, matching how rls_anamnesis_select scopes in 17_rls_policies.sql.

CREATE POLICY "rls_tprot_select" ON core."treatment_protocols" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (plan_id IN ( SELECT treatment_plans.plan_id
             FROM treatment_plans
            WHERE (treatment_plans.patient_id = rls_user_id())))
        OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text]))
            AND (plan_id IN ( SELECT treatment_plans.plan_id
                 FROM treatment_plans
                WHERE (treatment_plans.patient_id IN ( SELECT patients.profile_id
                         FROM patients
                        WHERE (patients.primary_clinic_id = rls_clinic_id()))))))));

CREATE POLICY "rls_tprot_insert" ON core."treatment_protocols" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'doctor'::text])));

CREATE POLICY "rls_tprot_update" ON core."treatment_protocols" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'doctor'::text])));

CREATE POLICY "rls_psess_select" ON core."protocol_sessions" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text]))
        OR (protocol_id IN ( SELECT tp.protocol_id
             FROM (treatment_protocols tp JOIN treatment_plans pl ON ((pl.plan_id = tp.plan_id)))
            WHERE (pl.patient_id = rls_user_id())))));

CREATE POLICY "rls_psess_insert" ON core."protocol_sessions" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'doctor'::text])));

CREATE POLICY "rls_psess_update" ON core."protocol_sessions" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'doctor'::text, 'clinical_assistant'::text])));

CREATE POLICY "rls_pfup_select" ON core."protocol_followups" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text]))
        OR (protocol_id IN ( SELECT tp.protocol_id
             FROM (treatment_protocols tp JOIN treatment_plans pl ON ((pl.plan_id = tp.plan_id)))
            WHERE (pl.patient_id = rls_user_id())))));

CREATE POLICY "rls_pfup_insert" ON core."protocol_followups" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'doctor'::text])));

CREATE POLICY "rls_pfup_update" ON core."protocol_followups" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'doctor'::text, 'clinical_assistant'::text])));

CREATE POLICY "rls_dsprs_select" ON core."device_session_prs_responses" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (patient_id = rls_user_id())
        OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text]))
            AND (patient_id IN ( SELECT patients.profile_id
                 FROM patients
                WHERE (patients.primary_clinic_id = rls_clinic_id()))))));

CREATE POLICY "rls_dsprs_insert" ON core."device_session_prs_responses" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinical_assistant'::text, 'doctor'::text]))
        OR (patient_id = rls_user_id())));

CREATE POLICY "rls_dsprs_update" ON core."device_session_prs_responses" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinical_assistant'::text, 'doctor'::text]))
        OR (patient_id = rls_user_id())));

CREATE POLICY "rls_fuprs_select" ON core."followup_prs_responses" FOR SELECT TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text]))
        OR (patient_id = rls_user_id())
        OR ((rls_user_role() = ANY (ARRAY['clinic_admin'::text, 'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text]))
            AND (patient_id IN ( SELECT patients.profile_id
                 FROM patients
                WHERE (patients.primary_clinic_id = rls_clinic_id()))))));

CREATE POLICY "rls_fuprs_insert" ON core."followup_prs_responses" FOR INSERT TO public
    WITH CHECK (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinical_assistant'::text, 'doctor'::text]))
        OR (patient_id = rls_user_id())));

CREATE POLICY "rls_fuprs_update" ON core."followup_prs_responses" FOR UPDATE TO public
    USING (((rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinical_assistant'::text, 'doctor'::text]))
        OR (patient_id = rls_user_id())));


-- ###########################################################################
-- §19  LAYER 3 — Grants
-- ###########################################################################

-- ---- existing ----

-- anava_app: full DML on core/compliance/ops, read on reference/analytics (RLS-scoped throughout)
GRANT USAGE ON SCHEMA core, reference, compliance, analytics, ops TO anava_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO anava_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA compliance TO anava_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ops TO anava_app;
GRANT SELECT ON ALL TABLES IN SCHEMA reference TO anava_app;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO anava_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core, ops TO anava_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA core, ops TO anava_app;

-- anava_readonly: SELECT-only everywhere. No RLS bypass (not superuser, not BYPASSRLS).
GRANT USAGE ON SCHEMA core, reference, compliance, analytics, ops TO anava_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA core, reference, compliance, analytics, ops TO anava_readonly;

-- anava_compliance: SELECT/UPDATE on compliance schema only.
GRANT USAGE ON SCHEMA compliance TO anava_compliance;
GRANT SELECT, UPDATE ON ALL TABLES IN SCHEMA compliance TO anava_compliance;

-- Default privileges so future tables in each schema inherit the same grants automatically.
ALTER DEFAULT PRIVILEGES IN SCHEMA core, compliance, ops GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO anava_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA reference, analytics GRANT SELECT ON TABLES TO anava_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA core, reference, compliance, analytics, ops GRANT SELECT ON TABLES TO anava_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA compliance GRANT SELECT, UPDATE ON TABLES TO anava_compliance;

-- Layer 5 — grants on the 6 new tables. Existing ALTER DEFAULT PRIVILEGES
-- (18_grants.sql) already covers "future tables in compliance schema" for
-- anava_app/anava_readonly/anava_compliance, but that only applies to tables
-- created AFTER the default-privileges statement ran in the same session context
-- for the granting role — explicit grants here so there's no ambiguity.
GRANT SELECT, INSERT, UPDATE, DELETE ON
    compliance."erasure_requests",
    compliance."erasure_request_items",
    compliance."data_portability_requests",
    compliance."staff_termination_authorizations",
    compliance."compliance_incidents",
    compliance."manual_snapshots"
TO anava_app;

GRANT SELECT ON
    compliance."erasure_requests",
    compliance."erasure_request_items",
    compliance."data_portability_requests",
    compliance."staff_termination_authorizations",
    compliance."compliance_incidents",
    compliance."manual_snapshots"
TO anava_readonly;

GRANT SELECT, UPDATE ON
    compliance."erasure_requests",
    compliance."erasure_request_items",
    compliance."data_portability_requests",
    compliance."staff_termination_authorizations",
    compliance."compliance_incidents",
    compliance."manual_snapshots"
TO anava_compliance;
-- ---- new ----

GRANT SELECT ON
    reference."neuromod_devices", reference."neuromod_conditions",
    reference."neuromod_diagnoses", reference."neuromod_scales",
    reference."neuromod_condition_scales",
    reference."tdcs_placements", reference."hd_tdcs_placements",
    reference."tavns_placements", reference."tps_placements",
    reference."rtms_placements", reference."other_placements",
    reference."tdcs_dosing", reference."hd_tdcs_dosing",
    reference."tavns_dosing", reference."tps_dosing",
    reference."rtms_dosing", reference."other_dosing"
    TO anava_app, anava_readonly;

-- v2 Layer 3: "REVOKE DELETE on all three tables from anava_app — deletion
-- belongs to the purge worker." Applied to all five new core tables: they are
-- all Bucket 2 (anonymise, never delete-now) per §21, so the application role
-- has no legitimate reason to issue a DELETE against any of them.
GRANT SELECT, INSERT, UPDATE ON
    core."treatment_protocols", core."protocol_sessions", core."protocol_followups",
    core."device_session_prs_responses", core."followup_prs_responses"
    TO anava_app;

-- Belt and braces: 18_grants.sql's ALTER DEFAULT PRIVILEGES grants DELETE on
-- every future core table to anava_app. That default fires for these five, so
-- an explicit REVOKE is required — the GRANT above alone does not remove it.
REVOKE DELETE ON
    core."treatment_protocols", core."protocol_sessions", core."protocol_followups",
    core."device_session_prs_responses", core."followup_prs_responses"
    FROM anava_app;

-- Catalogue tables are read-only to the app (v2 Layer 3: "Fee tables are
-- read-only to the app. A booking-handler bug must not be able to reprice a
-- clinic."). Same reasoning for the neuromodulation catalogue: an application
-- bug must not be able to silently alter a prescribed dose or montage.
REVOKE INSERT, UPDATE, DELETE ON
    reference."neuromod_devices", reference."neuromod_conditions",
    reference."neuromod_diagnoses", reference."neuromod_scales",
    reference."neuromod_condition_scales",
    reference."tdcs_placements", reference."hd_tdcs_placements",
    reference."tavns_placements", reference."tps_placements",
    reference."rtms_placements", reference."other_placements",
    reference."tdcs_dosing", reference."hd_tdcs_dosing",
    reference."tavns_dosing", reference."tps_dosing",
    reference."rtms_dosing", reference."other_dosing"
    FROM anava_app;

GRANT SELECT ON
    core."treatment_protocols", core."protocol_sessions", core."protocol_followups",
    core."device_session_prs_responses", core."followup_prs_responses"
    TO anava_readonly;

GRANT EXECUTE ON FUNCTION core.fn_generate_protocol_sessions(UUID, DATE, INTEGER, TIME, TIME, INTEGER) TO anava_app;


-- ###########################################################################
-- §20  LAYER 4 — Partitioning note
-- ###########################################################################



-- ###########################################################################
-- §21  LAYER 5 — Retention classes (recorded, gated on Blocker 2)
-- ###########################################################################


COMMENT ON TABLE core."treatment_protocols"          IS 'Retention: clinical (7yr from last clinical contact). Bucket 2 — anonymise with the patient profile, do not hard-delete: the protocol is part of the clinical record.';
COMMENT ON TABLE core."protocol_sessions"            IS 'Retention: clinical (7yr). Follows its parent protocol.';
COMMENT ON TABLE core."protocol_followups"           IS 'Retention: clinical (7yr). Follows its parent protocol.';
COMMENT ON TABLE core."device_session_prs_responses" IS 'Retention: clinical (7yr). Bucket 2 — the assessment stays, patient identifiers are anonymised.';
COMMENT ON TABLE core."followup_prs_responses"       IS 'Retention: clinical (7yr). Bucket 2 — the assessment stays, patient identifiers are anonymised.';
COMMENT ON TABLE reference."neuromod_devices"        IS 'Retention: none. Static catalogue, no patient data, never purged.';


-- ###########################################################################
-- §22  SEED — device registry
-- ###########################################################################


INSERT INTO reference."neuromod_devices" ("device_code", "device_name", "modality", "phase") VALUES
    ('TDCS',    'Transcranial direct current stimulation',            'tDCS',    1),
    ('HD_TDCS', 'High-definition tDCS (4x1 ring)',                    'HD-tDCS', 1),
    ('TAVNS',   'Transcutaneous auricular vagus nerve stimulation',   'taVNS',   2),
    ('TPS',     'Transcranial pulse stimulation',                     'TPS',     2),
    ('RTMS',    'Repetitive transcranial magnetic stimulation',       'rTMS',    2),
    ('OTHER',   'Device not listed — specify in protocol notes',      'other',   2);


-- ###########################################################################
-- §23  search_path — PREREQUISITE, must already be set before this file runs
-- ###########################################################################
--
-- Every unqualified table reference inside the RLS policies, the trigger
-- functions, and the view resolves through the database-level search_path.
-- Table names are unique across all six schemas (verified — no collisions),
-- so this is safe.
--
-- This is recorded here for completeness, but it is NOT executed as part of
-- this file, for two reasons:
--
--   1. ALTER DATABASE ... SET search_path takes effect for NEW connections
--      only. Running it here would not affect the connection currently
--      executing this file, so every policy above would already have failed.
--   2. It names a specific database. The original 19_search_path.sql targets
--      "Anava_App_v1"; a fresh build per the architecture document (Section 7)
--      targets anava_v1. Hardcoding either one here would silently configure
--      the wrong database.
--
-- Run this against the target database BEFORE opening the connection that
-- executes this file — see the PREREQUISITE section in the header:
--
--   ALTER DATABASE <your_database> SET search_path =
--       core, reference, compliance, analytics, ops, extensions, public;


-- ###########################################################################
-- §24  SUMMARY
-- ###########################################################################
--
-- All counts below were read from the catalog of a database actually built by
-- this file, not counted by hand from the source.
--
-- 89 base tables (excluding partitions)
--   core        47   42 existing + 5 new (Treatment Protocol module)
--   reference   30   13 existing + 17 new (neuromodulation catalogue)
--   compliance   9   3 existing + 6 from the Layer 5 workstream
--   ops          3   outbox_events, alembic_version, schema_migrations
--   analytics    0   schema reserved, no consumer yet (architecture doc §2.3)
--   archive      0   lifecycle state, not a fixed table set
--
-- Partitioned (Layer 4): 7 tables — treatment_sessions, audit_logs,
--   activity_logs, appointment_audit_logs, notifications (monthly/yearly range).
--   These expand to 1,164 child partitions covering 2024-2028, each inheriting
--   its parent's constraints and triggers — which is why raw catalog counts of
--   foreign keys and triggers read far higher than the numbers below.
--
--   appointments and sessions are deliberately NOT partitioned: Postgres
--   requires the partition key inside every unique constraint, which would
--   force composite PKs and break 5 incoming FKs (NOTES.md, "Resolved during
--   build" #1).
--
-- Constraints: 259 foreign keys on parent tables (188 existing + 59 new + 12
--   from the Layer 5 workstream and file 27). Every one is ON DELETE RESTRICT
--   — zero CASCADE anywhere, verified by querying confdeltype. An accidental
--   clinic delete must not silently erase patients.
--
-- Access: 3 roles (anava_app, anava_readonly, anava_compliance), RLS ENABLE +
--   FORCE on every table, 245 policies, 84 triggers on parent tables,
--   6 enum types.
--
--
-- OPEN ITEMS — flagged, not silently assumed
--
--   1. fn_generate_protocol_sessions places appointments on a fixed day gap at
--      fixed times. It guarantees the fan-out SHAPE (N sessions + N/K
--      follow-ups, correctly interleaved), not calendar placement. Real
--      slotting needs doctor_weekly_schedules, doctor_schedule_overrides and
--      the excl_doctor_overlap exclusion constraint — application work.
--
--   2. The two PRS response tables are 1:1 with their visit (UNIQUE on the FK).
--      If a patient can retake a PRS within one session, that constraint must
--      relax to allow multiple rows.
--
--   3. The same-device rule (placement and dosing must belong to the protocol's
--      device_id) is a TRIGGER, not a CHECK, because a CHECK cannot read
--      another table. This is the cost of the per-device table split.
--
--   4. Cross-device reporting — "every placement this patient has had" — is a
--      6-way UNION rather than a single join. Same trade.
--
--   5. Enum conversion for the ~20 pre-existing TEXT status columns
--      (appointments.status, patients.registration_status, ...) remains
--      deferred pending the literal-value audit NOTES.md describes. New columns
--      in this file use real enums.
--
--   6. Placeholder passwords in §1 (anava_readonly, anava_compliance) must be
--      rotated via ALTER ROLE before either role is granted to anyone.
--
--   7. mrn_seq starts at 10001. At data migration it must be advanced past the
--      highest imported MRN before the app writes through it again.
--
--   8. Ongoing partition maintenance is not automated here. Something must
--      create next year's/month's partitions ahead of the current date, or
--      inserts land in the DEFAULT partition (Layer 7 operational job).
--
-- ###########################################################################
