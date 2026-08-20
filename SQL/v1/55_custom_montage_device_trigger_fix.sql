-- 55_custom_montage_device_trigger_fix.sql
--
-- Fixes core.fn_check_protocol_device_consistency (32) so it stops
-- rejecting every custom-montage protocol (54).
--
-- SCOPE: one CREATE OR REPLACE FUNCTION. No table, column, or trigger
-- definition changes — trg_check_protocol_device_consistency (32) already
-- fires BEFORE INSERT OR UPDATE ON protocol_plan and keeps doing so; only
-- the function body changes.
--
-- APPLY ORDER: after 54. Fixes a bug 54 introduced by omission.
--
-- NOT YET EXECUTED ANYWHERE.
--
--
-- ###########################################################################
-- WHY
-- ###########################################################################
--
-- fn_check_protocol_device_consistency (32) COALESCEs across the six
-- reference.*_placements / reference.*_dosing columns to find "the
-- placement's device" and "the dosing's device", then rejects the row if
-- either disagrees with protocol_plan.device_id. 54 added a seventh
-- placement source (custom_montage_id) and made the six dosing columns
-- optional for it, but never touched this trigger — so on any row where
-- custom_montage_id is set, all six placement/dosing columns are NULL by
-- construction, the COALESCE resolves to NULL, and NULL IS DISTINCT FROM
-- device_id is unconditionally TRUE. Every custom-montage protocol insert
-- fails with "Protocol device_id ... does not match the placement's device
-- <NULL>", regardless of whether the montage's actual device_id matches.
--
-- The check the trigger performs for a custom montage is already done, in
-- Python, before this INSERT ever runs:
-- ProtocolService.create() (treatment_protocols/service.py) fetches the
-- custom_montage_id row and raises MONTAGE_DEVICE_MISMATCH if its device_id
-- disagrees with body.device_id, for exactly the same reason 32's trigger
-- exists — turning a raised exception into a readable 422 before the write.
-- This file makes the trigger agree with that check instead of
-- unconditionally failing on the one condition it can't observe (there is
-- no reference.*_placements row to read a device_id from).
--
--
-- ###########################################################################
-- WHAT THIS FILE DELIBERATELY DOES NOT DO
-- ###########################################################################
--
-- It does not weaken the trigger for the catalogue-placement path — every
-- existing catalogue-placement protocol is checked exactly as before. The
-- new branch only skips the placement/dosing device lookups when
-- custom_montage_id IS NOT NULL, which chk_protocol_plan_one_placement (54)
-- already guarantees means none of the six placement columns exist to
-- check anyway.
--
-- It does not add a redundant re-check of the custom montage's own
-- device_id against protocol_plan.device_id at the trigger level. The
-- service layer already owns that check (MONTAGE_DEVICE_MISMATCH); a CHECK
-- constraint or trigger duplicating it here would need to read
-- protocol_custom_montages, the same kind of cross-table read that made
-- this whole rule a trigger instead of a CHECK in the first place — adding
-- one more such read for a condition the service layer already guards is
-- not warranted just to make the DB independently paranoid about a write
-- path only the service layer ever uses.
--
--
BEGIN;


CREATE OR REPLACE FUNCTION core.fn_check_protocol_device_consistency()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_placement_device UUID;
    v_dosing_device    UUID;
BEGIN
    -- A custom-montage row (54) has all six placement/dosing columns NULL
    -- by construction (chk_protocol_plan_one_placement /
    -- chk_protocol_plan_dosing_requires_catalogue_placement) - there is
    -- nothing in reference.*_placements or reference.*_dosing to read a
    -- device_id from, and the service layer already verified the montage's
    -- own device_id against NEW.device_id before this INSERT ran. Skip both
    -- lookups rather than let a COALESCE-over-nothing read as a mismatch.
    IF NEW.custom_montage_id IS NOT NULL THEN
        RETURN NEW;
    END IF;

    -- Exactly one of the six is non-null (chk_protocol_plan_one_placement),
    -- so COALESCE over all six scalar subqueries yields that one's device.
    SELECT COALESCE(
        (SELECT device_id FROM reference.tdcs_placements    WHERE tdcs_placement_id    = NEW.tdcs_placement_id),
        (SELECT device_id FROM reference.hd_tdcs_placements WHERE hd_tdcs_placement_id = NEW.hd_tdcs_placement_id),
        (SELECT device_id FROM reference.tavns_placements   WHERE tavns_placement_id   = NEW.tavns_placement_id),
        (SELECT device_id FROM reference.tps_placements     WHERE tps_placement_id     = NEW.tps_placement_id),
        (SELECT device_id FROM reference.rtms_placements    WHERE rtms_placement_id    = NEW.rtms_placement_id),
        (SELECT device_id FROM reference.other_placements   WHERE other_placement_id   = NEW.other_placement_id)
    ) INTO v_placement_device;

    SELECT COALESCE(
        (SELECT device_id FROM reference.tdcs_dosing    WHERE tdcs_dosing_id    = NEW.tdcs_dosing_id),
        (SELECT device_id FROM reference.hd_tdcs_dosing WHERE hd_tdcs_dosing_id = NEW.hd_tdcs_dosing_id),
        (SELECT device_id FROM reference.tavns_dosing   WHERE tavns_dosing_id   = NEW.tavns_dosing_id),
        (SELECT device_id FROM reference.tps_dosing     WHERE tps_dosing_id     = NEW.tps_dosing_id),
        (SELECT device_id FROM reference.rtms_dosing    WHERE rtms_dosing_id    = NEW.rtms_dosing_id),
        (SELECT device_id FROM reference.other_dosing   WHERE other_dosing_id   = NEW.other_dosing_id)
    ) INTO v_dosing_device;

    IF v_placement_device IS DISTINCT FROM NEW.device_id THEN
        RAISE EXCEPTION 'Protocol device_id % does not match the placement''s device %',
            NEW.device_id, v_placement_device;
    END IF;
    IF v_dosing_device IS DISTINCT FROM NEW.device_id THEN
        RAISE EXCEPTION 'Protocol device_id % does not match the dosing''s device %',
            NEW.device_id, v_dosing_device;
    END IF;

    RETURN NEW;
END;
$function$;


COMMIT;


-- ###########################################################################
-- VERIFY — run after COMMIT
-- ###########################################################################
--
-- SELECT prosrc FROM pg_proc WHERE proname = 'fn_check_protocol_device_consistency';
--   -- body contains "IF NEW.custom_montage_id IS NOT NULL THEN RETURN NEW; END IF;"
--
-- Manual: create a protocol with custom_montage_id set (matching device) —
-- must succeed. Create one with a mismatched device_id on the request body
-- vs the montage's actual device — must fail in the SERVICE layer
-- (MONTAGE_DEVICE_MISMATCH, a 422) before this trigger is ever reached, and
-- if reached anyway (a hypothetical direct-SQL write bypassing the service)
-- this trigger now silently allows it — that gap is accepted, see file
-- header WHAT THIS FILE DELIBERATELY DOES NOT DO.
--
-- Regression: an existing catalogue-placement protocol create/update must
-- still behave exactly as before this file.
