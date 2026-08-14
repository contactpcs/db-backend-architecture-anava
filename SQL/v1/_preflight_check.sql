-- _preflight_check.sql
--
-- READ-ONLY. Creates nothing permanent, changes nothing, drops nothing.
-- The helper functions live in pg_temp and die with your session.
--
-- Run this whole file (DBeaver: Alt+X "Execute script") against a target
-- database BEFORE applying 30-36. It returns a single result grid — select all,
-- copy, paste it back.
--
-- WHY IT MATTERS FOR PRODUCTION: files 30-34 were written for a database where
-- every affected table holds 0 rows. That was true in the sandbox. It is very
-- unlikely to be true in production. Section D counts exactly what file 34 would
-- destroy; section E tells you which new constraints would fail against data
-- that already exists. 30, 31 and 34 are not wrapped in transactions, so a
-- mid-file failure leaves the chain half-applied.
--
--   A  which database and user am I actually connected as
--   B  do the 0-row assumptions still hold
--   C  which of files 30-36 are already applied
--   D  what would file 34 destroy
--   E  would any NEW constraint fail against existing data
--   F  is btree_gist present


CREATE OR REPLACE FUNCTION pg_temp.cnt(p_schema text, p_table text)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE n bigint;
BEGIN
    IF to_regclass(format('%I.%I', p_schema, p_table)) IS NULL THEN RETURN NULL; END IF;
    EXECUTE format('SELECT count(*) FROM %I.%I', p_schema, p_table) INTO n;
    RETURN n;
END $$;

CREATE OR REPLACE FUNCTION pg_temp.cntw(p_schema text, p_table text, p_where text)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE n bigint;
BEGIN
    IF to_regclass(format('%I.%I', p_schema, p_table)) IS NULL THEN RETURN NULL; END IF;
    EXECUTE format('SELECT count(*) FROM %I.%I WHERE %s', p_schema, p_table, p_where) INTO n;
    RETURN n;
EXCEPTION WHEN undefined_column THEN
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION pg_temp.has_col(p_schema text, p_table text, p_col text)
RETURNS text LANGUAGE sql AS $$
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = p_schema AND table_name = p_table AND column_name = p_col
    ) THEN 'yes' ELSE 'no' END;
$$;

CREATE OR REPLACE FUNCTION pg_temp.has_rel(p_qualified text)
RETURNS text LANGUAGE sql AS $$
    SELECT CASE WHEN to_regclass(p_qualified) IS NOT NULL THEN 'yes' ELSE 'no' END;
$$;

CREATE OR REPLACE FUNCTION pg_temp.has_con(p_name text)
RETURNS text LANGUAGE sql AS $$
    SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_constraint WHERE conname = p_name)
           THEN 'yes' ELSE 'no' END;
$$;


