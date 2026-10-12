# Backend task: move Anava document storage to the new S3 architecture

Paste this whole file into Claude Code (or use it as a ticket) in the `backend/` repo.

## Role and goal

You are working on the Anava backend (FastAPI, ECS Fargate). Implement the application-side changes for the new document storage architecture. The AWS infrastructure is already built in the new account. Your job is the code, the task-definition/IAM changes, and the promotion job. Read `backend/app/integrations/s3.py` and `backend/app/config.py` first and match their style.

## What is already done (AWS account 363518210533, region ap-south-1)

| Purpose | Name / ARN |
|---|---|
| Patient records bucket | `arn:aws:s3:::anava-clinic-patient-files-prod-363518210533-ap-south-1` |
| Quarantine bucket | `arn:aws:s3:::anava-clinic-uploads-quarantine-363518210533-ap-south-1` |
| Compliance bucket (consent, licenses) | `arn:aws:s3:::anava-clinic-compliance-records-363518210533-ap-south-1` |
| Access-log bucket | `arn:aws:s3:::anava-clinic-access-logs-363518210533-ap-south-1` |
| KMS key A (patient records + quarantine), alias `alias/anava-clinic-phi` | `arn:aws:kms:ap-south-1:363518210533:key/a677e7d1-3601-488b-8691-498ebe7d8d58` |
| KMS key B (compliance), alias `alias/anava-clinic-compliance` | `arn:aws:kms:ap-south-1:363518210533:key/8a23c7d6-b6b6-49d1-b3df-ba85c9331270` |
| GuardDuty detector | `a0d08ad56c02c35747459f134e08d6d8` |
| GuardDuty Malware Protection plan (quarantine bucket, tagging on) | `72d08ad5dfe9dfabe6d8` |
| GuardDuty scan role | `arn:aws:iam::363518210533:role/anava-guardduty-malware-scan` |

Settings on all four buckets: public access blocked, versioning on, deny non-TLS bucket policy, `project=anava` tag, bucket keys on.

- Patient records and quarantine: SSE-KMS with key A. Quarantine has a lifecycle rule expiring objects after 7 days.
- Compliance: SSE-KMS with key B. Object Lock enabled, **no default retention** (retention period is a legal decision, see below).
- Access logs: SSE-S3. Object Lock enabled, no default retention.

GuardDuty scans every object that lands in the quarantine bucket and sets the object tag `GuardDutyMalwareScanStatus` to `NO_THREATS_FOUND`, `THREATS_FOUND`, `UNSUPPORTED`, `ACCESS_DENIED` or `FAILED`. Verified with a clean file and the EICAR test string (about 60 seconds).

## What is NOT done (this is your work)

Today the service still runs with `FILE_STORAGE_MODE=local` and no `S3_BUCKET_NAME` (task definition revision 1, `config.py:101-103`). Uploads go to container disk and are lost on every deploy. Nothing above protects a file until this changes.

### 1. Config and runtime
- Add settings for the four bucket names, the region, and the two KMS key ARNs. Do not hardcode them.
- Task definition: set `FILE_STORAGE_MODE=s3` and the bucket names. Deploy through the existing GitHub Actions pipeline.
- `anava-ecs-task-role` currently allows `s3:DeleteObject`. Remove it. App role gets: on the patient records bucket `s3:PutObject`, `s3:GetObject`, `s3:GetObjectVersion`, `s3:ListBucket`; on quarantine `s3:PutObject` (the presigned POST is signed by this role); on compliance `s3:PutObject`, `s3:GetObject`, `s3:GetObjectVersion`, plus `s3:PutObjectRetention` if the app sets retention per object. KMS: `kms:GenerateDataKey`, `kms:Decrypt` on key A (and key B for compliance). **No delete anywhere.**
- Deletes happen only through a separate `anava-retention-purge` role (not used by the app day to day).

### 2. `s3.py` changes
- `build_key()` takes a bucket kind and region. Key shapes:
  - Patient records: `regions/{region_id}/clinics/{clinic_id}/patients/{patient_id}/{category}/{uuidv7}_{filename}` where category is `eeg`, `medical_history` or `patient_upload`.
  - Consent: `regions/{region_id}/clinics/{clinic_id}/consents/patients/{patient_id}/{template}_v{n}_{uuidv7}.pdf`
  - Clinic license: `regions/{region_id}/clinics/{clinic_id}/licenses/{license_type}/v{n}_{uuidv7}_{filename}`
  - Quarantine: `incoming/{upload_id}` (no patient or clinic identifiers in the key)
