-- 52_appointments_booked_by_semantics.sql
--
-- Gives core.appointments.booked_by its actual meaning back, and lets a device
-- session record the doctor who prescribed it without consuming that doctor's
-- calendar.
--
-- SCOPE: two columns made nullable, one exclusion constraint narrowed, one
-- CHECK added. No data is deleted; existing rows keep the values they have.
--
-- APPLY ORDER: after 51.
--
-- NOT YET EXECUTED ANYWHERE.
--
--
-- ###########################################################################
-- WHY — booked_by does not mean what the generator was writing
-- ###########################################################################
--
-- booked_by / booked_by_role record WHO CONFIRMED THE BOOKING: the receptionist
-- who took payment at the desk, or the patient who paid in the app. That is an
-- answer to "who put money against this slot", and it only exists once a slot
-- has been chosen and paid for.
--
-- The protocol generator was writing the PRESCRIBING DOCTOR into both columns,
-- because they are NOT NULL and it had to put something there. Every generated
-- session therefore claimed a doctor had taken the payment — on a row that is
-- 'planned', has no time, and has had no payment at all. The audit trail said
-- something untrue about a clinical user.
--
-- After this file:
--
--   status = 'planned'              booked_by / booked_by_role NULL.
--                                   Nobody has booked anything yet — the
--                                   doctor prescribed a DATE, and the patient
--                                   has not chosen an hour.
--   status = 'selected' / 'paid'    both set, to whoever confirmed it.
--   later states                    carried forward unchanged.
--
-- §4's CHECK enforces exactly that, so a future code path cannot quietly go
-- back to stamping a placeholder.
--
--
-- ###########################################################################
-- WHY — doctor_id on a device session, without booking the doctor's day
-- ###########################################################################
--
-- 30 left doctor_id NULL on device sessions, and said why: "a device session is
-- run by a clinical assistant and consumes no doctor's time, so it carries
-- doctor_id = NULL and must never touch a doctor's calendar."
--
-- The intent was right; the mechanism was a blunt instrument. The reason NULL
-- was needed is excl_doctor_overlap — a GIST exclusion that stops one doctor
-- being double-booked:
--
--     EXCLUDE USING gist (doctor_id WITH =, tsrange(...) WITH &&)
--       WHERE status NOT IN ('cancelled','rescheduled') AND start_time IS NOT NULL
--
-- With doctor_id populated, twenty device sessions would become twenty blocking
-- entries in that doctor's diary the moment patients picked slots — refusing
-- real consultations at those times for sessions the doctor is not attending.
--
-- So the exclusion is narrowed to the appointment types that genuinely consume
-- a doctor's time, and doctor_id becomes free to mean "the doctor responsible
-- for this session" on every row. Same protection for consultations, no false
-- calendar pressure from CA-run device work.
--
-- Device-session capacity is unaffected: it was never counted here. It is
-- counted per machine, on clinic_device_id, by idx_appointments_device_capacity
-- (41).


BEGIN;


-- ###########################################################################
-- 1  booked_by / booked_by_role become nullable
-- ###########################################################################
-- NOT NULL is what forced the generator to invent a value. Neither column has
-- a default, so nothing starts filling itself in.

ALTER TABLE core."appointments" ALTER COLUMN "booked_by"      DROP NOT NULL;
ALTER TABLE core."appointments" ALTER COLUMN "booked_by_role" DROP NOT NULL;

COMMENT ON COLUMN core."appointments"."booked_by" IS
    'Who confirmed this booking — the receptionist who took payment, or the patient who paid in the app. NULL while status is ''planned'': the doctor has prescribed a date, nobody has booked a slot. Not the prescriber; that is doctor_id.';
COMMENT ON COLUMN core."appointments"."booked_by_role" IS
    'The role booked_by acted in at confirmation time. NULL while status is ''planned'', set together with booked_by.';


-- ###########################################################################
-- 2  excl_doctor_overlap covers only doctor-scheduled visits
-- ###########################################################################
-- initial, follow_up and protocol_followup consume a doctor's time and must
-- still never overlap. device_session does not: a clinical assistant runs it.

ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "excl_doctor_overlap";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "excl_doctor_overlap"
    EXCLUDE USING gist (
        "doctor_id" WITH =,
        tsrange(("appointment_date" + "start_time"), ("appointment_date" + "end_time")) WITH &&
    )
    WHERE (
        "status" <> ALL (ARRAY['cancelled'::text, 'rescheduled'::text])
        AND "start_time" IS NOT NULL
        -- The one clause this file adds. Everything else is 12b verbatim.
        AND "appointment_type" <> 'device_session'
    );

