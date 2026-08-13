-- 31_appointments_payment_states.sql
--
-- Hand-written net-new DDL (like 30_appointments_spine.sql, unlike 00-29 which
-- are verbatim introspection of the live schema).
--
-- SCOPE: core.appointments and the payment wiring that reaches it. Nothing
-- here creates or alters a protocol table — those belong to the protocol
-- workstream and are deliberately untouched.
--
-- WHAT THIS DOES
-- Locks the appointment lifecycle agreed on the flow call: a slot is selected
-- first and paid for second, and an unpaid selection expires so the slot
-- returns to the pool.
--
--   planned      doctor set a DATE at protocol setup. No slot yet, no hold.
--                appointment_date IS NOT NULL, start_time IS NULL.
--
--   selected     patient picked a slot. NOT PAID. The slot is held, and the
--                hold expires after a fixed window (hold_expires_at). A
--                patient-booked appointment is created directly in this state.
--
--   paid         payment captured. Slot locked permanently, hold cleared.
--
--   cancelled    terminal, always carries a cancellation_reason.
--   rescheduled  terminal, superseded by a new row (rescheduled_to).
--
--   after paid:  checked_in -> in_progress -> completed, or no_show.
--
-- Expiry is asymmetric, and the asymmetry is the point:
--
--   plan_id IS NULL      patient-booked. The row is DELETED. Nothing unpaid
--                        persists; the patient simply starts over from the
--                        booking screen.
--
--   plan_id IS NOT NULL  protocol-born. The row reverts to 'planned' with its
--                        time cleared. The doctor's prescribed date survives —
--                        only the slot is released. Deleting these would
--                        destroy a doctor's protocol on a payment timeout.
--
-- EXPAND ONLY — nothing is dropped. The constraints that would pin down the
-- appointment_type and status vocabularies stay deferred (see the bottom): the
-- staff booking path still writes seven legacy type values and 'confirmed'.
--
-- SAFE TO APPLY: core.appointments holds 0 rows (verified 2026-08-11 in
-- 30_appointments_spine.sql, unchanged since — no booking endpoint has
-- shipped). Every constraint below is vacuously satisfied at apply time and no
-- backfill is required.


-- ---------------------------------------------------------------------------
-- 1. The hold
-- ---------------------------------------------------------------------------
-- A 'selected' row occupies a slot it has not paid for. This column is the only
-- thing that lets that slot come back if the payment never lands.

ALTER TABLE core."appointments" ADD COLUMN IF NOT EXISTS "hold_expires_at" TIMESTAMPTZ;

-- Exactly the 'selected' rows carry an expiry, and only those.
--
-- Both directions matter. A 'selected' row with a NULL expiry blocks its slot
-- forever and no sweeper query would ever find it — a bug that stays invisible
-- until a busy doctor's calendar has silently filled with dead holds. A 'paid'
-- row with a non-NULL expiry is the mirror image: the sweeper would release a
-- slot somebody has already paid for.
ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "chk_appointments_hold";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_hold"
    CHECK (("status" = 'selected') = ("hold_expires_at" IS NOT NULL));

-- The sweeper's only query, and it never looks at any other status. Partial, so
-- the index stays roughly the size of the outstanding-hold set (small, minutes
-- of churn) rather than the whole appointment history.
CREATE INDEX IF NOT EXISTS idx_appointments_expiring_holds
    ON core."appointments" ("hold_expires_at")
    WHERE "status" = 'selected';

-- 30_appointments_spine.sql allows a missing time only for 'planned' and
-- 'cancelled'. A protocol row released by the sweeper goes back to 'planned'
-- with a NULL time, which that constraint already permits — restated here only
-- so the dependency is visible if either file is read alone. Note also
-- chk_appointments_time_pair: start_time and end_time must be nulled together.


-- ---------------------------------------------------------------------------
-- 2. Overlap guards — a held slot is as occupied as a paid one
-- ---------------------------------------------------------------------------
-- 30_appointments_spine.sql excluded 'planned' from these guards, correctly for
-- the model it was written against, where 'planned' was the only timeless state.
--
-- Under the agreed model that carve-out would become a double-booking hole. Two
-- patients could each hold 10:00 Tuesday in an unguarded state, both pay, and
-- the second payment would blow up on the constraint *after* the money was
-- taken. Guarding on start_time alone closes it: any row with a time occupies
-- its slot whatever its status, and rows without a time ('planned', and rows
-- the sweeper has released) drop out on the NULL, since NULL never compares
-- equal in a GIST exclusion.

ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "excl_doctor_overlap";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "excl_doctor_overlap"
    EXCLUDE USING gist (
        "doctor_id" WITH =,
        tsrange(("appointment_date" + "start_time"), ("appointment_date" + "end_time")) WITH &&
    )
    WHERE ("status" <> ALL (ARRAY['cancelled'::text, 'rescheduled'::text])
           AND "start_time" IS NOT NULL);