- Replace the 8-character uuid4 prefix (`s3.py:80`) with a full UUIDv7 (128 bit, time-ordered).
- Sanitize `filename` (strip path separators, limit length). Never trust the client's name or content type.
- `presign_upload` must return a presigned **POST** (`generate_presigned_post`) with `content-length-range` and an allowed content-type list. Presigned PUT cannot cap size. Target is the quarantine bucket.
- `presign_download` (`s3.py:101-107`): set `ResponseContentDisposition: attachment` so a browser never renders an uploaded HTML or SVG file in the portal session. Presign for a specific `VersionId`.
- Store the S3 `VersionId` next to the key in the database. Today only a SHA-256 checksum is stored. Add the column and a migration.
- Existing files in local mode are not migrated (confirm with the team whether any real data exists).

### 3. Append-only record behavior
- EEG and medical history are append-only. A correction is a new key plus a database link to the record it supersedes. Never write over an existing key.
- Clinic licenses: a renewal is a new `v{n}` key. Current license is the highest `n`, tracked in the database.
- Consent: a re-signature always creates a new object (template version and fresh UUID are in the key). Set Object Lock retention per object using a configurable number of days, not a constant in code. Use governance mode until counsel confirms the period, then compliance mode.

### 4. Patient upload flow (quarantine, scan, promote)
1. Backend creates an `upload_id` and a database row with status `scanning`, then returns the presigned POST for `incoming/{upload_id}`. The row records patient, clinic, region, category `patient_upload`, original filename and declared type.
2. Patient uploads directly to the quarantine bucket. The browser upload needs a **CORS rule on the quarantine bucket** (allow POST from the portal origins). That rule does not exist yet; add it.
3. GuardDuty tags the object. An EventBridge rule on the GuardDuty malware scan result event (source `aws.guardduty`, detail-type `GuardDuty Malware Protection Object Scan Result`) triggers the promotion job (Lambda or ECS task).
4. Promotion job:
   - `NO_THREATS_FOUND`: copy to the patient records bucket under the `patient_upload` key (SSE-KMS key A), record the new key and `VersionId`, set the row to `unverified`.
   - `THREATS_FOUND`: leave in quarantine, set the row to `rejected`, send an alert.
   - Any other status (`UNSUPPORTED`, `ACCESS_DENIED`, `FAILED`): treat as rejected and alert. Never promote on anything but `NO_THREATS_FOUND`.
   - Make it idempotent (events can repeat) and verify the object's tag itself before copying.
   - Job role needs `s3:GetObject`, `s3:GetObjectTagging` on quarantine; `s3:PutObject` on patient records; `kms:Decrypt`/`kms:GenerateDataKey` on key A. No delete on quarantine (the 7-day lifecycle cleans up).
5. API/UI: patient sees `scanning`, then `unverified` or `rejected`. A clinician opens the file and confirms it, which sets `verified`. Patients and clinicians must not be able to fetch a file that is not promoted.

### 5. Retention and erasure
- An erasure request inside the retention period restricts access and logs the decision. It does not delete. After the retention period ends with no legal hold, only the `anava-retention-purge` role deletes the patient prefix. Retention lengths are configuration, not code.

### 6. Infrastructure follow-ups (phase 2)
- CloudTrail data events for the patient records and compliance buckets, delivered to the access-log bucket. The access-log bucket needs a bucket policy statement allowing `cloudtrail.amazonaws.com` to write. Not set up yet.
- Retention period for consent Object Lock: **pending a decision from counsel.** Do not enable a default retention in compliance mode before then (it cannot be shortened, even by root).
- Lifecycle to Glacier Instant Retrieval for raw EEG traces: phase 3, check minimum object size first.

## Acceptance checks
- A new upload lands in quarantine, is tagged, promoted, and appears as `unverified`.
- An EICAR file ends as `rejected` and never appears in the patient records bucket.
- `aws s3api delete-object` with the app task role is denied.
- A presigned POST with an oversize file or disallowed content type is rejected by S3.
- A downloaded HTML file is served with `Content-Disposition: attachment`.
- Overwriting an existing key never happens in code paths for EEG, history, licenses or consent.
- Unit tests for `build_key()` (all four shapes, filename sanitizing) and for the promotion job's status handling.

## Reference
Design doc: https://claude.ai/artifact/5FMxkW4EhqpT6VocsjrTAB
