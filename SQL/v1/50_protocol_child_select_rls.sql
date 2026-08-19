-- 50_protocol_child_select_rls.sql
--
-- Two fixes that both block protocol creation outright:
--   §1-3  the missing SELECT policies on 38's three protocol child tables
--   §4    the two appointments CHECKs that still require plan_id, which 48
--         removed from protocol_plan entirely
--
-- SCOPE: RLS policies, and two CHECK constraints on core.appointments.
-- No columns, no data.
--
-- APPLY ORDER: after 49. Renumbered from 46 — 45/46 were taken by the
-- anamnesis files, and 47 renames the table this depends on.
--
-- NOT YET EXECUTED ANYWHERE.
--
--
-- ###########################################################################
-- WHY
-- ###########################################################################
--
-- 38 gave core.protocol_conditions, core.protocol_diagnoses and
-- core.protocol_scales an INSERT, an UPDATE and a DELETE policy each — and no
-- SELECT policy. All three are ENABLE + FORCE ROW LEVEL SECURITY, so with no
-- SELECT policy no row is readable by anyone except a BYPASSRLS superuser.
--
--   protocol_conditions    rls=true/true   policies: INSERT, UPDATE, DELETE
--   protocol_diagnoses     rls=true/true   policies: INSERT, UPDATE, DELETE
--   protocol_scales        rls=true/true   policies: INSERT, UPDATE, DELETE
--
-- Compare core.appointments (a,d,r,w) and core.protocol_plan, both of
-- which have one.
--
-- Two consequences, and the first is why protocol creation fails outright:
--
--   1. INSERT ... ON CONFLICT DO NOTHING must READ the conflicting row to
--      decide whether to skip it. With no SELECT policy that read is refused,
--      and Postgres reports it as
--
--          new row violates row-level security policy
--          for table "protocol_conditions"
--
--      which reads like the INSERT policy rejected the row. It did not — the
--      INSERT policy admits 'doctor' and the caller IS a doctor. The whole
--      protocol transaction rolls back at this statement.
--
--   2. Even were the write to land, GET /treatment-protocols/{id} could never
--      show the conditions, diagnoses or scales back: every SELECT returns
--      zero rows, silently, exactly as if the doctor had recorded none.
--
-- The policies below mirror protocol_plan's own SELECT policy: a row is readable by whoever can read the protocol it belongs to. No
-- new visibility is granted — reaching these rows already requires reaching
-- the parent protocol first.


-- ⚠ If your session already failed a statement, Postgres is holding an aborted
-- transaction and will answer every command below with
--     ERROR: current transaction is aborted, commands ignored until end of
--     transaction block  (SQLSTATE 25P02)
-- That is not this file failing — nothing here has run yet. Issue ROLLBACK;
-- once, then run this file.
ROLLBACK;

BEGIN;


-- ###########################################################################
-- 1  Shared predicate
-- ###########################################################################
-- Reachability is delegated to core.protocol_plan (core.treatment_protocols
-- until 47 renamed it) rather than restated three times. That table's own
-- SELECT policy resolves the clinic through protocol_instances — 47 made
-- instance_id NOT NULL and 48 dropped plan_id — so these policies stay correct
-- if that resolution changes again. One place to fix, not four.

CREATE OR REPLACE FUNCTION core.fn_can_read_protocol(p_protocol_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $function$
    SELECT EXISTS (SELECT 1 FROM core.protocol_plan tp WHERE tp.protocol_id = p_protocol_id);
$function$;

COMMENT ON FUNCTION core.fn_can_read_protocol IS
    'True when the current RLS context can see the given protocol. SECURITY INVOKER on purpose: the EXISTS runs under the caller''s own policies, so protocol_plan''s own SELECT policy decides, and this function grants nothing by itself.';

GRANT EXECUTE ON FUNCTION core.fn_can_read_protocol(UUID) TO anava_app;
GRANT EXECUTE ON FUNCTION core.fn_can_read_protocol(UUID) TO anava_readonly;
GRANT EXECUTE ON FUNCTION core.fn_can_read_protocol(UUID) TO anava_compliance;


-- ###########################################################################
-- 2  SELECT policies
-- ###########################################################################

DROP POLICY IF EXISTS "rls_protocol_conditions_select" ON core."protocol_conditions";
CREATE POLICY "rls_protocol_conditions_select" ON core."protocol_conditions" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR core.fn_can_read_protocol("protocol_id")
    );

DROP POLICY IF EXISTS "rls_protocol_diagnoses_select" ON core."protocol_diagnoses";
CREATE POLICY "rls_protocol_diagnoses_select" ON core."protocol_diagnoses" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR core.fn_can_read_protocol("protocol_id")
    );

DROP POLICY IF EXISTS "rls_protocol_scales_select" ON core."protocol_scales";
CREATE POLICY "rls_protocol_scales_select" ON core."protocol_scales" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR core.fn_can_read_protocol("protocol_id")
    );


-- ###########################################################################
-- 3  protocol_custom_montages
-- ###########################################################################
-- Same table family, same file (38), same omission — but it has no
-- protocol_id, so it cannot delegate. A montage is readable by its author and
-- by staff of the clinic it was written at; a NULL clinic_id means
-- general-purpose and is readable by any staff member, which is what the
-- picker needs to offer it.

