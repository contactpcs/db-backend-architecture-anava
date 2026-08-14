-- _postapply_verify.sql
--
-- READ-ONLY. Run after 30-36 have been applied. Returns one result grid.
--
-- The preflight only confirms that a handful of marker objects exist. This
-- confirms the whole chain landed: table counts, every constraint on the spine,
-- all the triggers and functions, RLS coverage, policy counts, grants, and that
-- the protocol generator writes the agreed vocabulary.
--
-- Anything where "actual" does not match "expected" is a partial apply worth
-- chasing before code runs against it.

SELECT * FROM (

-- ------------------------------------------------------------ A. inventory
SELECT 'A inventory' AS section, 1 AS ord,
       'core tables' AS item, '46' AS expected,
       (SELECT count(*)::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='core' AND c.relkind IN ('r','p')
          AND NOT EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid=c.oid)) AS actual
UNION ALL SELECT 'A inventory', 2, 'reference tables', '32',
       (SELECT count(*)::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='reference' AND c.relkind IN ('r','p')
          AND NOT EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid=c.oid))
UNION ALL SELECT 'A inventory', 3, 'compliance tables', '9',
       (SELECT count(*)::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='compliance' AND c.relkind IN ('r','p')
          AND NOT EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid=c.oid))
UNION ALL SELECT 'A inventory', 4, 'ops tables', '3',
       (SELECT count(*)::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='ops' AND c.relkind IN ('r','p')
          AND NOT EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid=c.oid))
UNION ALL SELECT 'A inventory', 5, 'protocol module tables (32)', '21',
       (SELECT count(*)::text FROM pg_class c
        WHERE c.relname IN ('device_companies','neuromod_devices','neuromod_conditions',
              'neuromod_diagnoses','neuromod_scales','neuromod_condition_scales',
              'tdcs_placements','hd_tdcs_placements','tavns_placements','tps_placements',
              'rtms_placements','other_placements','tdcs_dosing','hd_tdcs_dosing',
              'tavns_dosing','tps_dosing','rtms_dosing','other_dosing',
              'treatment_protocols','device_session_prs_responses','followup_prs_responses'))
UNION ALL SELECT 'A inventory', 6, 'core.appointments columns', '30',
       (SELECT count(*)::text FROM information_schema.columns
        WHERE table_schema='core' AND table_name='appointments')
UNION ALL SELECT 'A inventory', 7, 'clinic device schedule tables (36)', '2',
       (SELECT count(*)::text FROM pg_class c
        WHERE c.relname IN ('clinic_device_schedules','clinic_device_schedule_overrides'))

-- --------------------------------------------- B. constraints on the spine
UNION ALL SELECT 'B spine guards', 10, 'excl_doctor_overlap', 'present',
       (SELECT CASE WHEN count(*)>0 THEN 'present' ELSE 'MISSING' END
        FROM pg_constraint WHERE conname='excl_doctor_overlap')
UNION ALL SELECT 'B spine guards', 11, 'excl_ca_overlap', 'present',
       (SELECT CASE WHEN count(*)>0 THEN 'present' ELSE 'MISSING' END
        FROM pg_constraint WHERE conname='excl_ca_overlap')
UNION ALL SELECT 'B spine guards', 12, 'excl guards skip only cancelled/rescheduled', 'yes',
       (SELECT CASE WHEN bool_and(pg_get_constraintdef(oid) NOT LIKE '%planned%'
                                  AND pg_get_constraintdef(oid) LIKE '%start_time IS NOT NULL%')
                    THEN 'yes' ELSE 'NO - old 30 predicate still in place' END
        FROM pg_constraint WHERE conname IN ('excl_doctor_overlap','excl_ca_overlap'))
UNION ALL SELECT 'B spine guards', 13, 'chk_appointments_hold', 'present',
       (SELECT CASE WHEN count(*)>0 THEN 'present' ELSE 'MISSING' END
        FROM pg_constraint WHERE conname='chk_appointments_hold')
UNION ALL SELECT 'B spine guards', 14, 'chk_appointments_cancel_reason', 'present',
       (SELECT CASE WHEN count(*)>0 THEN 'present' ELSE 'MISSING' END
        FROM pg_constraint WHERE conname='chk_appointments_cancel_reason')