-- A device session is run by a clinical assistant, not the supervising doctor,
-- so it needs its own guard keyed on ca_id. Rows with a NULL ca_id (every
-- doctor-run appointment) drop out on their own.
ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "excl_ca_overlap";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "excl_ca_overlap"
    EXCLUDE USING gist (
        "ca_id" WITH =,
        tsrange(("appointment_date" + "start_time"), ("appointment_date" + "end_time")) WITH &&
    )
    WHERE ("status" <> ALL (ARRAY['cancelled'::text, 'rescheduled'::text])
           AND "start_time" IS NOT NULL);


-- ---------------------------------------------------------------------------
-- 3. A cancellation without a reason is not a cancellation
-- ---------------------------------------------------------------------------
-- AppointmentService already refuses this, but the payment webhook will write
-- cancellations too. Enforcing it here means every writer gets the rule,
-- present and future, instead of each one remembering it.

ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "chk_appointments_cancel_reason";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_cancel_reason"
    CHECK ("status" <> 'cancelled' OR "cancellation_reason" IS NOT NULL);


-- ---------------------------------------------------------------------------
-- 4. One live initial appointment per patient
-- ---------------------------------------------------------------------------
-- A unique index rather than a SELECT-then-INSERT in the service: two
-- concurrent taps on the booking button cannot both win a race against an
-- index, and they can both win against an application check. The service still
-- does the lookup first, only to return a readable 409 instead of a raw
-- constraint violation.
--
-- 'planned' is absent from the status list on purpose — an initial appointment
-- is never created by protocol setup, so it can never be in that state.

CREATE UNIQUE INDEX IF NOT EXISTS uq_one_active_initial_per_patient
    ON core."appointments" ("patient_id")
    WHERE "appointment_type" = 'initial'
      AND "status" IN ('selected', 'paid', 'checked_in', 'in_progress');


-- ---------------------------------------------------------------------------
-- 5. Payment provision
-- ---------------------------------------------------------------------------
-- 30_appointments_spine.sql already added payments.appointment_id, its FK, its
-- index, and chk_payments_single_target. What was missing is the guarantee that
-- a visit cannot be charged twice.
--
-- Gateway webhooks are at-least-once by design; Razorpay re-delivers on any
-- timeout. payments.idempotency_key already dedupes a retried request from our
-- side, but nothing stopped two *distinct* payment rows both reaching
-- 'captured' against the same appointment. This is the backstop.

CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_one_captured_per_appointment
    ON core."payments" ("appointment_id")
    WHERE "status" = 'captured' AND "appointment_id" IS NOT NULL;

-- rls_payments_select scopes a payment through sessions or store_orders. A
-- payment attached to an appointment matches neither branch, so once money
-- starts flowing through appointment_id, FORCE RLS + no matching policy would
-- return 0 rows — silently, not as an error. That exact failure already
-- happened once with the webhook (see 25_webhook_system_role_rls.sql); adding
-- the branch now rather than rediscovering it.
ALTER POLICY "rls_payments_select" ON core."payments"
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text,
                                      'clinic_admin'::text, 'system'::text]))
        OR (session_id IN (SELECT sessions.session_id FROM sessions WHERE sessions.clinic_id = rls_clinic_id()))
        OR (order_id IN (SELECT store_orders.order_id FROM store_orders WHERE store_orders.clinic_id = rls_clinic_id()))
        OR (appointment_id IN (SELECT a.appointment_id FROM appointments a
                               WHERE a.clinic_id = rls_clinic_id() OR a.patient_id = rls_user_id()))
    );


