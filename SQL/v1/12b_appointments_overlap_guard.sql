-- Generated from live production schema introspection (2026-07-20). Do not hand-edit column/RLS/trigger/function bodies — regenerate from source instead.

-- btree_gist extension (01_extensions.sql) makes the doctor_id equality term possible in a GIST exclusion.
ALTER TABLE core."appointments" ADD CONSTRAINT "excl_doctor_overlap" EXCLUDE USING gist (doctor_id WITH =, tsrange((appointment_date + start_time), (appointment_date + end_time)) WITH &&) WHERE (status <> ALL (ARRAY['cancelled'::text, 'rescheduled'::text]));