COMMENT ON COLUMN core."appointments"."doctor_id" IS
    'The doctor responsible for this appointment. On a consultation they attend it and excl_doctor_overlap protects their calendar; on a device_session they prescribed it and a clinical assistant runs it, so it is excluded from that guard and books clinic device capacity instead (clinic_device_id, 41).';


-- ###########################################################################
-- 3  Correct the rows already carrying a prescriber as their booker
-- ###########################################################################
-- MUST run before §4 adds the CHECKs. NOT VALID exempts rows that already
-- exist from the initial scan, but the constraint is still enforced on every
-- statement after it — including these UPDATEs. Adding the CHECK first made
-- §3's own first UPDATE fail against the constraint it exists to satisfy:
--
--     new row for relation "appointments" violates check constraint
--     "chk_appointments_planned_not_booked"
--
-- Every 'planned' row was written by the generator with the prescribing doctor
-- in both columns. Moving that id to doctor_id where it is missing keeps the
-- information — which doctor is responsible — and clears the claim that they
-- booked it.

UPDATE core."appointments"
   SET "doctor_id" = COALESCE("doctor_id", "booked_by")
 WHERE "status" = 'planned'
   AND "booked_by_role" = 'doctor';

UPDATE core."appointments"
   SET "booked_by" = NULL, "booked_by_role" = NULL
 WHERE "status" = 'planned';


-- ###########################################################################
-- 4  The booking pair is all-or-nothing, and only once a slot exists
-- ###########################################################################
-- Two rules in one CHECK:
--   * booked_by and booked_by_role are set together or not at all — half a
--     booking record answers neither question.
--   * a 'planned' row has no booker, because no booking has happened.
-- Deliberately silent about the later states: a row can reach 'cancelled' from
-- either side of that line, and 30's own rows predate the distinction.

ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "chk_appointments_booked_by_pair";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_booked_by_pair"
    CHECK (("booked_by" IS NULL) = ("booked_by_role" IS NULL))
    NOT VALID;

ALTER TABLE core."appointments" DROP CONSTRAINT IF EXISTS "chk_appointments_planned_not_booked";
ALTER TABLE core."appointments"
    ADD CONSTRAINT "chk_appointments_planned_not_booked"
    CHECK ("status" <> 'planned' OR "booked_by" IS NULL)
    NOT VALID;


COMMIT;


-- ###########################################################################
-- 5  Validate
-- ###########################################################################
-- §3 has just made every existing row satisfy both, so these are instant.

ALTER TABLE core."appointments" VALIDATE CONSTRAINT "chk_appointments_booked_by_pair";
ALTER TABLE core."appointments" VALIDATE CONSTRAINT "chk_appointments_planned_not_booked";


-- ###########################################################################
-- VERIFY — run after COMMIT
-- ###########################################################################
--
-- No planned row claims a booker, and device sessions now carry their doctor:
--
-- SELECT appointment_type, status,
--        count(*)                                        AS rows,
--        count(*) FILTER (WHERE doctor_id IS NOT NULL)    AS with_doctor,
--        count(*) FILTER (WHERE booked_by IS NOT NULL)    AS with_booker
--   FROM core.appointments
--  GROUP BY 1, 2 ORDER BY 1, 2;
--
-- Expected: planned rows have with_booker = 0; device_session rows have
-- with_doctor = rows once a protocol is created after this file.
--
-- The exclusion still guards consultations:
--
-- SELECT pg_get_constraintdef(oid) FROM pg_constraint
--  WHERE conrelid = 'core.appointments'::regclass AND conname = 'excl_doctor_overlap';
--                                    -- must contain appointment_type <> 'device_session'
--
--
-- ###########################################################################
-- SHIPS WITH
-- ###########################################################################
--
-- backend/app/modules/treatment_protocols/service.py ::
--     ProtocolService._generate_appointments
--         sets doctor_id on device sessions, leaves booked_by NULL.
--
-- The booking and payment paths in modules/scheduling and modules/payments set
-- booked_by / booked_by_role when a row moves to 'selected' or 'paid'. Those
-- already pass the acting user; what changes is that they are now the only
-- writers of those two columns.
-- ###########################################################################
