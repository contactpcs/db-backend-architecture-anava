-- 32_drop_legacy_appointment_objects.sql
--
-- CONTRACT STEP. This file DROPS things. Read the whole header before running it.
--
-- Removes the database objects that existed only to serve the receptionist
-- approval flow and the retired fixed-session model. Both are gone from the
-- agreed model (see Anava_Appointments_Payment_Flow_Redesign, decisions D3 and
-- the four-type vocabulary), so these objects have no reader and no writer left.
--
-- ORDER MATTERS: this file must be applied AFTER the application code that
-- referenced these objects has been removed. Applying it against running code
-- that still queries core.appointment_requests will produce runtime errors, not
-- a clean failure at apply time.
--
-- APPLIED 2026-08-13 to Anava_App_v1 (sandbox RDS, PostgreSQL 18.3).
-- Preflight was clean: every table touched held 0 rows, no existing data violated
-- a new constraint, btree_gist present. Post-apply verification passed on every
-- check except one pre-existing issue unrelated to this file (two tables elsewhere
-- in the schema have RLS enabled with no policy).
--
-- SAFE TO APPLY: every object dropped here holds 0 rows. core.appointment_requests
-- was verified empty on 2026-08-11 (see 30_appointments_spine.sql header) and no
-- endpoint has written to it since. core.appointments is likewise empty, so the
-- three dropped columns carry no data.
--
-- NOT REVERSIBLE by re-running an earlier file. 05_tables_core.sql and its
-- companions are introspection output and are deliberately NOT edited here —
-- they remain the record of the schema as it was. Rebuilding from 00..29 and
-- then applying 30, 31, 32 in order reproduces the current schema exactly.


-- ---------------------------------------------------------------------------
-- 1. core.appointments — three columns with no remaining purpose
-- ---------------------------------------------------------------------------
-- DROP COLUMN also drops the column's foreign key and any index built on it,
-- so idx_appt_request_id and idx_appt_session_id disappear with their columns.
-- Naming them here anyway so a reader of this file knows what went.

-- appointment_request_id: pointed at the approval queue. With the queue gone
-- (decision D3 — patient books directly, no receptionist), an appointment can
-- no longer originate from a request, so the column can never be non-NULL.
-- Drops: fk_appointments_appointment_request_id, idx_appt_request_id.
ALTER TABLE core."appointments" DROP COLUMN IF EXISTS "appointment_request_id";

-- session_id: pointed DOWN at core.sessions, from the era when a session was
-- the parent record and an appointment hung off it. 30_appointments_spine.sql
-- inverted that — appointments is the spine and every child carries its own
-- appointment_id pointing UP. A downward pointer from the spine is exactly the
-- cycle that inversion removed.
-- Drops: fk_appointments_session_id, idx_appt_session_id.
--
-- NOTE: this drops the spine's pointer at core.sessions. It does NOT drop
-- core.sessions itself, which is still live — see section 3.
ALTER TABLE core."appointments" DROP COLUMN IF EXISTS "session_id";

-- session_phase: belonged to the fixed Session-1-to-4 model retired on the
-- 10 July flow call. The agreed model distinguishes visits by appointment_type
-- ('initial', 'follow_up', 'device_session', 'protocol_followup'), so a second
-- overlapping discriminator on the same row has nothing left to say.
--
-- Unrelated to core.sessions.session_phase, which stays — the clinical module
-- still reads and writes that one (clinical/service.py).
ALTER TABLE core."appointments" DROP COLUMN IF EXISTS "session_phase";


-- ---------------------------------------------------------------------------
-- 2. core.appointment_requests — the approval queue itself
-- ---------------------------------------------------------------------------
-- The whole table. Patients now pick a slot directly and pay for it; there is
-- no preference to submit and no decision for anyone to make.
--
-- DROP TABLE takes its own dependents with it, and every one of them existed
-- only for this table:
--     PK          appointment_requests_pkey
--     FKs (8)     clinic_id, patient_id, doctor_id, cycle_id,
--                 parent_appointment_id, approved_appointment_id,
--                 submitted_by, reviewed_by
--     indexes (5) idx_areq_clinic_status, idx_areq_doctor_status,
--                 idx_areq_patient_status, idx_areq_pref_date1,
--                 idx_areq_urgency
--     triggers (2) trg_audit_appointment_requests,
--                  trg_updated_at_appointment_requests
--     policies (3) rls_areq_insert, rls_areq_select, rls_areq_update
--
-- No CASCADE. Section 1 already removed the only inbound reference
-- (appointments.appointment_request_id); if anything else still points here,
-- this statement should fail loudly rather than quietly demolish it.
DROP TABLE IF EXISTS core."appointment_requests" RESTRICT;


-- ---------------------------------------------------------------------------
-- 3. DELIBERATELY NOT DROPPED — each has a live reader today
-- ---------------------------------------------------------------------------
-- Listing these explicitly. "Not mentioned" and "considered and kept" look the
-- same in a diff, and only one of them is a decision.
--
-- core.sessions
--     Still read and written by the clinical module
--     (clinical/repository.py:102,113,125) and read by the retention worker
--     (workers/retention_purge.py:74). Five tables still carry a session_id FK
--     to it. Retiring it is a separate contract step that needs the clinical
--     module moved first.
--
-- core.treatment_sessions
--     Overlaps the protocol workstream's planned protocol_device_sessions
--     (same plan_id, session_number, billing_type, payment_status). Which of
--     the two survives is an open question (risk R2). Dropping either before
--     that is settled risks destroying the one being kept.
--
-- appointments.cycle_id  (and idx_appt_cycle_id)
--     Not clearly dead. A protocol row can reach its cycle through
--     plan_id -> treatment_plans.cycle_id, but a patient-booked 'follow_up' has
--     no plan_id, and follow-up cycles are a live concept
--     (patients/router.py: POST /patients/{id}/followup-cycles). Kept until
--     someone confirms a follow-up appointment never needs its own cycle link.
--
-- payments.session_id
--     Still the only payment path for session-based flows until core.sessions
--     retires. chk_payments_single_target continues to allow it.
--
-- core.appointment_audit_logs
--     The new lifecycle audits exactly as the old one did. Unaffected.
--
-- appointments.ca_id, excl_ca_overlap
--     Still correct under late binding (decision D15): NULL at booking, set
--     when a clinical assistant starts a device session, at which point the
--     guard begins protecting that assistant from a double claim.
