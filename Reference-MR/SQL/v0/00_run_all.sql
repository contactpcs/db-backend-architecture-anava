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

\echo '>>> 19 Admin Workflow Updates (clinic_admin_id nullable — 2-step clinic setup)'
\i 19_admin_workflow_updates.sql

\echo '>>> 20 Doctor clinic_id (denormalized primary-clinic column)'
\i 20_doctor_clinic_id.sql

\echo '>>> 21 Regional Admin Independence (policy note, no DDL)'
\i 21_regional_admin_independence.sql

\echo '>>> 22 Staff Onboarding Lockdown (policy note, no DDL)'
\i 22_staff_onboarding_lockdown.sql

\echo '>>> 23 Staff Request Fulfillment Link'
\i 23_staff_request_fulfillment.sql

\echo '>>> 24 Patient Self-Registration + Receptionist Approval Gate'
\i 24_patient_self_registration.sql

\echo '>>> 25 Fix PRS Stage-Aware Completion Trigger'
\i 25_fix_prs_stage_completion_trigger.sql

\echo '>>> 26 Fix RLS Same-Clinic Patient Data Leak'
\i 26_fix_rls_patient_clinic_leak.sql

\echo '>>> 27 Fix Regional Admin Consent Scope (clinic_id nullable + region_id)'
\i 27_fix_regional_admin_consent_scope.sql

\echo '>>> 28 Consent Redesign (per-role templates + profiles.consent_signed)'
\i 28_consent_redesign.sql

\echo '>>> 29 Regional Admin Clinic Binding (region -> clinic -> regional_admin -> clinic_admin order)'
\i 29_regional_admin_clinic_binding.sql

\echo '>>> 31 Fix Profile Bootstrap Lookup RLS (auth middleware self-lookup by cognito_sub)'
\i 31_fix_profile_bootstrap_lookup_rls.sql

\echo '>>> 32 Fix Public Clinics Endpoint RLS (self-registration clinic picker)'
\i 32_fix_public_clinics_endpoint_rls.sql

\echo '>>> 33 Fix Local Login Email Lookup RLS (login-by-email self-lookup)'
\i 33_fix_local_login_email_lookup_rls.sql

\echo '>>> 34 Fix Admins RLS Regional Scope (regional_admin -> clinic_admin visibility)'
\i 34_fix_admins_rls_regional_scope.sql

\echo '>>> 35 Remove clinic_admin from staff_requests.position_role'
\i 35_remove_clinic_admin_staff_request_role.sql

\echo '>>> 36 Fix Consent Records Self-Sign RLS (doctor/CA/receptionist/patient sign loop)'
\i 36_fix_consent_records_self_sign_rls.sql

\echo '>>> 37 Fix Regions RLS Anonymous Read (public self-registration clinic lookup)'
\i 37_fix_regions_rls_anonymous_read.sql

\echo '>>> 38 Fix Self-Registration Anonymous Insert RLS (profiles/patients/consent_records)'
\i 38_fix_self_registration_anonymous_insert_rls.sql

\echo '>>> 39 Fix Patient Scale Assignments Self-Select RLS (disease-selection auto-assign)'
\i 39_fix_patient_scale_assignments_self_select_rls.sql

\echo '>>> 40 Fix PRS Scale/Final Results Self-Submit RLS (general PRS submission)'
\i 40_fix_prs_scale_and_final_results_self_submit_rls.sql

\echo '>>> 41 Fix Profiles Patient Sees Own Clinic Staff RLS (doctor auto-allocation)'
\i 41_fix_profiles_patient_sees_own_clinic_staff_rls.sql

\echo '>>> 42 Fix Doctor Patient Assignments Self-Insert RLS (auto-allocation write)'
\i 42_fix_doctor_patient_assignments_self_insert_rls.sql

\echo '>>> 43 Fix Receptionist Patient Approval RLS (is_active activation write)'
\i 43_fix_receptionist_patient_approval_rls.sql

\echo '>>> 44 Patient OTP Channel Verification (email_verified/phone_verified columns)'
\i 44_patient_otp_channel_verification.sql

\echo '>>> 45 PRS Question Translations (Hindi, Marathi — questions only)'
\i 45_questions_translations_insert.sql

\echo '>>> 46 PRS Translations (Hindi, Marathi — questions + option labels)'
\i 46_translations_insert.sql

\echo '>>> 47 PRS Assessment Language (language_code on prs_assessment_instances)'
\i 47_prs_assessment_language.sql

\echo '>>> 48 patient_scale_assignments.disease_id'
\i 48_patient_scale_assignments_disease_id.sql

\echo '>>> Schema build complete. 60 tables created.'
\echo '>>>'
\echo '>>> NOT included above (run manually, in this order, once schema is up):'
\echo '>>>   1. 30_rds_app_role_setup.sql — connected as master user. Creates the'
\echo '>>>      anava_app role the backend actually runs as (no BYPASSRLS). Then'
\echo '>>>      set its password yourself: ALTER ROLE anava_app WITH PASSWORD ...'
\echo '>>>      (never put that statement in a committed file).'
\echo '>>>   2. backend/scripts/seed_dev_profile.py (or bootstrap_superadmin.py in'
\echo '>>>      a real Cognito environment) — the first account. Bootstrapping is'
\echo '>>>      inherently privileged (every INSERT-policy on profiles/admins'
\echo '>>>      requires an already-authenticated super_admin, which doesn'\''t exist'
\echo '>>>      yet) — this and every other scripts/seed_*.py already connect via'
\echo '>>>      get_migration_engine(), not the app'\''s own RLS-scoped connection.'
\echo '>>>   3. backend/scripts/seed_prs_clinical_content.py — real PRS scale/'
\echo '>>>      question content + anamnesis questions from Data/*.csv.'