UNION ALL SELECT 'B spine guards', 15, 'chk_appointments_time_pair', 'present',
       (SELECT CASE WHEN count(*)>0 THEN 'present' ELSE 'MISSING' END
        FROM pg_constraint WHERE conname='chk_appointments_time_pair')
UNION ALL SELECT 'B spine guards', 16, 'chk_appointments_slotted_has_time', 'present',
       (SELECT CASE WHEN count(*)>0 THEN 'present' ELSE 'MISSING' END
        FROM pg_constraint WHERE conname='chk_appointments_slotted_has_time')
UNION ALL SELECT 'B spine guards', 17, 'chk_appointments_protocol_pair', 'present',
       (SELECT CASE WHEN count(*)>0 THEN 'present' ELSE 'MISSING' END
        FROM pg_constraint WHERE conname='chk_appointments_protocol_pair')
UNION ALL SELECT 'B spine guards', 18, 'chk_appointments_protocol_has_plan', 'present',
       (SELECT CASE WHEN count(*)>0 THEN 'present' ELSE 'MISSING' END
        FROM pg_constraint WHERE conname='chk_appointments_protocol_has_plan')
UNION ALL SELECT 'B spine guards', 19, 'NOT VALID constraints left unvalidated', '0',
       (SELECT count(*)::text FROM pg_constraint WHERE NOT convalidated)
UNION ALL SELECT 'B spine guards', 20, 'uq_one_active_initial_per_patient', 'present',
       (SELECT CASE WHEN count(*)>0 THEN 'present' ELSE 'MISSING' END
        FROM pg_class WHERE relname='uq_one_active_initial_per_patient')
UNION ALL SELECT 'B spine guards', 21, 'uq_appointments_protocol_session', 'present',
       (SELECT CASE WHEN count(*)>0 THEN 'present' ELSE 'MISSING' END
        FROM pg_class WHERE relname='uq_appointments_protocol_session')
UNION ALL SELECT 'B spine guards', 22, 'uq_payments_one_captured_per_appointment', 'present',
       (SELECT CASE WHEN count(*)>0 THEN 'present' ELSE 'MISSING' END
        FROM pg_class WHERE relname='uq_payments_one_captured_per_appointment')

-- ------------------------------------------------ C. functions and triggers
UNION ALL SELECT 'C functions', 30, 'fn_check_device_modality (bug fix)', 'present',
       (SELECT CASE WHEN count(*)>0 THEN 'present' ELSE 'MISSING' END
        FROM pg_proc WHERE proname='fn_check_device_modality')
UNION ALL SELECT 'C functions', 31, 'fn_check_protocol_device_consistency', 'present',
       (SELECT CASE WHEN count(*)>0 THEN 'present' ELSE 'MISSING' END
        FROM pg_proc WHERE proname='fn_check_protocol_device_consistency')
UNION ALL SELECT 'C functions', 32, 'fn_check_prs_appointment_type', 'present',
       (SELECT CASE WHEN count(*)>0 THEN 'present' ELSE 'MISSING' END
        FROM pg_proc WHERE proname='fn_check_prs_appointment_type')
UNION ALL SELECT 'C functions', 33, 'device-modality triggers', '12',
       (SELECT count(*)::text FROM pg_trigger WHERE tgname LIKE 'trg_check_modality_%')
UNION ALL SELECT 'C functions', 34, 'PRS visit-type triggers', '2',
       (SELECT count(*)::text FROM pg_trigger
        WHERE tgname IN ('trg_check_device_session_prs_appointment_type',
                         'trg_check_followup_prs_appointment_type'))
UNION ALL SELECT 'C functions', 35, 'hd_tdcs electrode CHECK uses COALESCE (bug fix)', 'yes',
       (SELECT CASE WHEN bool_or(pg_get_constraintdef(oid) LIKE '%COALESCE%') THEN 'yes'
                    ELSE 'NO - NULL hole still open' END
        FROM pg_constraint WHERE conname='chk_hd_tdcs_placements_electrode_rule')
UNION ALL SELECT 'C functions', 36, 'PRS follow-up trigger guards protocol_followup', 'yes',
       (SELECT CASE WHEN bool_or(pg_get_triggerdef(oid) LIKE '%protocol_followup%') THEN 'yes'
                    ELSE 'NO - still guards follow_up' END
        FROM pg_trigger WHERE tgname='trg_check_followup_prs_appointment_type')

