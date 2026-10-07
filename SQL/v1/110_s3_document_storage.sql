-- 110_s3_document_storage.sql
--
-- Schema side of the S3 document storage architecture (BACKEND_S3_PROMPT.md,
-- design doc "Document Storage Architecture" revision 2).
--
-- core.patient_medical_history_files gains:
--   s3_version_id  the S3 VersionId of the object s3_key points at. The
--                  buckets are versioned, so key + version id names the exact
--                  bytes a chart entry referred to. NULL for rows written in
--                  local mode and for rows still being scanned.
--   status         scanning    patient upload sitting in the quarantine bucket,
--                              waiting for the malware scan
--                  unverified  scan passed, file copied into the patient
--                              records bucket, no clinician has confirmed it
--                  verified    a clinician confirmed it, or staff uploaded it
--                  rejected    scan failed or never ran; the file was never
--                              copied into the patient records bucket
--   scan_result    the raw scan outcome (GuardDutyMalwareScanStatus tag value,
--                  or NOT_UPLOADED when the patient never sent the bytes).
--   verified_by / verified_at   who confirmed a patient upload, and when.
--
-- s3_key is written when the row is created, also for a 'scanning' row: it is
-- the key the file WILL have in the patient records bucket. Nothing exists at
-- that key until status leaves 'scanning', and the API refuses a download
-- unless status is 'unverified' or 'verified'. The quarantine key is
-- incoming/{mhf_id} and is never stored.
--
-- core.patient_eeg_files gains raw_s3_version_id, same meaning as above.
--
-- Existing rows: staff uploads default to 'verified'. Rows a patient uploaded
-- themselves (uploaded_by = patient_id) were never looked at by a clinician,
-- so they become 'unverified'.
--
-- RLS: the promotion job (app/workers/upload_promoter.py) has no logged-in
-- user and runs as RLS role 'system', like the other workers (25, 31, 93).
-- It has to read and update these rows, so 'system' gets a SELECT and an
-- UPDATE policy. No other policy changes: a patient can already insert a row
-- for themselves (rls_mhf_insert) and clinic staff can already update rows of
-- their clinic (rls_mhf_update), which covers the clinician confirmation.
--
-- APPLY ORDER: after 109, and BEFORE deploying the backend that writes the
-- new columns.

ALTER TABLE core."patient_medical_history_files"
    ADD COLUMN IF NOT EXISTS "s3_version_id" TEXT,
    ADD COLUMN IF NOT EXISTS "status" TEXT NOT NULL DEFAULT 'verified',
    ADD COLUMN IF NOT EXISTS "scan_result" TEXT,
    ADD COLUMN IF NOT EXISTS "verified_by" UUID,
    ADD COLUMN IF NOT EXISTS "verified_at" TIMESTAMPTZ;

ALTER TABLE core."patient_medical_history_files" DROP CONSTRAINT IF EXISTS "chk_mhf_status";
ALTER TABLE core."patient_medical_history_files" ADD CONSTRAINT "chk_mhf_status"
    CHECK ("status" = ANY (ARRAY['scanning'::text, 'unverified'::text, 'verified'::text, 'rejected'::text]));

ALTER TABLE core."patient_medical_history_files" DROP CONSTRAINT IF EXISTS "fk_patient_medical_history_files_verified_by";
ALTER TABLE core."patient_medical_history_files" ADD CONSTRAINT "fk_patient_medical_history_files_verified_by"
    FOREIGN KEY ("verified_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

UPDATE core."patient_medical_history_files" SET "status" = 'unverified'
    WHERE "uploaded_by" = "patient_id" AND "status" = 'verified' AND "verified_by" IS NULL;

ALTER TABLE core."patient_eeg_files"
    ADD COLUMN IF NOT EXISTS "raw_s3_version_id" TEXT;

DROP POLICY IF EXISTS "rls_mhf_select_system" ON core."patient_medical_history_files";
CREATE POLICY "rls_mhf_select_system" ON core."patient_medical_history_files" FOR SELECT TO public
    USING (rls_user_role() = 'system');

DROP POLICY IF EXISTS "rls_mhf_update_system" ON core."patient_medical_history_files";
CREATE POLICY "rls_mhf_update_system" ON core."patient_medical_history_files" FOR UPDATE TO public
    USING (rls_user_role() = 'system')
    WITH CHECK (rls_user_role() = 'system');

COMMENT ON COLUMN core."patient_medical_history_files"."s3_version_id" IS 'S3 VersionId of the object at s3_key. NULL in local storage mode and while status is scanning or rejected.';

COMMENT ON COLUMN core."patient_medical_history_files"."status" IS 'scanning (in quarantine, waiting for the malware scan) / unverified (scan passed, not yet confirmed by a clinician) / verified / rejected (scan failed, never copied to the patient records bucket). Downloads are allowed only for unverified and verified.';

COMMENT ON COLUMN core."patient_medical_history_files"."scan_result" IS 'Raw malware scan outcome: the GuardDutyMalwareScanStatus tag value, or NOT_UPLOADED when the bytes never arrived.';

COMMENT ON COLUMN core."patient_medical_history_files"."verified_by" IS 'Clinician (profiles.id) who confirmed a patient-uploaded file.';

COMMENT ON COLUMN core."patient_eeg_files"."raw_s3_version_id" IS 'S3 VersionId of the object at raw_data_s3_key. NULL in local storage mode.';
