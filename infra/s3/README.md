# S3 document storage: AWS-side changes

The backend code for the new storage architecture (`BACKEND_S3_PROMPT.md`) is in
the repo. These are the AWS changes it needs, in the order to apply them.
Nothing here has been applied. Account `363518210533`, region `ap-south-1`.

Do steps 1 to 3 before deploying the new backend. Patient uploads stay stuck on
"scanning" until step 5 is done; staff uploads work after step 4.

## 1. Database

Apply migration `0062` (`SQL/v1/110_s3_document_storage.sql`). The new backend
writes the new columns on every upload, so this goes first.

```
cd backend
alembic upgrade head
```

## 2. CORS on the two buckets the browser uploads to

The browser sends a POST straight to S3. Patients post to the quarantine
bucket, staff post to the patient records bucket. Both need the rule.

`upload-cors.json` lists the staging portal and localhost. **Add the production
portal origin before applying.**

```
aws s3api put-bucket-cors --bucket anava-clinic-uploads-quarantine-363518210533-ap-south-1 --cors-configuration file://upload-cors.json
aws s3api put-bucket-cors --bucket anava-clinic-patient-files-prod-363518210533-ap-south-1 --cors-configuration file://upload-cors.json
```

## 3. API task role: remove delete

Replace the S3/KMS permissions on `anava-ecs-task-role` with
`app-task-role-policy.json`. It has no `s3:DeleteObject` anywhere. Remove the old
statement that allows it; adding this policy next to the old one is not enough.

Check:

```
aws s3api delete-object --bucket anava-clinic-patient-files-prod-363518210533-ap-south-1 --key any-key
```

run with the task role must return AccessDenied.

## 4. API task definition: switch to S3 mode

New revision of the API task definition with these environment variables, then
deploy through the GitHub Actions pipeline as usual.

```
FILE_STORAGE_MODE=s3
S3_BUCKET_NAME=anava-clinic-patient-files-prod-363518210533-ap-south-1
S3_QUARANTINE_BUCKET_NAME=anava-clinic-uploads-quarantine-363518210533-ap-south-1
S3_COMPLIANCE_BUCKET_NAME=anava-clinic-compliance-records-363518210533-ap-south-1
S3_ACCESS_LOGS_BUCKET_NAME=anava-clinic-access-logs-363518210533-ap-south-1
S3_KMS_KEY_ARN_PHI=arn:aws:kms:ap-south-1:363518210533:key/a677e7d1-3601-488b-8691-498ebe7d8d58
S3_KMS_KEY_ARN_COMPLIANCE=arn:aws:kms:ap-south-1:363518210533:key/8a23c7d6-b6b6-49d1-b3df-ba85c9331270
```

Optional, defaults shown: `UPLOAD_MAX_BYTES=26214400` (25 MB) and
`UPLOAD_ALLOWED_CONTENT_TYPES=["application/pdf","image/jpeg","image/png"]`.

Leave `CONSENT_OBJECT_LOCK_DAYS` unset until counsel decides the retention
period. `CONSENT_OBJECT_LOCK_MODE` stays `GOVERNANCE` until then.

## 5. Promotion job

The job that moves a scanned patient upload out of quarantine. Same Docker image
as the API, different command and a different IAM role.

1. Create role `anava-upload-promoter` (trusted by `ecs-tasks.amazonaws.com`)
   with `upload-promoter-role-policy.json`.
2. Register a task definition `anava-upload-promoter`: copy the API task
   definition (same image, same database settings and secrets, same subnets and
   security group so it can reach RDS), set `taskRoleArn` to the new role, drop
   the port mapping, and set the storage variables from step 4.
3. Create an EventBridge rule with the pattern in `scan-result-rule.json` and
   an ECS RunTask target for that task definition. Input transformer:

   Input path: `{"key": "$.detail.s3ObjectDetails.objectKey"}`

   Template (replace `CONTAINER_NAME` with the container name in the task
   definition):

   ```
   {"containerOverrides":[{"name":"CONTAINER_NAME","command":["python","-m","app.workers.upload_promoter","<key>"]}]}
   ```
4. Catch-up run: an EventBridge schedule every 10 minutes that runs the same
   task with command `["python","-m","app.workers.upload_promoter"]`. It
   picks up any upload whose event was lost and gives up on uploads that never
   arrived.

## 6. Alert on rejected uploads

A rejected upload writes a log line with event `patient_upload_rejected`.
Create a CloudWatch Logs metric filter on that text for the promotion job's log
group, and an alarm on it that notifies the team.

## Acceptance checks (from the prompt)

- Upload a clean PDF as a patient: the file shows `scanning`, then `unverified`
  after about a minute.
- Upload the EICAR test file: it ends `rejected` and nothing appears in the
  patient records bucket.
- `delete-object` with the API task role is denied (step 3).
- A file over 25 MB or of another type is refused by S3 itself.
- A downloaded file arrives with `Content-Disposition: attachment`.

## Not done here

- `anava-retention-purge` role and the purge of a patient prefix after
  retention ends.
- CloudTrail data events to the access-log bucket (phase 2).
- Glacier lifecycle for raw EEG (phase 3).
