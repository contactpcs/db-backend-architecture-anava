-- 93_outbox_relay_system_role.sql
--
-- Lets the outbox relay (app/workers/event_relay.py) run under the ordinary
-- app login (anava_app) as RLS role 'system', instead of only working when it
-- happens to connect as the RDS master user.
--
-- SCOPE: one new policy on ops.outbox_events, one widened policy on
-- core.notifications, five read-only 'system' SELECT policies for the
-- relay's recipient lookups. No data change.
--
-- APPLY ORDER: after 92. Safe to apply before or after the backend deploy
-- that makes the relay set app.current_user_role = 'system'.
--
--
-- WHY
-- The relay connects through get_migration_engine(), which falls back to
-- DATABASE_URL (anava_app) whenever MIGRATION_DATABASE_URL is unset — the
-- normal state of a deployed API container. Under anava_app, with FORCE RLS:
--   * rls_outbox_select admitted 'system' but the relay never set a role, so
--     it saw ZERO events and idled silently — no error, no log, no popups;
--   * ops.outbox_events had NO UPDATE policy at all, so it could never mark
--     an event published (it would redeliver forever);
--   * rls_notif_insert admitted staff roles only, not 'system', so every
--     notification it tried to write was rejected.
-- The relay now sets 'system' on every transaction; these policies are what
-- that role needs.


-- Mark events published (published_at). SELECT already admits 'system'.
DROP POLICY IF EXISTS "rls_outbox_update" ON ops."outbox_events";
CREATE POLICY "rls_outbox_update" ON ops."outbox_events" FOR UPDATE TO public
    USING (rls_user_role() = ANY (ARRAY['super_admin'::text, 'system'::text]))
    WITH CHECK (rls_user_role() = ANY (ARRAY['super_admin'::text, 'system'::text]));

-- Write a notification for any recipient. Same staff roles as before, plus
-- 'system' (the relay writes on behalf of the platform, not as a person).
ALTER POLICY "rls_notif_insert" ON core."notifications"
    WITH CHECK (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'clinic_admin'::text,
                                             'doctor'::text, 'clinical_assistant'::text, 'receptionist'::text,
                                             'system'::text]));



-- Read access the relay's recipient lookups need (who to notify for a staff
-- request, a registration decision, a clinic's staff, ...). A separate
-- PERMISSIVE policy per table rather than editing each existing one:
-- permissive policies OR together, so every existing role's access is
-- exactly unchanged and 'system' gains read-only visibility. appointments,
-- profiles and outbox_events already admit 'system'.

DROP POLICY IF EXISTS "rls_admins_select_system" ON core."admins";
CREATE POLICY "rls_admins_select_system" ON core."admins" FOR SELECT TO public
    USING (rls_user_role() = 'system');

DROP POLICY IF EXISTS "rls_clinic_staff_assignments_select_system" ON core."clinic_staff_assignments";
CREATE POLICY "rls_clinic_staff_assignments_select_system" ON core."clinic_staff_assignments" FOR SELECT TO public
    USING (rls_user_role() = 'system');

DROP POLICY IF EXISTS "rls_clinics_select_system" ON core."clinics";
CREATE POLICY "rls_clinics_select_system" ON core."clinics" FOR SELECT TO public
    USING (rls_user_role() = 'system');

DROP POLICY IF EXISTS "rls_patients_select_system" ON core."patients";
CREATE POLICY "rls_patients_select_system" ON core."patients" FOR SELECT TO public
    USING (rls_user_role() = 'system');

DROP POLICY IF EXISTS "rls_staff_requests_select_system" ON core."staff_requests";
CREATE POLICY "rls_staff_requests_select_system" ON core."staff_requests" FOR SELECT TO public
    USING (rls_user_role() = 'system');


-- VERIFY
--  SELECT polname FROM pg_policy WHERE polrelid = 'ops.outbox_events'::regclass;  -- includes rls_outbox_update
--  SELECT pg_get_expr(polwithcheck, polrelid) FROM pg_policy WHERE polname = 'rls_notif_insert';  -- includes 'system'
