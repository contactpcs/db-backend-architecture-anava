-- 48_protocol_plan_drop_plan_id.sql
--
-- Drops core.protocol_plan.plan_id. protocol_instances (45/47) is now the
-- prescription's only parent — the optional pointer to core.treatment_plans
-- that 45 kept for backward compatibility is retired.
--
-- SCOPE: protocol_plan only. core.treatment_plans, core.treatment_cycles and
-- core.treatment_sessions are NOT touched — they stay load-bearing for
-- anamnesis, PRS, EEG files, medical history, doctor notes, patient
-- transfers, appointment requests, sessions, store orders and device
-- assignments (11+ tables, see 11_foreign_keys.sql). This file's scope is
-- the protocol module only.
--
-- APPLY ORDER: after 47.
--
--
BEGIN;

ALTER TABLE core."protocol_plan" DROP COLUMN IF EXISTS "plan_id" CASCADE;

COMMENT ON TABLE core."protocol_plan" IS 'Treatment Protocol — device + montage + dosing + session plan, set once by a doctor to start a course of treatment. A CHILD of core.protocol_instances (45/47) only — 48 dropped the optional pointer to treatment_plans. Retention: clinical, Bucket 2 — anonymise with the patient profile, never hard-delete.';

COMMIT;


-- ###########################################################################
-- VERIFY — run after COMMIT
-- ###########################################################################
--
-- SELECT column_name FROM information_schema.columns
--  WHERE table_schema='core' AND table_name='protocol_plan' AND column_name='plan_id'; -- 0 rows
