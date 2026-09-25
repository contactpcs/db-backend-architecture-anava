-- 90_parallel_protocol_instances.sql
--
-- Lets one patient hold more than one open protocol instance at a time (e.g.
-- a tDCS course and a taVNS course running side by side), with their device
-- sessions booked one after another on the same day.
--
-- SCOPE: one index dropped, one exclusion constraint added. The single-
-- instance flow is unchanged — a patient with one open instance behaves
-- exactly as before.
--
-- APPLY ORDER: after 89.
--
--
-- ###########################################################################
-- WHY
-- ###########################################################################
--
-- uq_protocol_instances_one_active (45, repointed to patient_id by 58) allowed
-- at most one draft/active instance per patient. That rule is what blocked a
-- second protocol; it goes.
--
-- Nothing ever stopped one patient's device sessions from overlapping each
-- other: excl_doctor_overlap skips device_session rows (52) and
-- excl_ca_overlap keys on whoever runs the session, not the patient. With
-- one instance the generator's one-session-per-date rule mostly hid that;
-- with two instances it is the normal case, so the patient needs their own
-- guard. Scoped to device_session rows only — consultation booking is not
-- touched by this file.


-- ###########################################################################
-- 1  Pre-check: refuse to add the guard over rows that already break it
-- ###########################################################################
-- An EXCLUDE constraint cannot be added NOT VALID, so an existing overlap
-- would fail the ALTER below with a bare "could not create exclusion
-- constraint". Fail here instead, with the query that finds them.

DO $$
DECLARE
    v_overlaps INTEGER;
BEGIN
    SELECT count(*) INTO v_overlaps
    FROM core."appointments" a
    JOIN core."appointments" b
      ON  b."patient_id" = a."patient_id"
      AND b."appointment_id" > a."appointment_id"
      AND tsrange(b."appointment_date" + b."start_time", b."appointment_date" + b."end_time")
       && tsrange(a."appointment_date" + a."start_time", a."appointment_date" + a."end_time")
    WHERE a."appointment_type" = 'device_session'
      AND b."appointment_type" = 'device_session'
      AND a."start_time" IS NOT NULL AND b."start_time" IS NOT NULL
      AND a."status" <> ALL (ARRAY['cancelled', 'rescheduled'])
      AND b."status" <> ALL (ARRAY['cancelled', 'rescheduled']);

    IF v_overlaps > 0 THEN
        RAISE EXCEPTION
            '90: % pair(s) of device sessions for the same patient already overlap. '
            'Reschedule or cancel them first — find them with the query in this DO block.',
            v_overlaps;
    END IF;
END $$;


-- ###########################################################################
-- 2  Allow several open instances per patient
-- ###########################################################################

DROP INDEX IF EXISTS core.uq_protocol_instances_one_active;


-- ###########################################################################
-- 3  One patient, one device session at a time
-- ###########################################################################
-- Same shape and status filter as excl_ca_overlap (31): 'planned' and
-- 'missed' rows carry no time and drop out on the NULL; cancelled and
-- rescheduled rows no longer hold their slot. Back-to-back sessions
-- (15:00-15:30 then 15:30-16:00) do not conflict: tsrange is [start, end).

ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "excl_patient_device_session_overlap";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "excl_patient_device_session_overlap"
    EXCLUDE USING gist (
        "patient_id" WITH =,
        tsrange(("appointment_date" + "start_time"), ("appointment_date" + "end_time")) WITH &&
    )
    WHERE ("status" <> ALL (ARRAY['cancelled'::text, 'rescheduled'::text])
           AND "start_time" IS NOT NULL
           AND "appointment_type" = 'device_session');


-- ###########################################################################
-- VERIFY
-- ###########################################################################
--  SELECT indexname FROM pg_indexes
--  WHERE schemaname = 'core' AND indexname = 'uq_protocol_instances_one_active';  -- 0 rows
--
--  SELECT conname FROM pg_constraint
--  WHERE conrelid = 'core.appointments'::regclass
--    AND conname = 'excl_patient_device_session_overlap';                         -- 1 row
--
-- ROLLBACK (only while every patient still has at most one open instance —
-- the index recreate fails otherwise):
--  ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "excl_patient_device_session_overlap";
--  CREATE UNIQUE INDEX uq_protocol_instances_one_active
--      ON core."protocol_instances" ("patient_id") WHERE "status" IN ('draft', 'active');