SELECT * FROM (

-- --------------------------------------------------------------- A. identity
SELECT 'A identity' AS section, 1 AS ord, 'database' AS item, current_database()::text AS value
UNION ALL SELECT 'A identity', 2, 'connected as',   current_user::text
UNION ALL SELECT 'A identity', 3, 'is superuser',   (SELECT COALESCE(usesuper::text,'?') FROM pg_user WHERE usename = current_user)
UNION ALL SELECT 'A identity', 4, 'server version', split_part(version(), ' ', 2)
UNION ALL SELECT 'A identity', 5, 'search_path',    current_setting('search_path')

-- ------------------------------------------------- B. the 0-row assumptions
UNION ALL SELECT 'B rowcounts', 10, 'core.appointments',             pg_temp.cnt('core','appointments')::text
UNION ALL SELECT 'B rowcounts', 11, 'core.appointment_requests',     pg_temp.cnt('core','appointment_requests')::text
UNION ALL SELECT 'B rowcounts', 12, 'core.appointment_audit_logs',   pg_temp.cnt('core','appointment_audit_logs')::text
UNION ALL SELECT 'B rowcounts', 13, 'core.sessions',                 pg_temp.cnt('core','sessions')::text
UNION ALL SELECT 'B rowcounts', 14, 'core.treatment_sessions',       pg_temp.cnt('core','treatment_sessions')::text
UNION ALL SELECT 'B rowcounts', 15, 'core.treatment_plans',          pg_temp.cnt('core','treatment_plans')::text
UNION ALL SELECT 'B rowcounts', 16, 'core.treatment_cycles',         pg_temp.cnt('core','treatment_cycles')::text
UNION ALL SELECT 'B rowcounts', 17, 'core.payments',                 pg_temp.cnt('core','payments')::text
UNION ALL SELECT 'B rowcounts', 18, 'core.treatment_protocols',      pg_temp.cnt('core','treatment_protocols')::text
UNION ALL SELECT 'B rowcounts', 19, 'core.patient_eeg_files',        pg_temp.cnt('core','patient_eeg_files')::text
UNION ALL SELECT 'B rowcounts', 20, 'core.prs_assessment_instances', pg_temp.cnt('core','prs_assessment_instances')::text
UNION ALL SELECT 'B rowcounts', 21, 'reference.billable_items',      pg_temp.cnt('reference','billable_items')::text
UNION ALL SELECT 'B rowcounts', 22, 'core.patients   (context)',     pg_temp.cnt('core','patients')::text
UNION ALL SELECT 'B rowcounts', 23, 'core.profiles   (context)',     pg_temp.cnt('core','profiles')::text

-- --------------------------------------------------- C. which files applied
UNION ALL SELECT 'C applied', 30, '30  appointments.plan_id',              pg_temp.has_col('core','appointments','plan_id')
UNION ALL SELECT 'C applied', 31, '30  payments.appointment_id',           pg_temp.has_col('core','payments','appointment_id')
UNION ALL SELECT 'C applied', 32, '30  reference.billable_items exists',   pg_temp.has_rel('reference.billable_items')
UNION ALL SELECT 'C applied', 33, '30  chk_payments_single_target',        pg_temp.has_con('chk_payments_single_target')
UNION ALL SELECT 'C applied', 40, '31  appointments.hold_expires_at',      pg_temp.has_col('core','appointments','hold_expires_at')
UNION ALL SELECT 'C applied', 41, '31  chk_appointments_hold',             pg_temp.has_con('chk_appointments_hold')
UNION ALL SELECT 'C applied', 42, '31  chk_appointments_cancel_reason',    pg_temp.has_con('chk_appointments_cancel_reason')
UNION ALL SELECT 'C applied', 43, '31  excl_ca_overlap',                   pg_temp.has_con('excl_ca_overlap')
UNION ALL SELECT 'C applied', 50, '32  core.treatment_protocols exists',   pg_temp.has_rel('core.treatment_protocols')
UNION ALL SELECT 'C applied', 51, '32  reference.neuromod_devices exists', pg_temp.has_rel('reference.neuromod_devices')
UNION ALL SELECT 'C applied', 52, '32  appointments.protocol_id',          pg_temp.has_col('core','appointments','protocol_id')
UNION ALL SELECT 'C applied', 60, '33  billable_items.device_id',          pg_temp.has_col('reference','billable_items','device_id')
UNION ALL SELECT 'C applied', 61, '33  billable_items.device_type (old)',  pg_temp.has_col('reference','billable_items','device_type')
UNION ALL SELECT 'C applied', 70, '34  appointment_requests still exists', pg_temp.has_rel('core.appointment_requests')
UNION ALL SELECT 'C applied', 71, '34  appointments.session_id still',     pg_temp.has_col('core','appointments','session_id')
UNION ALL SELECT 'C applied', 72, '34  appointments.session_phase still',  pg_temp.has_col('core','appointments','session_phase')
UNION ALL SELECT 'C applied', 73, '34  appointments.appt_request_id still',pg_temp.has_col('core','appointments','appointment_request_id')
UNION ALL SELECT 'C applied', 80, '35  ops.alembic_version RLS off',
       (SELECT CASE WHEN relrowsecurity THEN 'no - still on' ELSE 'yes' END
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='ops' AND c.relname='alembic_version')
UNION ALL SELECT 'C applied', 90, '36  core.clinic_device_schedules exists', pg_temp.has_rel('core.clinic_device_schedules')

-- ------------------------------------------------ D. what file 34 destroys
UNION ALL SELECT 'D 34 destroys', 100, 'appointment_requests rows',
       pg_temp.cnt('core','appointment_requests')::text
UNION ALL SELECT 'D 34 destroys', 101, 'appointments.session_id non-null',
       pg_temp.cntw('core','appointments','session_id IS NOT NULL')::text
UNION ALL SELECT 'D 34 destroys', 102, 'appointments.appointment_request_id non-null',
       pg_temp.cntw('core','appointments','appointment_request_id IS NOT NULL')::text
UNION ALL SELECT 'D 34 destroys', 103, 'appointments.session_phase non-null',
       pg_temp.cntw('core','appointments','session_phase IS NOT NULL')::text
UNION ALL SELECT 'D 34 destroys', 104, 'billable_items.device_type non-null (33 drops)',
       pg_temp.cntw('reference','billable_items','device_type IS NOT NULL')::text

-- ------------------------------------ E. would a NEW constraint fail today?
-- Every row here must come back 0.
UNION ALL SELECT 'E would fail', 110,
       'cancelled appointments with NO reason  -> blocks chk_appointments_cancel_reason',
       pg_temp.cntw('core','appointments',
           $q$status = 'cancelled' AND cancellation_reason IS NULL$q$)::text

UNION ALL SELECT 'E would fail', 111,
       'rows already in status selected        -> blocks chk_appointments_hold',
       pg_temp.cntw('core','appointments', $q$status = 'selected'$q$)::text

UNION ALL SELECT 'E would fail', 112,
       'duplicate live initial per patient     -> blocks uq_one_active_initial',
       COALESCE((
         SELECT count(*)::text FROM (
           SELECT patient_id FROM core.appointments
           WHERE appointment_type = 'initial'
             AND status IN ('selected','paid','checked_in','in_progress')
           GROUP BY patient_id HAVING count(*) > 1
         ) d
       ), '0')

UNION ALL SELECT 'E would fail', 113,
       'overlapping DOCTOR appointments        -> blocks excl_doctor_overlap',
       COALESCE((
         SELECT count(*)::text FROM core.appointments a
         JOIN core.appointments b
           ON b.doctor_id = a.doctor_id
          AND b.appointment_id <> a.appointment_id
          AND b.appointment_date = a.appointment_date
          AND b.start_time IS NOT NULL AND a.start_time IS NOT NULL
          AND tsrange(b.appointment_date + b.start_time, b.appointment_date + b.end_time)
              && tsrange(a.appointment_date + a.start_time, a.appointment_date + a.end_time)
         WHERE a.status NOT IN ('cancelled','rescheduled')
           AND b.status NOT IN ('cancelled','rescheduled')
       ), '0')

UNION ALL SELECT 'E would fail', 114,
       'overlapping CA appointments            -> blocks excl_ca_overlap',
       COALESCE((
         SELECT count(*)::text FROM core.appointments a
         JOIN core.appointments b
           ON b.ca_id = a.ca_id
          AND b.appointment_id <> a.appointment_id
          AND b.appointment_date = a.appointment_date
          AND b.start_time IS NOT NULL AND a.start_time IS NOT NULL
          AND tsrange(b.appointment_date + b.start_time, b.appointment_date + b.end_time)
              && tsrange(a.appointment_date + a.start_time, a.appointment_date + a.end_time)
         WHERE a.ca_id IS NOT NULL
           AND a.status NOT IN ('cancelled','rescheduled')
           AND b.status NOT IN ('cancelled','rescheduled')
       ), '0')

UNION ALL SELECT 'E would fail', 115,
       'payments targeting 0 or 2+ things      -> blocks chk_payments_single_target',
       pg_temp.cntw('core','payments',
           $q$num_nonnulls(session_id, order_id) <> 1$q$)::text

UNION ALL SELECT 'E would fail', 116,
       'timed rows with only one of start/end  -> blocks chk_appointments_time_pair',
       pg_temp.cntw('core','appointments',
           $q$(start_time IS NULL) <> (end_time IS NULL)$q$)::text

-- ------------------------------------------------------- F. extension check
UNION ALL SELECT 'F extensions', 120, 'btree_gist installed (both overlap guards need it)',
       CASE WHEN EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'btree_gist')
            THEN 'yes' ELSE 'NO — excl guards will fail' END

) t
ORDER BY section, ord;
