-- ============================================================
-- Anava Clinic — DB Schema
-- File 00: Master run script
-- Run this on a fresh AWS RDS PostgreSQL 14+ instance to build
-- the entire Anava schema from scratch.
--
-- Usage (psql):
--   psql -h <rds-endpoint> -U <master_user> -d <dbname> -f 00_run_all.sql
--
-- Run order is critical — foreign key dependencies enforced.
-- ============================================================

\echo '>>> 01 Extensions'
\i 01_extensions.sql

\echo '>>> 02 Core Tables (profiles, regions, clinics, admins, staff_assignments)'
\i 02_core_tables.sql

\echo '>>> 03 Staff Role Tables (doctors, clinical_assistants, receptionists)'
\i 03_staff_role_tables.sql

\echo '>>> 04 Patient Tables (patients, patient_disease_selection)'
\i 04_patient_tables.sql

\echo '>>> 05 Request Tables (clinic_requests, staff_requests)'
\i 05_request_tables.sql

\echo '>>> 06 Clinical Tables (treatment_cycles, sessions, treatment_plans, treatment_sessions)'
\i 06_clinical_tables.sql

\echo '>>> 06b Appointment Scheduling Tables (schedules, overrides, requests, appointments, audit_logs)'
\i 06b_appointment_tables.sql

\echo '>>> 07 PRS Tables (scales, questions, instances, responses)'
\i 07_prs_tables.sql

\echo '>>> 08 Anamnesis Tables'
\i 08_anamnesis_tables.sql

\echo '>>> 08b Patient File Tables (EEG + Medical History)'
\i 08b_patient_files.sql

\echo '>>> 09 Consent Tables (templates, records, patient_clinic_transfers)'
\i 09_consent_tables.sql

\echo '>>> 10 Store Tables (products, orders, inventory, stock_transfers, device_assignments)'
\i 10_store_tables.sql

\echo '>>> 11 Payment Tables (Razorpay)'
\i 11_payment_tables.sql

\echo '>>> 12 Logging Tables (audit_logs, activity_logs)'
\i 12_logging_tables.sql

\echo '>>> 12b Notifications Table'
\i 12b_notifications.sql

\echo '>>> 13 Indexes'
\i 13_indexes.sql

\echo '>>> 14 Triggers (updated_at + audit_logs)'
\i 14_triggers.sql

\echo '>>> 15 RLS Policies'
\i 15_rls_policies.sql

\echo '>>> 16 Seed Data (consent_templates, prs_diseases)'
\i 16_seed_data.sql

\echo '>>> 17 Outbox Events (transactional outbox for domain events)'
\i 17_outbox_events.sql

\echo '>>> 18 Appointment Overlap Guard (double-booking prevention)'
\i 18_appointment_overlap_guard.sql

\echo '>>> Schema build complete. 57 tables created.'
