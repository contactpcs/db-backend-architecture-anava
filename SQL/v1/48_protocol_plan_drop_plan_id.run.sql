-- 48_protocol_plan_drop_plan_id.run.sql — stripped, copy-paste-and-run version
BEGIN;

ALTER TABLE core."protocol_plan" DROP COLUMN IF EXISTS "plan_id" CASCADE;

COMMENT ON TABLE core."protocol_plan" IS 'Treatment Protocol — device + montage + dosing + session plan, set once by a doctor to start a course of treatment. A CHILD of core.protocol_instances (45/47) only — 48 dropped the optional pointer to treatment_plans. Retention: clinical, Bucket 2 — anonymise with the patient profile, never hard-delete.';

COMMIT;
