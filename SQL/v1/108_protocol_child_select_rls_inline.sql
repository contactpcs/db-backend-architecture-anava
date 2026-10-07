-- 108_protocol_child_select_rls_inline.sql
--
-- Performance only. Who can read which row does not change.
-- (design: Documents/design_perf_round1.md)
--
-- 50 gave core.protocol_conditions / protocol_diagnoses / protocol_scales a
-- SELECT policy that calls core.fn_can_read_protocol(protocol_id), whose body
-- is
--     SELECT EXISTS (SELECT 1 FROM core.protocol_plan tp WHERE tp.protocol_id = p_protocol_id)
-- Postgres cannot inline a SQL function whose body contains a subquery, so it
-- runs the function once per row, and every run re-evaluates protocol_plan's
-- own policy from scratch (its two "instance_id IN (...)" lists are rebuilt
-- each time). Cost grows with rows read TIMES protocols in the clinic.
--
-- Measured on the live database as a receptionist, 280 appointment rows:
--     function call per row   40.0 ms
--     the same EXISTS inline   0.8 ms      (same 280 rows pass)
-- The appointments list calls it once per appointment (condition_names
-- subquery in scheduling/repository.py _APPT_SELECT): 62 of that query's
-- 72 ms.
--
-- This writes the function's body straight into the three policies. The
-- planner then evaluates protocol_plan's policy lists once per statement.
-- Reachability is still delegated to core.protocol_plan's own SELECT policy,
-- exactly as 50 intended: one place to fix, not four.
--
-- core.fn_can_read_protocol is left in place (nothing else uses it; harmless).
--
-- Plain statements, no functions or dollar quotes. Safe to run twice.
-- APPLY ORDER: after 107. No backend change depends on it.


DROP POLICY IF EXISTS "rls_protocol_conditions_select" ON core."protocol_conditions";
CREATE POLICY "rls_protocol_conditions_select" ON core."protocol_conditions" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR EXISTS (SELECT 1 FROM core.protocol_plan tp WHERE tp.protocol_id = protocol_conditions.protocol_id)
    );

DROP POLICY IF EXISTS "rls_protocol_diagnoses_select" ON core."protocol_diagnoses";
CREATE POLICY "rls_protocol_diagnoses_select" ON core."protocol_diagnoses" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR EXISTS (SELECT 1 FROM core.protocol_plan tp WHERE tp.protocol_id = protocol_diagnoses.protocol_id)
    );

DROP POLICY IF EXISTS "rls_protocol_scales_select" ON core."protocol_scales";
CREATE POLICY "rls_protocol_scales_select" ON core."protocol_scales" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR EXISTS (SELECT 1 FROM core.protocol_plan tp WHERE tp.protocol_id = protocol_scales.protocol_id)
    );


-- VERIFY (each role must see exactly the rows it saw before):
--   D:\PCS\Documents\Perf_Test\analysis\rls_visibility.py save before   -- before applying
--   D:\PCS\Documents\Perf_Test\analysis\rls_visibility.py save after    -- after applying
--   D:\PCS\Documents\Perf_Test\analysis\rls_visibility.py diff before after
--
--   SELECT tablename, policyname, qual FROM pg_policies
--   WHERE policyname IN ('rls_protocol_conditions_select', 'rls_protocol_diagnoses_select', 'rls_protocol_scales_select');
