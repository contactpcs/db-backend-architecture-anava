-- 49_protocol_plan_fix_dropped_dependents.run.sql — stripped, copy-paste-and-run version
--
-- 48's DROP COLUMN plan_id CASCADE took rls_protocol_plan_select down with it
-- (the policy's USING clause referenced plan_id). protocol_plan currently has
-- FORCE ROW LEVEL SECURITY and zero SELECT policies = every role gets zero
-- rows back, silently. Recreated below with only the instance_id branches.
--
-- Also fn_check_device_available_at_clinic (37/45) still reads NEW.plan_id in
-- its fallback branch. That field no longer exists on the row — the next
-- INSERT/UPDATE on protocol_plan crashes with "record new has no field
-- plan_id". Rewritten to resolve the clinic via instance_id only.

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
