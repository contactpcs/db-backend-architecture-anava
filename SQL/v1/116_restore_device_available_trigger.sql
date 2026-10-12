-- 116_restore_device_available_trigger.sql
--
-- APPLY ORDER: after 115. Depends on 58 (the current body of
-- core.fn_check_device_available_at_clinic()). Idempotent.
--
-- THE PROBLEM
--
-- 37 created trg_check_device_available_at_clinic on core.treatment_protocols
-- (now core.protocol_plan) as
--
--     BEFORE INSERT OR UPDATE OF "device_id", "plan_id"
--
-- A trigger with a column list depends on those columns. 48's
-- DROP COLUMN "plan_id" CASCADE therefore dropped the trigger along with the
-- column, silently. 49 put back the SELECT policy that the same CASCADE took,
-- and 49 and 58 rewrote the function, but no file recreated the trigger. The
-- function exists and nothing calls it. Found on Anava_App_v1 (built
-- 2026-10-08) by comparing every CREATE TRIGGER in SQL/v1 with pg_trigger.
--
-- The application still refuses such a protocol itself
-- (ProtocolService.create -> clinic_has_device, DEVICE_NOT_AT_CLINIC), and a
-- draft edit cannot change device_id, so nothing reaches users today. What is
-- missing is the database-level guard for writes that do not go through that
-- service: manual SQL, an import, a future code path.
--
-- THE FIX
--
-- Recreate the trigger on the columns the current function reads:
-- device_id, and instance_id, which decides the clinic (58).
--
-- Still restricted to those two columns, for the reason 37 gives: an ordinary
-- status update must keep working after a clinic retires the device.
--
-- The function is not SECURITY DEFINER, so it reads protocol_instances and
-- clinic_devices under the caller's RLS — the same rows the service's own
-- check reads in the same session, so a write the service allows passes here.
--
-- Like the original, this trigger depends on its listed columns. A later
-- DROP COLUMN ... CASCADE on device_id or instance_id would remove it again.

BEGIN;

DROP TRIGGER IF EXISTS trg_check_device_available_at_clinic ON core."protocol_plan";
CREATE TRIGGER trg_check_device_available_at_clinic
    BEFORE INSERT OR UPDATE OF "device_id", "instance_id" ON core."protocol_plan"
    FOR EACH ROW EXECUTE FUNCTION core.fn_check_device_available_at_clinic();

COMMIT;


-- VERIFY — run after COMMIT
--
-- SELECT pg_get_triggerdef(oid) FROM pg_trigger
--  WHERE tgrelid = 'core.protocol_plan'::regclass
--    AND tgname = 'trg_check_device_available_at_clinic';   -- one row
