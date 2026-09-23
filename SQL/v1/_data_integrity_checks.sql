-- _data_integrity_checks.sql
--
-- Read-only. Every row below is one invariant; `violations` must be 0.
-- Companion to Documents/DATA_CAPTURE_AUDIT_2026-09-21.md (section 5, T-ids).
-- Run: psql -f SQL/v1/_data_integrity_checks.sql   (any role that can read core/compliance/ops)
--
-- Expected on 2026-09-21 BEFORE the fixes: the rows tagged (open) are non-zero.
-- After the fix batches every row must be 0.

SELECT check_id, description, violations FROM (

  -- ---- audit infrastructure -------------------------------------------------
  SELECT 'C01' AS check_id, 'audit trigger not enabled (DC-03)' AS description,
         (SELECT count(*) FROM pg_trigger tg JOIN pg_proc p ON p.oid = tg.tgfoid
           WHERE NOT tg.tgisinternal AND p.proname = 'fn_audit_trigger' AND tg.tgenabled <> 'O') AS violations
  UNION ALL
  SELECT 'C02', 'non-planned appointment with no appointment_audit_logs row (DC-06)',
         (SELECT count(*) FROM core.appointments a
           WHERE a.status <> 'planned'
             AND NOT EXISTS (SELECT 1 FROM core.appointment_audit_logs al WHERE al.appointment_id = a.appointment_id))
  UNION ALL
  SELECT 'C03', 'audit_logs rows in last 7 days with no request_id/ip (DC-04)',
         (SELECT count(*) FROM compliance.audit_logs
           WHERE changed_at > now() - interval '7 days' AND (request_id IS NULL OR ip_address IS NULL))

  -- ---- notifications pipeline ----------------------------------------------
  UNION ALL
  SELECT 'C04', 'outbox event unpublished for more than 10 minutes (DC-01)',
         (SELECT count(*) FROM ops.outbox_events
           WHERE published_at IS NULL AND created_at < now() - interval '10 minutes')

  -- ---- money linkage --------------------------------------------------------
  UNION ALL
  SELECT 'C05', 'paid-type appointment with no paid/waived payment (DC-02)',
         (SELECT count(*) FROM core.appointments a
           WHERE a.status IN ('paid','checked_in','in_progress','completed','no_show')
             AND NOT EXISTS (SELECT 1 FROM core.payments p
                              WHERE p.appointment_id = a.appointment_id AND p.status IN ('paid','waived')))
  UNION ALL
  SELECT 'C06', 'payment still attached to a rescheduled appointment (DC-02)',
         (SELECT count(*) FROM core.payments p JOIN core.appointments a ON a.appointment_id = p.appointment_id
           WHERE a.status = 'rescheduled' AND p.status IN ('paid','pending'))
  UNION ALL
  SELECT 'C07', 'pending payment left on an appointment that already has a paid one (DC-16)',
         (SELECT count(*) FROM core.payments p
           WHERE p.status = 'pending'
             AND EXISTS (SELECT 1 FROM core.payments q WHERE q.appointment_id = p.appointment_id AND q.status = 'paid'))
  UNION ALL
  SELECT 'C08', 'payment amount <> base_fee + platform_fee',
         (SELECT count(*) FROM core.payments
           WHERE base_fee_amount IS NOT NULL
             AND abs(amount - (base_fee_amount + coalesce(platform_fee_amount, 0))) > 0.01)
  UNION ALL
  SELECT 'C09', 'appointment with more than one paid payment',
         (SELECT count(*) FROM (SELECT appointment_id FROM core.payments
                                 WHERE status = 'paid' AND appointment_id IS NOT NULL
                                 GROUP BY 1 HAVING count(*) > 1) x)
  UNION ALL
  SELECT 'C10', 'cancelled appointment with paid payment but no refund fields',
         (SELECT count(*) FROM core.appointments a JOIN core.payments p ON p.appointment_id = a.appointment_id
           WHERE a.status = 'cancelled' AND p.status = 'paid' AND p.cancellation_refund_percent IS NULL)

  -- ---- episode / protocol linkage ------------------------------------------
  UNION ALL
  SELECT 'C11', 'active/completed protocol whose instance is still draft (DC-08)',
         (SELECT count(*) FROM core.protocol_plan pp JOIN core.protocol_instances pi ON pi.instance_id = pp.instance_id
           WHERE pp.status IN ('active','completed') AND pi.status = 'draft')
  UNION ALL
  SELECT 'C12', 'protocol-born appointment patient <> protocol instance patient',
         (SELECT count(*) FROM core.appointments a
            JOIN core.protocol_plan pp ON pp.protocol_id = a.protocol_id
            JOIN core.protocol_instances pi ON pi.instance_id = pp.instance_id
           WHERE a.patient_id <> pi.patient_id)
  UNION ALL
  SELECT 'C13', 'protocol_device_sessions / protocol_followup out of sync with appointment',
         (SELECT (SELECT count(*) FROM core.protocol_device_sessions pds JOIN core.appointments a ON a.appointment_id = pds.appointment_id
                   WHERE pds.protocol_id IS DISTINCT FROM a.protocol_id OR pds.session_number IS DISTINCT FROM a.session_number)
               + (SELECT count(*) FROM core.protocol_followup pf JOIN core.appointments a ON a.appointment_id = pf.appointment_id
                   WHERE pf.protocol_id IS DISTINCT FROM a.protocol_id))
  UNION ALL
  SELECT 'C14', 'device_sessions.protocol_id <> appointment.protocol_id',
         (SELECT count(*) FROM core.device_sessions ds JOIN core.appointments a ON a.appointment_id = ds.appointment_id
           WHERE ds.protocol_id IS DISTINCT FROM a.protocol_id)
  UNION ALL
  SELECT 'C15', 'started/completed device_session appointment with no device_sessions header',
         (SELECT count(*) FROM core.appointments a
           WHERE a.appointment_type = 'device_session' AND a.status IN ('in_progress','completed')
             AND NOT EXISTS (SELECT 1 FROM core.device_sessions ds WHERE ds.appointment_id = a.appointment_id))
  UNION ALL
  SELECT 'C16', 'started device_session with no performed_by_id (DC-15)',
         (SELECT count(*) FROM core.device_sessions
           WHERE session_status IN ('in_progress','paused','completed','stopped_early') AND performed_by_id IS NULL)

  -- ---- PRS / anamnesis / scales --------------------------------------------
  UNION ALL
  SELECT 'C17', 'completed main_clinical PRS with no appointment_id (DC-09)',
         (SELECT count(*) FROM core.prs_assessment_instances
           WHERE assessment_stage = 'main_clinical' AND status = 'completed' AND appointment_id IS NULL)
  UNION ALL
  SELECT 'C18', 'completed main anamnesis with no appointment_id (DC-09)',
         (SELECT count(*) FROM core.anamnesis_assessments
           WHERE assessment_stage = 'main' AND status = 'completed' AND appointment_id IS NULL)
  UNION ALL
  SELECT 'C19', 'completed device-session scale with no device_session_prs_responses link (DC-10)',
         (SELECT count(*) FROM core.device_session_scales s
            JOIN core.device_sessions ds ON ds.device_session_record_id = s.device_session_record_id
           WHERE s.status = 'completed'
             AND NOT EXISTS (SELECT 1 FROM core.device_session_prs_responses r WHERE r.appointment_id = ds.appointment_id))
  UNION ALL
  SELECT 'C20', 'device_session_prs_responses disagrees with its appointment',
         (SELECT count(*) FROM core.device_session_prs_responses r JOIN core.appointments a ON a.appointment_id = r.appointment_id
           WHERE r.patient_id <> a.patient_id OR r.protocol_id IS DISTINCT FROM a.protocol_id
              OR r.session_number IS DISTINCT FROM a.session_number)
  UNION ALL
  SELECT 'C21', 'duplicate active scale assignment rows (DC-12)',
         (SELECT coalesce(sum(n - 1), 0) FROM (SELECT count(*) AS n FROM core.patient_scale_assignments
                                                WHERE is_active GROUP BY patient_id, scale_id, assessment_stage
                                                HAVING count(*) > 1) x)
  UNION ALL
  SELECT 'C22', 'PRS scale result with no scoring_version_id (DC-11)',
         (SELECT count(*) FROM core.prs_scale_results WHERE scoring_version_id IS NULL)
  UNION ALL
  SELECT 'C23', 'completed PRS instance with no prs_final_results',
         (SELECT count(*) FROM core.prs_assessment_instances i
           WHERE i.status = 'completed'
             AND NOT EXISTS (SELECT 1 FROM core.prs_final_results f WHERE f.instance_id = i.instance_id))

  -- ---- consent / identity ---------------------------------------------------
  UNION ALL
  SELECT 'C24', 'signed consent with no ip_address (DC-13)',
         (SELECT count(*) FROM compliance.consent_records WHERE status = 'signed' AND ip_address IS NULL)
  UNION ALL
  SELECT 'C25', 'signed consent with blank signature_data',
         (SELECT count(*) FROM compliance.consent_records
           WHERE status = 'signed' AND (signature_data IS NULL OR signature_data = ''))
  UNION ALL
  SELECT 'C26', 'profile whose role has no matching role-table row',
         (SELECT count(*) FROM core.profiles pr
           WHERE (pr.role = 'doctor' AND NOT EXISTS (SELECT 1 FROM core.doctors d WHERE d.profile_id = pr.id))
              OR (pr.role = 'clinical_assistant' AND NOT EXISTS (SELECT 1 FROM core.clinical_assistants c WHERE c.profile_id = pr.id))
              OR (pr.role = 'receptionist' AND NOT EXISTS (SELECT 1 FROM core.receptionists r WHERE r.profile_id = pr.id))
              OR (pr.role = 'patient' AND NOT EXISTS (SELECT 1 FROM core.patients p WHERE p.profile_id = pr.id))
              OR (pr.role IN ('clinic_admin','regional_admin','super_admin')
                  AND NOT EXISTS (SELECT 1 FROM core.admins a WHERE a.profile_id = pr.id)))
  UNION ALL
  SELECT 'C27', 'registration_complete patient missing signed consent / completed anamnesis / completed PRS',
         (SELECT count(*) FROM core.patients p
           WHERE p.registration_status = 'registration_complete'
             AND NOT (EXISTS (SELECT 1 FROM compliance.consent_records c WHERE c.patient_id = p.profile_id AND c.status = 'signed')
                  AND EXISTS (SELECT 1 FROM core.anamnesis_assessments a WHERE a.patient_id = p.profile_id AND a.status = 'completed')
                  AND EXISTS (SELECT 1 FROM core.prs_assessment_instances i WHERE i.patient_id = p.profile_id AND i.status = 'completed')))

  -- ---- retention ------------------------------------------------------------
  UNION ALL
  SELECT 'C28', 'patient with completed appointment but last_clinical_contact_at NULL (DC-14)',
         (SELECT count(*) FROM core.patients p
           WHERE p.last_clinical_contact_at IS NULL
             AND EXISTS (SELECT 1 FROM core.appointments a WHERE a.patient_id = p.profile_id AND a.status = 'completed'))

) checks
ORDER BY check_id;