DROP POLICY IF EXISTS "rls_protocol_custom_montages_select" ON core."protocol_custom_montages";
CREATE POLICY "rls_protocol_custom_montages_select" ON core."protocol_custom_montages" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR ("created_by" = rls_user_id())
        OR ("clinic_id" IS NULL)
        OR ("clinic_id" = rls_clinic_id())
        OR ("clinic_id" IN (
            SELECT s.clinic_id FROM core.clinic_staff_assignments s
            WHERE s.profile_id = rls_user_id() AND s.is_active))
    );


-- ###########################################################################
-- 4  appointments still demands a plan_id that no longer exists
-- ###########################################################################
-- 48 dropped protocol_plan.plan_id: a protocol now hangs off protocol_instances
-- and nothing else. But core.appointments kept two CHECKs written when the plan
-- WAS the only parent, and both still require it on protocol-born rows:
--
--   chk_appointments_device_session_has_plan
--       CHECK (appointment_type <> 'device_session' OR plan_id IS NOT NULL)
--   chk_appointments_protocol_has_plan
--       CHECK (protocol_id IS NULL OR plan_id IS NOT NULL)
--
-- Together they make a device session unwritable: the generator has no plan_id
-- to supply, so every protocol push dies on session 1 and rolls back.
--
-- 30's own comment says what the first one meant — "A device session always
-- belongs to a PROTOCOL" — while the predicate tested plan_id, because the two
-- were the same statement then. It is rewritten here to test what it always
-- meant.
--
-- 32 added the second for a stated reason: "a protocol-born row must carry
-- plan_id, or an expired hold deletes the doctor's prescribed date instead of
-- releasing the slot." That is a rule about the SWEEPER, not about plans. The
-- sweeper now tests protocol_id directly — a row carrying protocol_id is
-- prescribed work and is reverted, never deleted (app/workers/hold_sweeper.py
-- and modules/scheduling/repository.py) — so the proxy is dropped rather than
-- rewritten.
--
-- ⚠ Those two application changes must be deployed with this file. Without
-- them an expired hold on a protocol-born row would delete a prescribed date.

ALTER TABLE core."appointments"
    DROP CONSTRAINT IF EXISTS "chk_appointments_device_session_has_plan";
ALTER TABLE core."appointments"
    DROP CONSTRAINT IF EXISTS "chk_appointments_device_session_has_protocol";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_device_session_has_protocol"
    CHECK ("appointment_type" <> 'device_session' OR "protocol_id" IS NOT NULL)
    NOT VALID;

ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "chk_appointments_protocol_has_plan";


COMMIT;

-- Validated outside the transaction: appointments is small, and keeping it
-- separate means a long scan never holds the DDL lock taken above.
ALTER TABLE core."appointments" VALIDATE CONSTRAINT "chk_appointments_device_session_has_protocol";


-- ###########################################################################
-- VERIFY — run after COMMIT
-- ###########################################################################
--
-- Every one of these four must now list a SELECT ('r') policy:
--
-- SELECT c.relname, string_agg(DISTINCT p.polcmd::text, ',') AS cmds
--   FROM pg_class c
--   JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'core'
--   LEFT JOIN pg_policy p ON p.polrelid = c.oid
--  WHERE c.relname IN ('protocol_conditions','protocol_diagnoses',
--                      'protocol_scales','protocol_custom_montages')
--  GROUP BY 1 ORDER BY 1;
--
-- Expected: a,d,r,w for each.
--
-- And the appointments CHECKs — the two *_has_plan must be GONE, the
-- _has_protocol one PRESENT:
--
-- SELECT conname FROM pg_constraint
--  WHERE conrelid = 'core.appointments'::regclass AND contype = 'c'
--    AND conname LIKE 'chk_appointments_%pl%'
--  ORDER BY conname;
--
--
-- ###########################################################################
-- WHY THIS WAS NOT CAUGHT EARLIER
-- ###########################################################################
--
-- A missing SELECT policy does not fail a test that only counts rows as a
-- superuser: psql as postgres bypasses RLS entirely, so the tables looked
-- correct from DBeaver and correct in a container provisioned and queried by
-- the migration role. It surfaces only through anava_app, inside a request,
-- with an RLS context set — which is exactly the shape of the three earlier
-- silent-empty-result bugs this module has already hit (32's plan-scoped
-- policy, the INNER JOIN on treatment_plans, and patient_id resolution).
--
-- Worth a sweep: any table with ENABLE ROW LEVEL SECURITY and no policy for
-- one of the four commands is a latent version of this.
--
--     SELECT c.relname, string_agg(DISTINCT p.polcmd::text, ',') AS cmds
--       FROM pg_class c
--       JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'core'
--       LEFT JOIN pg_policy p ON p.polrelid = c.oid
--      WHERE c.relrowsecurity
--      GROUP BY 1
--     HAVING string_agg(DISTINCT p.polcmd::text, ',') NOT LIKE '%r%'
--     ORDER BY 1;
-- ###########################################################################