-- ---------------------------------------------------------------------------
-- 6. RLS on appointments — patient self-service and the hold sweeper
-- ---------------------------------------------------------------------------
-- Two gaps, both currently masked by the application's DB role being a
-- Postgres superuser (rolbypassrls=TRUE, so RLS never applies). Fixing them now
-- rather than on the day that role is downgraded and patient booking stops
-- working with no obvious cause.
--
-- Gap 1: no patient branch. rls_appt_insert/update list five staff roles and
-- then fall back to `clinic_id = rls_clinic_id()`. That fallback is dead code —
-- AuthContextMiddleware sets app.current_user_id and app.current_user_role but
-- never app.current_clinic_id, so ops.rls_clinic_id() always returns NULL and
-- the branch is never true for anyone. It is kept, unchanged, for whenever that
-- GUC does get set.
--
-- Gap 2: no 'system' branch and no DELETE policy at all. The hold sweeper runs
-- unattended, so it has no logged-in user — the same situation that made the
-- Razorpay webhook silently update 0 rows (25_webhook_system_role_rls.sql).
-- It uses the same remedy: a distinct 'system' role value, set by the worker
-- via SET LOCAL app.current_user_role = 'system', so the audit trail shows an
-- unattended system write rather than a human admin action. These policies do
-- nothing without that SET LOCAL in the worker.

DROP POLICY IF EXISTS "rls_appt_insert" ON core."appointments";
CREATE POLICY "rls_appt_insert" ON core."appointments" FOR INSERT TO public
    WITH CHECK (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text,
                                      'clinical_assistant'::text, 'receptionist'::text]))
        OR (patient_id = rls_user_id())
        OR (clinic_id = rls_clinic_id())
    );

-- Patient needs UPDATE to move their own row from 'planned' to 'selected'
-- (claiming a slot) and to cancel it. 'system' needs it to revert an expired
-- protocol hold back to 'planned'.
DROP POLICY IF EXISTS "rls_appt_update" ON core."appointments";
CREATE POLICY "rls_appt_update" ON core."appointments" FOR UPDATE TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'clinic_admin'::text, 'doctor'::text,
                                      'clinical_assistant'::text, 'receptionist'::text, 'system'::text]))
        OR (patient_id = rls_user_id())
        OR (clinic_id = rls_clinic_id())
    );

-- The sweeper must SELECT before it can UPDATE or DELETE.
DROP POLICY IF EXISTS "rls_appt_select" ON core."appointments";
CREATE POLICY "rls_appt_select" ON core."appointments" FOR SELECT TO public
    USING (
        (rls_user_role() = ANY (ARRAY['super_admin'::text, 'regional_admin'::text, 'system'::text]))
        OR (clinic_id = rls_clinic_id())
        OR (patient_id = rls_user_id())
        OR (doctor_id = rls_user_id())
        OR (ca_id = rls_user_id())
    );

-- appointments had no DELETE policy whatsoever. Under FORCE RLS that means
-- every DELETE affects 0 rows — so the sweeper's expiry of an unpaid
-- patient-booked selection would silently do nothing. Restricted to 'system'
-- and super_admin: a deleted appointment leaves no audit trail, so this is not
-- a capability clinic staff should have. Staff cancel; only the sweeper deletes.
DROP POLICY IF EXISTS "rls_appt_delete" ON core."appointments";
CREATE POLICY "rls_appt_delete" ON core."appointments" FOR DELETE TO public
    USING (rls_user_role() = ANY (ARRAY['system'::text, 'super_admin'::text]));


-- ---------------------------------------------------------------------------
-- DELIBERATELY NOT DONE HERE
-- ---------------------------------------------------------------------------
-- 1. CHECK on appointments.appointment_type. The staff booking path still
--    writes seven legacy values (scheduling/schemas.py). Adding it now breaks
--    every staff booking. Lands when that path moves to the agreed vocabulary.
--
-- 2. CHECK on appointments.status IN
--    ('planned','selected','paid','cancelled','rescheduled',
--     'checked_in','in_progress','completed','no_show').
--    Blocked on the same code move — 'confirmed' is still reachable today via
--    PATCH /appointments/{id}/status.
--
-- 3. Anything to do with protocol tables. Out of scope by instruction. Note for
--    whoever owns that workstream: the hold sweeper decides delete-vs-revert by
--    testing appointments.plan_id IS NULL. A protocol-born appointment row that
--    reaches 'selected' without plan_id set would be deleted rather than
--    reverted, taking the doctor's prescribed date with it. Setting plan_id on
--    every protocol-born spine row is what prevents that.
--
-- 4. Dropping core.sessions and the five session_id columns. Contract step,
--    unchanged from 30_appointments_spine.sql.
--
-- 5. Refunds, partial payments, packages, price history, a money ledger. The
--    agreed model is one payment per visit. None of it is needed for that, and
--    reference.billable_items already answers "what does this cost".
