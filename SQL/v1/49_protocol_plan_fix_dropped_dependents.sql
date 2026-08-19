-- 49_protocol_plan_fix_dropped_dependents.sql
--
-- Repairs two things 48's DROP COLUMN plan_id CASCADE broke.
--
-- APPLY ORDER: after 48.
--
--
-- ###########################################################################
-- WHY
-- ###########################################################################
--
-- 1. rls_protocol_plan_select's USING clause had two OR branches keyed on
--    plan_id (carried over from 45, never rewritten when 47/48 removed the
--    column). CASCADE took the policy down with the column. protocol_plan
--    has FORCE ROW LEVEL SECURITY and, until this file, zero SELECT
--    policies — every role, including the doctor who wrote the prescription,
--    gets zero rows back. Not an error, not a log line: SELECT just returns
--    empty, the exact silent-empty-result failure 45 §3 already fought once.
--    Recreated here with only the instance_id branches, since instance_id is
--    the sole parent as of 47.
--
-- 2. fn_check_device_available_at_clinic (37, rewritten 45 §3b) still reads
--    NEW.plan_id in its fallback branch. Functions are not tracked as
--    column-level dependents the way policies are, so CASCADE left it alone —
--    but the column it reads is gone. Postgres row variables are dynamically
--    typed, so this does not fail at CREATE FUNCTION time; it fails at the
--    next INSERT or UPDATE on protocol_plan, with "record "new" has no field
--    "plan_id"". Rewritten to resolve the clinic via instance_id only, the
--    only path that still exists.
--
--
BEGIN;

DROP POLICY IF EXISTS "rls_protocol_plan_select" ON core."protocol_plan";
CREATE POLICY "rls_protocol_plan_select" ON core."protocol_plan" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR (instance_id IN (
            SELECT i.instance_id
            FROM protocol_instances i
            JOIN treatment_cycles c ON c.cycle_id = i.cycle_id
            WHERE c.clinic_id = rls_clinic_id()
               OR i.patient_id = rls_user_id()
               OR i.created_by = rls_user_id()
               OR c.doctor_id  = rls_user_id()))
        OR (instance_id IN (
            SELECT i.instance_id
            FROM protocol_instances i
            JOIN treatment_cycles c          ON c.cycle_id = i.cycle_id
            JOIN clinic_staff_assignments s  ON s.clinic_id = c.clinic_id
            WHERE s.profile_id = rls_user_id()
              AND s.is_active))
    );

CREATE OR REPLACE FUNCTION core.fn_check_device_available_at_clinic()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_clinic_id UUID;
    v_available BOOLEAN;
BEGIN
    SELECT tc.clinic_id INTO v_clinic_id
    FROM core.protocol_instances pi
    JOIN core.treatment_cycles tc ON tc.cycle_id = pi.cycle_id
    WHERE pi.instance_id = NEW.instance_id;

    IF v_clinic_id IS NULL THEN
        RAISE EXCEPTION 'Cannot resolve a clinic for protocol instance % — protocol cannot be validated',
            NEW.instance_id
            USING ERRCODE = '23514';
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM core.clinic_devices cd
        WHERE cd.clinic_id = v_clinic_id
          AND cd.device_id = NEW.device_id
          AND cd.is_active
          AND cd.quantity > 0
    ) INTO v_available;

    IF NOT v_available THEN
        RAISE EXCEPTION 'Device % is not available at clinic %', NEW.device_id, v_clinic_id
            USING ERRCODE = '23514',
                  HINT = 'Add the device to this clinic''s inventory, or prescribe one the clinic has.';
    END IF;

    RETURN NEW;
END;
$function$;

COMMIT;


-- ###########################################################################
-- VERIFY — run after COMMIT
-- ###########################################################################
--
-- SELECT count(*) FROM pg_policies
--  WHERE schemaname='core' AND tablename='protocol_plan' AND policyname='rls_protocol_plan_select'; -- 1
-- SELECT prosrc FROM pg_proc WHERE proname='fn_check_device_available_at_clinic'; -- no "plan_id" substring
