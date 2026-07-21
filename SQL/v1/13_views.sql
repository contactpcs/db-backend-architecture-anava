-- Generated from live production schema introspection (2026-07-20). Do not hand-edit column/RLS/trigger/function bodies — regenerate from source instead.

CREATE VIEW core."v_doctor_active_patient_counts" AS
    SELECT doctor_id, count(*) AS active_patient_count FROM core.doctor_patient_assignments WHERE status = 'active'::text GROUP BY doctor_id;
