-- 94_patient_rejection_fields.sql
--
-- Gives a rejected self-registered patient its own attribution columns
-- instead of sharing approved_by/approved_at, and lets a rejected patient be
-- re-approved later without re-registering.
--
-- SCOPE: two new nullable columns on core.patients. No data change, no RLS
-- change (patients already has UPDATE policies wide enough for receptionist/
-- clinic_admin/super_admin — this just adds columns those same policies
-- already cover).
--
-- APPLY ORDER: after 93.
--
--
-- WHY
-- PatientRepository.set_approval (patients/repository.py) writes
-- approved_by + approved_at on EVERY decision, reject included — so a
-- rejected patient's row showed who "approved" it and when, which was
-- never true. There's no column to record who rejected it or when, so the
-- app service has to reuse (and corrupt) the approve columns. rejected_by/
-- rejected_at give rejection its own attribution, and both stay populated
-- afterwards even once the receptionist reconsiders and approves — a
-- readable audit trail of "rejected by X, then approved by Y", not one bit
-- of history silently overwriting another.

ALTER TABLE core."patients" ADD COLUMN IF NOT EXISTS "rejected_by" UUID;
ALTER TABLE core."patients" ADD COLUMN IF NOT EXISTS "rejected_at" TIMESTAMPTZ;

ALTER TABLE core."patients" DROP CONSTRAINT IF EXISTS "fk_patients_rejected_by";
ALTER TABLE core."patients" ADD CONSTRAINT "fk_patients_rejected_by"
    FOREIGN KEY ("rejected_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

COMMENT ON COLUMN core."patients"."approved_by" IS
    'Who most recently moved this registration to approved. NULL if it has only ever been rejected, never approved. Distinct from rejected_by (94) — a patient rejected then later approved has BOTH set, from two different decisions.';
COMMENT ON COLUMN core."patients"."approved_at" IS
    'When approved_by acted. Not touched by a rejection (94) — previously this was wrongly stamped on every decision, reject included.';
COMMENT ON COLUMN core."patients"."rejected_by" IS
    'Who most recently rejected this registration. NULL if never rejected. See approved_by.';
COMMENT ON COLUMN core."patients"."rejected_at" IS
    'When rejected_by acted.';


-- VERIFY
--  SELECT column_name FROM information_schema.columns
--  WHERE table_name = 'patients' AND column_name IN ('rejected_by', 'rejected_at');  -- 2 rows
