-- 65_clinic_hours_and_operational_status.sql
--
-- APPLY ORDER: after 64. Independent of 60-64.
--
-- THE PROBLEM
--
-- Doctor weekly schedules (core.doctor_weekly_schedules) are set with no
-- reference to when the clinic itself is open — a doctor could set hours
-- the clinic doesn't run, and there's no way to take a whole clinic offline
-- (maintenance, holiday, closure) short of manually pausing every doctor's
-- schedule one by one. Mohan's 27 Aug 2026 spec: clinic hours become the
-- ceiling doctor schedules must fit inside, and clinic_admin gets a direct
-- active/inactive toggle that blocks all scheduling and booking at that
-- clinic while it's off.
--
-- THE FIX
--
-- 1. core.clinics gains is_operational — a plain on/off flag, deliberately
--    NOT the same thing as the existing `status` lifecycle column
--    (setup/active/pending_closure/closed, super_admin-gated, has a
--    minimum-staff check to go active). That FSM answers "has this clinic
--    finished onboarding"; is_operational answers "is it open for business
--    today", flippable anytime by clinic_admin without touching onboarding.
--    Defaults TRUE so no existing clinic is retroactively locked out.
--    No RLS change needed — rls_clinics_update (17_rls_policies.sql:143-144)
--    already lets clinic_admin update their own clinic row and super_admin/
--    regional_admin update any/their-region's; this is just a new column on
--    an already-writable table.
--
-- 2. core.clinic_weekly_hours — one row per (clinic, day_of_week), open/
--    close time for that day. Mirrors doctor_weekly_schedules' day_of_week/
--    start_time/end_time shape, minus the doctor-specific slotting concepts
--    (slot_duration_minutes, break_start/end, max_appointments) a clinic's
--    hours don't need. No row for a day = that day is unset, not
--    necessarily closed (see enforcement note below) — a clinic with zero
--    rows at all has simply never configured hours yet.
--
-- ENFORCEMENT (application code, not a DB constraint — a doctor's schedule
-- row and the clinic's hours row are independent tables, nothing here can
-- cross-validate them at insert time):
--   - A clinic with ZERO clinic_weekly_hours rows has no policy set yet —
--     doctor-schedule writes are NOT gated by hours for that clinic. This is
--     a deliberate rollout safety valve: shipping this schema does not
--     retroactively break every doctor's existing schedule the moment it
--     applies. Gating turns on the first time a clinic_admin actually saves
--     an hours row for their clinic.
--   - Once a clinic has ANY hours rows, a doctor-schedule day with no
--     matching clinic_weekly_hours row is implicitly closed, and a day_of_
--     week slot outside the matching row's [start_time, end_time) is
--     rejected. Enforced in scheduling/service.py's WeeklyScheduleService.
--   - is_operational = FALSE blocks: new doctor-schedule writes, new
--     appointment creation/claim/reschedule, at that clinic — enforced in
--     scheduling/service.py's AppointmentService.create, PatientBookingService.
--     claim_slot/reschedule_own.
--
-- SAFE TO APPLY: core.clinic_weekly_hours is a new table (nothing to
-- migrate). core.clinics.is_operational is a new column with a TRUE default
-- (no existing row's behavior changes).

BEGIN;

-- ── 1. clinic operational toggle ──────────────────────────────────────────

ALTER TABLE core."clinics" ADD COLUMN IF NOT EXISTS "is_operational" BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN core."clinics"."is_operational" IS 'Day-to-day open/closed toggle, independent of the status onboarding lifecycle. FALSE blocks new doctor-schedule writes and new appointment creation/claim/reschedule at this clinic. Clinic_admin-flippable for their own clinic (rls_clinics_update already covers writes to this table).';

-- ── 2. clinic weekly operating hours ────────────────────────────────────

CREATE TABLE IF NOT EXISTS core."clinic_weekly_hours" (
    "hours_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "clinic_id" UUID NOT NULL,
    "day_of_week" SMALLINT NOT NULL,
    "start_time" TIME NOT NULL,
    "end_time" TIME NOT NULL,
    "created_by" UUID,
    "updated_by" UUID,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE core."clinic_weekly_hours" DROP CONSTRAINT IF EXISTS "clinic_weekly_hours_pkey" CASCADE;
ALTER TABLE core."clinic_weekly_hours" ADD CONSTRAINT "clinic_weekly_hours_pkey" PRIMARY KEY ("hours_id");

ALTER TABLE core."clinic_weekly_hours" DROP CONSTRAINT IF EXISTS "chk_clinic_weekly_hours_day_of_week";
ALTER TABLE core."clinic_weekly_hours"
    ADD CONSTRAINT "chk_clinic_weekly_hours_day_of_week"
    CHECK ("day_of_week" BETWEEN 0 AND 6);

ALTER TABLE core."clinic_weekly_hours" DROP CONSTRAINT IF EXISTS "chk_clinic_weekly_hours_time_order";
ALTER TABLE core."clinic_weekly_hours"
    ADD CONSTRAINT "chk_clinic_weekly_hours_time_order"
    CHECK ("end_time" > "start_time");

ALTER TABLE core."clinic_weekly_hours" DROP CONSTRAINT IF EXISTS "uq_clinic_weekly_hours_clinic_day";
ALTER TABLE core."clinic_weekly_hours" ADD CONSTRAINT "uq_clinic_weekly_hours_clinic_day" UNIQUE ("clinic_id", "day_of_week");

ALTER TABLE core."clinic_weekly_hours" DROP CONSTRAINT IF EXISTS "fk_clinic_weekly_hours_clinic_id";
ALTER TABLE core."clinic_weekly_hours"
    ADD CONSTRAINT "fk_clinic_weekly_hours_clinic_id"
    FOREIGN KEY ("clinic_id") REFERENCES core."clinics" ("clinic_id") ON DELETE CASCADE;

ALTER TABLE core."clinic_weekly_hours" DROP CONSTRAINT IF EXISTS "fk_clinic_weekly_hours_created_by";
ALTER TABLE core."clinic_weekly_hours"
    ADD CONSTRAINT "fk_clinic_weekly_hours_created_by"
    FOREIGN KEY ("created_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

ALTER TABLE core."clinic_weekly_hours" DROP CONSTRAINT IF EXISTS "fk_clinic_weekly_hours_updated_by";
ALTER TABLE core."clinic_weekly_hours"
    ADD CONSTRAINT "fk_clinic_weekly_hours_updated_by"
    FOREIGN KEY ("updated_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_clinic_weekly_hours_clinic_id ON core."clinic_weekly_hours" USING btree ("clinic_id");

DROP TRIGGER IF EXISTS trg_updated_at_clinic_weekly_hours ON core."clinic_weekly_hours";
CREATE TRIGGER trg_updated_at_clinic_weekly_hours
    BEFORE UPDATE ON core."clinic_weekly_hours"
    FOR EACH ROW EXECUTE FUNCTION ops.fn_set_updated_at();

-- RLS — read is public (a patient booking needs to see clinic hours same as
-- billable_items pricing); write mirrors rls_dws_insert/update/delete's
-- pattern exactly (17_rls_policies.sql:207-217) — broad role gate here,
-- assert_clinic_scope in the service layer is what actually confines a
-- clinic_admin/regional_admin to their own clinic/region (ADR-003: RLS is
-- defense-in-depth, not the primary check).
ALTER TABLE core."clinic_weekly_hours" ENABLE ROW LEVEL SECURITY;
ALTER TABLE core."clinic_weekly_hours" FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rls_clinic_weekly_hours_select" ON core."clinic_weekly_hours";
CREATE POLICY "rls_clinic_weekly_hours_select" ON core."clinic_weekly_hours" FOR SELECT TO public
    USING (true);

DROP POLICY IF EXISTS "rls_clinic_weekly_hours_insert" ON core."clinic_weekly_hours";
CREATE POLICY "rls_clinic_weekly_hours_insert" ON core."clinic_weekly_hours" FOR INSERT TO public
    WITH CHECK ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

DROP POLICY IF EXISTS "rls_clinic_weekly_hours_update" ON core."clinic_weekly_hours";
CREATE POLICY "rls_clinic_weekly_hours_update" ON core."clinic_weekly_hours" FOR UPDATE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

DROP POLICY IF EXISTS "rls_clinic_weekly_hours_delete" ON core."clinic_weekly_hours";
CREATE POLICY "rls_clinic_weekly_hours_delete" ON core."clinic_weekly_hours" FOR DELETE TO public
    USING ((rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text])));

GRANT SELECT ON core."clinic_weekly_hours" TO anava_app;
GRANT INSERT, UPDATE, DELETE ON core."clinic_weekly_hours" TO anava_app;
GRANT SELECT ON core."clinic_weekly_hours" TO anava_readonly;

-- No seed rows — an unset clinic is deliberately ungated (see header note),
-- not defaulted to some made-up 9-to-5.

COMMIT;
