-- _test_readiness.sql
--
-- READ-ONLY. Run before testing the appointment endpoints.
--
-- Answers, in one grid: did 36 land, and is there enough data to actually
-- exercise the flow? Most "the endpoint doesn't work" reports at this stage are
-- really missing prerequisites — no doctor hours means every booking returns
-- APPOINTMENT_SLOT_UNAVAILABLE, which looks like a bug and is not one.
--
-- Anything in the "need" column that does not match "have" tells you what to
-- fix, and section D gives you the ids to paste into Postman.

SELECT * FROM (

-- ------------------------------------------------------- A. did 36 land?
SELECT 'A file 36' AS section, 1 AS ord,
       'core.clinic_device_schedules exists' AS item, 'yes' AS need,
       (SELECT CASE WHEN to_regclass('core.clinic_device_schedules') IS NOT NULL
                    THEN 'yes' ELSE 'NO — apply 36' END) AS have
UNION ALL SELECT 'A file 36', 2, 'core.clinic_device_schedule_overrides exists', 'yes',
       (SELECT CASE WHEN to_regclass('core.clinic_device_schedule_overrides') IS NOT NULL
                    THEN 'yes' ELSE 'NO — apply 36' END)
UNION ALL SELECT 'A file 36', 3, 'CHECK constraints on the two tables', '10',
       (SELECT count(*)::text FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE c.contype = 'c'
          AND t.relname IN ('clinic_device_schedules','clinic_device_schedule_overrides'))
UNION ALL SELECT 'A file 36', 4, 'RLS policies on the two tables', '8',
       (SELECT count(*)::text FROM pg_policy p JOIN pg_class t ON t.oid = p.polrelid
        WHERE t.relname IN ('clinic_device_schedules','clinic_device_schedule_overrides'))
UNION ALL SELECT 'A file 36', 5, 'RLS enabled AND forced on both', 'yes',
       (SELECT CASE WHEN bool_and(relrowsecurity AND relforcerowsecurity) THEN 'yes' ELSE 'NO' END
        FROM pg_class WHERE relname IN ('clinic_device_schedules','clinic_device_schedule_overrides'))
UNION ALL SELECT 'A file 36', 6, 'capacity index on appointments', 'yes',
       (SELECT CASE WHEN count(*) > 0 THEN 'yes' ELSE 'NO' END
        FROM pg_class WHERE relname = 'idx_appointments_device_capacity')
UNION ALL SELECT 'A file 36', 7, 'anava_app can write clinic_device_schedules', 'yes',
       (SELECT CASE WHEN to_regclass('core.clinic_device_schedules') IS NULL THEN 'n/a'
                    WHEN has_table_privilege('anava_app','core.clinic_device_schedules','INSERT')
                    THEN 'yes' ELSE 'NO — grants did not take' END)

-- --------------------------------------------- B. can a patient book at all?
UNION ALL SELECT 'B patient', 10, 'patients ready to book (registration_complete + doctor)', '>= 1',
       (SELECT count(*)::text FROM core.patients
        WHERE registration_status = 'registration_complete'
          AND primary_doctor_id IS NOT NULL
          AND primary_clinic_id IS NOT NULL)
UNION ALL SELECT 'B patient', 11, 'patients stuck without an allocated doctor', '0',
       (SELECT count(*)::text FROM core.patients
        WHERE registration_status = 'registration_complete' AND primary_doctor_id IS NULL)
UNION ALL SELECT 'B patient', 12, 'patients not yet through registration', 'info only',
       (SELECT count(*)::text FROM core.patients WHERE registration_status <> 'registration_complete')

-- --------------------------------------------- C. can a doctor be booked?
UNION ALL SELECT 'C doctor', 20, 'doctors with weekly hours set', '>= 1',
       (SELECT count(DISTINCT doctor_id)::text FROM core.doctor_weekly_schedules WHERE is_active)
UNION ALL SELECT 'C doctor', 21, 'ALLOCATED doctors with NO hours (blocks booking)', '0',
       (SELECT count(*)::text FROM (
          SELECT DISTINCT pt.primary_doctor_id FROM core.patients pt
          WHERE pt.primary_doctor_id IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM core.doctor_weekly_schedules w
                            WHERE w.doctor_id = pt.primary_doctor_id AND w.is_active)
       ) d)
UNION ALL SELECT 'C doctor', 22, 'clinics with a device schedule', '>= 1 to test device slots',
       (SELECT CASE WHEN to_regclass('core.clinic_device_schedules') IS NULL THEN 'n/a'
                    ELSE (SELECT count(DISTINCT clinic_id)::text
                          FROM core.clinic_device_schedules WHERE is_active) END)

-- ------------------------------------------------- D. current appointments
UNION ALL SELECT 'D state', 30, 'appointments total', 'info only',
       (SELECT count(*)::text FROM core.appointments)
UNION ALL SELECT 'D state', 31, 'planned (claimable by a patient)', 'info only',
       (SELECT count(*)::text FROM core.appointments WHERE status = 'planned')
UNION ALL SELECT 'D state', 32, 'selected (held, unpaid)', 'info only',
       (SELECT count(*)::text FROM core.appointments WHERE status = 'selected')
UNION ALL SELECT 'D state', 33, 'paid', 'info only',
       (SELECT count(*)::text FROM core.appointments WHERE status = 'paid')
UNION ALL SELECT 'D state', 34, 'treatment protocols', 'info only',
       (SELECT CASE WHEN to_regclass('core.treatment_protocols') IS NULL THEN 'n/a'
                    ELSE (SELECT count(*)::text FROM core.treatment_protocols) END)
UNION ALL SELECT 'D state', 35, 'legacy status values still present (should be none)', '0',
       (SELECT count(*)::text FROM core.appointments
        WHERE status IN ('scheduled','confirmed'))

) t
ORDER BY section, ord;


-- ---------------------------------------------------------------------------
-- The ids to paste into Postman. Run this second.
-- ---------------------------------------------------------------------------
SELECT
    p.email                        AS patient_login,
    p.id                           AS patient_profile_id,
    pt.primary_clinic_id           AS clinic_id,      -- {{clinicId}}
    c.clinic_name,
    pt.primary_doctor_id           AS doctor_profile_id,
    dp.email                       AS doctor_login,
    (SELECT count(*) FROM core.doctor_weekly_schedules w
      WHERE w.doctor_id = pt.primary_doctor_id AND w.is_active) AS doctor_weekday_rules,
    (SELECT count(*) FROM core.clinic_device_schedules s
      WHERE s.clinic_id = pt.primary_clinic_id AND s.is_active) AS clinic_device_weekday_rules
FROM core.patients pt
JOIN core.profiles p  ON p.id = pt.profile_id
LEFT JOIN core.clinics c  ON c.clinic_id = pt.primary_clinic_id
LEFT JOIN core.profiles dp ON dp.id = pt.primary_doctor_id
WHERE pt.registration_status = 'registration_complete'
  AND pt.primary_doctor_id IS NOT NULL
ORDER BY p.created_at
LIMIT 5;
