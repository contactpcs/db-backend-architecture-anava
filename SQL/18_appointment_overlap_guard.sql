-- ============================================================
-- Anava Clinic — DB Schema
-- File 18: Appointment Overlap Guard
--
-- Prevents double-booking at the database level (Architecture doc Section
-- 25.2 / Development Plan Stage 8) — chk_appt_times only checks
-- start_time < end_time, nothing stops two overlapping appointments for the
-- same doctor. An app-level pre-check helps UX but is not a real guarantee
-- under concurrent booking; this constraint is the actual guarantee.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Postgres has no native "timerange" type — combine appointment_date +
-- start_time/end_time into real timestamps so tsrange/&& works correctly.
ALTER TABLE appointments ADD CONSTRAINT excl_doctor_overlap
EXCLUDE USING gist (
    doctor_id WITH =,
    tsrange((appointment_date + start_time), (appointment_date + end_time)) WITH &&
) WHERE (status NOT IN ('cancelled', 'rescheduled'));