-- ---------------------------------------------------------------- D. security
UNION ALL SELECT 'D security', 50, 'tables with RLS enabled but NO policy', '0',
       (SELECT count(*)::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relrowsecurity AND c.relkind='r'
          AND n.nspname IN ('core','reference','compliance','ops')
          AND NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid))
UNION ALL SELECT 'D security', 51, 'protocol catalogue policies (18 tables x 3)', '54',
       (SELECT count(*)::text FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
        WHERE c.relname IN ('device_companies','neuromod_devices','neuromod_conditions',
              'neuromod_diagnoses','neuromod_scales','neuromod_condition_scales',
              'tdcs_placements','hd_tdcs_placements','tavns_placements','tps_placements',
              'rtms_placements','other_placements','tdcs_dosing','hd_tdcs_dosing',
              'tavns_dosing','tps_dosing','rtms_dosing','other_dosing'))
UNION ALL SELECT 'D security', 52, 'appointments policies (incl. DELETE)', '4',
       (SELECT count(*)::text FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
        WHERE c.relname='appointments')
UNION ALL SELECT 'D security', 53, 'appointments has a DELETE policy', 'yes',
       (SELECT CASE WHEN count(*)>0 THEN 'yes' ELSE 'NO - sweeper deletes affect 0 rows' END
        FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
        WHERE c.relname='appointments' AND p.polcmd='d')
UNION ALL SELECT 'D security', 54, 'clinic_device_schedules policies', '3',
       (SELECT count(*)::text FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
        WHERE c.relname='clinic_device_schedules')
UNION ALL SELECT 'D security', 55, 'anava_app can SELECT clinic_device_schedules', 'yes',
       (SELECT CASE WHEN to_regclass('core.clinic_device_schedules') IS NULL THEN 'n/a'
                    WHEN has_table_privilege('anava_app','core.clinic_device_schedules','SELECT')
                    THEN 'yes' ELSE 'NO' END)
UNION ALL SELECT 'D security', 56, 'anava_app CANNOT delete treatment_protocols', 'yes',
       (SELECT CASE WHEN has_table_privilege('anava_app','core.treatment_protocols','DELETE')
                    THEN 'NO - REVOKE did not take' ELSE 'yes' END)
UNION ALL SELECT 'D security', 57, 'anava_compliance can read protocol tables', 'yes',
       (SELECT CASE WHEN has_table_privilege('anava_compliance','core.treatment_protocols','SELECT')
                    THEN 'yes' ELSE 'NO' END)
UNION ALL SELECT 'D security', 58, 'anava_app bypasses RLS?', 'no',
       (SELECT CASE WHEN rolbypassrls OR rolsuper THEN 'YES - RLS not enforced for the app'
                    ELSE 'no' END FROM pg_roles WHERE rolname='anava_app')

-- ------------------------------------------------------------- E. pricing
UNION ALL SELECT 'E pricing', 60, 'billable_items.device_id is an FK', 'present',
       (SELECT CASE WHEN count(*)>0 THEN 'present' ELSE 'MISSING' END
        FROM pg_constraint WHERE conname='fk_billable_items_device_id')
UNION ALL SELECT 'E pricing', 61, 'billable_items.device_type removed', 'yes',
       (SELECT CASE WHEN count(*)=0 THEN 'yes' ELSE 'NO - still present' END
        FROM information_schema.columns
        WHERE table_schema='reference' AND table_name='billable_items' AND column_name='device_type')

-- ------------------------------------------------------------- F. legacy
UNION ALL SELECT 'F legacy', 70, 'appointment_requests dropped', 'yes',
       (SELECT CASE WHEN to_regclass('core.appointment_requests') IS NULL THEN 'yes' ELSE 'NO' END)
UNION ALL SELECT 'F legacy', 71, 'orphaned FKs pointing at dropped objects', '0',
       (SELECT count(*)::text FROM pg_constraint WHERE contype='f' AND confrelid=0)
UNION ALL SELECT 'F legacy', 72, 'core.sessions still present (expected - contract step)', 'yes',
       (SELECT CASE WHEN to_regclass('core.sessions') IS NOT NULL THEN 'yes' ELSE 'no' END)
UNION ALL SELECT 'F legacy', 73, 'fn_generate_protocol_sessions (superseded by Python)', 'present',
       (SELECT CASE WHEN count(*)>0 THEN 'present' ELSE 'dropped' END
        FROM pg_proc WHERE proname='fn_generate_protocol_sessions')

) t
ORDER BY section, ord;
