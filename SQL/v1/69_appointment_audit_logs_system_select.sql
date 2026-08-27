-- 69_appointment_audit_logs_system_select.sql
--
-- APPLY ORDER: after 68.
--
-- THE PROBLEM (found live, 27 Aug 2026, immediately after 68)
--
-- Fixing 68 (profiles) got mark_paid() past AppointmentService.get() under
-- role='system', straight into a second, subtler RLS gap on the very next
-- line: _write_audit() -> AppointmentAuditLogRepository.create() inserts
-- into core.appointment_audit_logs WITH `RETURNING *` (repository.py's
-- fetch_one expects a row back). 44_audit_log_system_role_rls.sql already
-- gave rls_apal_insert (the INSERT policy) a 'system' branch — confirmed
-- correct and passing on its own. But INSERT ... RETURNING also implicitly
-- re-checks the table's SELECT policy against the newly-inserted row (it's
-- reading the row back), and rls_apal_select was never patched — same class
-- of gap as 68, one policy over. Confirmed by removing RETURNING from a
-- reproduction: the RLS error disappears entirely (replaced by an unrelated
-- FK error), isolating it to exactly this policy.
--
-- appointment_audit_logs is also a partitioned table (12 monthly partitions
-- + default, per NOTES.md) — ruled out as a factor: PG 18 (confirmed live
-- version) enforces the partitioned parent's policies against all
-- partitions uniformly; the partitions' own relrowsecurity=false is
-- expected and irrelevant here, this is purely the missing 'system' branch.
--
-- THE FIX
--
-- Same shape as 44's fix to rls_apal_insert and 68's fix to
-- rls_profiles_select: add 'system' to the super_admin/regional_admin
-- role-array branch, unconditional — an unattended write (webhook,
-- hold-sweeper-adjacent code) needs to read back what it just wrote
-- regardless of which clinic the appointment belongs to.

BEGIN;

DROP POLICY IF EXISTS "rls_apal_select" ON core."appointment_audit_logs";
CREATE POLICY "rls_apal_select" ON core."appointment_audit_logs" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR (appointment_id IN (
            SELECT appointments.appointment_id FROM appointments
            WHERE appointments.clinic_id = rls_clinic_id() OR appointments.patient_id = rls_user_id()
        ))
    );

COMMIT;
